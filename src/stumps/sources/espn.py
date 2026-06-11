"""ESPN open cricket API — free, keyless live data (no hs-consumer-api).

ESPNcricinfo's CDN serves a 403 challenge to standard Python TLS handshakes, so
this source uses ``curl_cffi`` to impersonate Chrome's TLS fingerprint (the same
trick the ``cricdata`` project uses). With that, ESPN's open API responds:

  - scoreboard header (`site.api.espn.com/.../scoreboard/header?sport=cricket`)
    → current/recent matches with scores, series (league), status, and a
    `class` block giving the exact format.
  - per-event summary (`.../sports/cricket/{league}/summary?event={id}`)
    → structured `linescores` (runs/wkts/overs/target) and `rosters` with full
    batting/bowling figures. Used to enrich the matches we display.

Field access is defensive; if ESPN changes shape we degrade rather than crash.
"""

from __future__ import annotations

import re
from typing import Any

from stumps import config
from stumps.models import (
    Ball, Batter, Bowler, FallOfWicket, Format, Innings, Match, OverScore,
    Partnership, Phase, Standings, StandingsRow, Team,
)
from stumps.sources.base import DataSource, DiskCache, SourceError

_SCOREBOARD = (
    "https://site.api.espn.com/apis/personalized/v2/scoreboard/header"
    "?sport=cricket&region=gb"
)
_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/cricket/{league}/summary?event={event}"
# Ball-by-ball commentary uses a fixed cricket league id (8676) regardless of the
# actual series, and paginates oldest-first (25 balls/page).
_PLAYBYPLAY = "https://site.web.api.espn.com/apis/site/v2/sports/cricket/8676/playbyplay?event={event}&page={page}"

# internationalClassId -> Format (the reliable signal for internationals).
_INTL_CLASS = {1: Format.TEST, 2: Format.ODI, 3: Format.T20I,
               10: Format.WT20I, 11: Format.WODI, 12: Format.WTEST}

# Roster `dismissalCard` abbreviation -> readable how-out mode.
_DISMISSAL_CARDS = {
    "c": "caught", "b": "bowled", "lbw": "lbw", "run out": "run out",
    "st": "stumped", "c & b": "caught & bowled", "hit wicket": "hit wicket",
}


def _expand_card(card: Any, fielder_keeper: int) -> str:
    """Readable how-out from the roster `dismissalCard` abbreviation; '' when the
    feed gives nothing (not-out is handled separately via the `outs` stat)."""
    text = str(card or "").strip().lower()
    if not text or text in ("0", "not out"):
        return ""
    text = _DISMISSAL_CARDS.get(text, text)
    if text == "caught" and fielder_keeper:
        text = "caught wk"
    return text


def _to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_score(score: str) -> tuple[int, int, float, int, bool, bool]:
    """Parse an ESPN score string into (runs, wkts, overs, target, declared,
    all_out). Handles forms like '421', '174/6 (49.2 ov)',
    '191/9 (42.2/50 ov, target 285)', '250/8d', and multi-innings '421 & 50/2'
    (the *current* segment, after the last '&', is used)."""
    if not score:
        return 0, 0, 0.0, 0, False, False
    segment = score.split("&")[-1].strip()

    target = 0
    if "target" in segment:
        tail = segment.split("target", 1)[1]
        target = _to_int("".join(c for c in tail if c.isdigit()))

    overs = 0.0
    if "(" in segment:
        inside = segment[segment.find("(") + 1 : segment.find(")") if ")" in segment else len(segment)]
        head = inside.split("ov")[0].strip()  # "42.2/50" or "49.2"
        overs = _to_float(head.split("/")[0])
        segment = segment[: segment.find("(")].strip()

    declared = segment.endswith(("d", "dec"))
    core = segment.rstrip("dec ").strip()
    if "/" in core:
        runs_s, _, wkts_s = core.partition("/")
        return _to_int(runs_s), _to_int(wkts_s), overs, target, declared, False
    return _to_int(core), 10, overs, target, declared, True


class EspnSource(DataSource):
    name = "espncricinfo"

    def __init__(self, settings: config.Settings):
        super().__init__(settings)
        self.cache = DiskCache(settings)
        self._session = None

    def _get(self, url: str) -> dict:
        cached = self.cache.get(url)
        if cached is not None:
            return cached  # type: ignore[return-value]
        if self._session is None:
            try:
                from curl_cffi import requests as curl_requests
            except ImportError as exc:  # pragma: no cover
                raise SourceError("curl_cffi not installed (pip install curl_cffi)") from exc
            self._session = curl_requests.Session(impersonate="chrome")
        try:
            resp = self._session.get(url, timeout=self.settings.http_timeout_seconds)
            if resp.status_code != 200:
                raise SourceError(f"ESPN returned HTTP {resp.status_code}")
            data = resp.json()
        except SourceError:
            raise
        except Exception as exc:  # curl_cffi / JSON errors
            raise SourceError(f"ESPN request failed: {exc}") from exc
        self.cache.set(url, data)
        return data

    # -- current matches ----------------------------------------------------

    def fetch_current_matches(self) -> list[Match]:
        data = self._get(_SCOREBOARD)
        if not (data.get("sports") or []):
            raise SourceError("ESPN scoreboard returned no sports (shape changed?)")
        matches = self._parse_scoreboard(data)
        if not matches:
            raise SourceError("ESPN scoreboard listed no matches")
        return matches

    def _parse_scoreboard(self, data: dict) -> list[Match]:
        matches: list[Match] = []
        for sport in data.get("sports") or []:
            for league in sport.get("leagues") or []:
                league_id = str(league.get("id") or "")
                series_name = league.get("name") or ""
                for event in league.get("events") or []:
                    matches.append(
                        self._event_to_match(event, league_id, series_name))
        return matches

    def fetch_recent_results(self, days: int) -> list[Match]:
        """Finished matches from the header for each of the last ``days`` dates.

        The header accepts `&dates=YYYYMMDD` (one date only — no ranges), so we
        make one cached call per day. A match appearing on several days keeps the
        most recent date as its `finished_on` (we walk oldest → newest)."""
        if days <= 0:
            return []
        from datetime import date, timedelta

        today = date.today()
        recent: dict[str, Match] = {}
        for delta in range(days, 0, -1):  # oldest first
            day = today - timedelta(days=delta)
            try:
                data = self._get(f"{_SCOREBOARD}&dates={day:%Y%m%d}")
            except SourceError:
                continue
            for match in self._parse_scoreboard(data):
                if match.phase is Phase.COMPLETE:
                    match.finished_on = day.isoformat()
                    recent[match.match_id] = match
        return list(recent.values())

    def fetch_upcoming(self, days: int) -> list[Match]:
        """Scheduled matches over the next ``days`` days (one cached header call
        per day). Multi-day games span dates, so keep the earliest sighting."""
        if days <= 0:
            return []
        from datetime import date, timedelta

        today = date.today()
        upcoming: dict[str, Match] = {}
        for delta in range(1, days + 1):
            day = today + timedelta(days=delta)
            try:
                data = self._get(f"{_SCOREBOARD}&dates={day:%Y%m%d}")
            except SourceError:
                continue
            for match in self._parse_scoreboard(data):
                if match.phase is Phase.UPCOMING:
                    upcoming.setdefault(match.match_id, match)
        return list(upcoming.values())

    def _event_to_match(self, event: dict, league_id: str, series_name: str) -> Match:
        cls = event.get("class") or {}
        fmt = self._format(cls, event.get("eventType", ""))
        phase = self._phase(event)
        status = event.get("summary") or _dig(event, "fullStatus", "type", "detail") or ""
        teams, innings = self._teams_and_innings(event.get("competitors") or [])

        match = Match(
            match_id=str(event.get("id") or ""),
            format=fmt,
            teams=teams,
            phase=phase,
            series_id=league_id or None,  # ESPN league id, needed to enrich
            series_name=series_name,
            status_text=status,
            venue=_dig(event, "location") or "",
            innings=innings,
            source=self.name,
        )
        if phase is Phase.COMPLETE:
            # ESPN's scoreboard `summary`/`detail` for a finished game are often
            # the bare label "Result" or "Final"; the human-readable outcome
            # ("X won by N runs", "Match drawn", "Match tied") lives in
            # fullStatus.type.detail when present. Prefer whichever is descriptive
            # and let render.console._synth_result reconstruct one otherwise.
            generic = ("", "result", "final")
            detail = _dig(event, "fullStatus", "type", "detail") or ""
            result = detail if detail.strip().lower() not in generic else status
            match.result_text = result
            match.status_text = result
            # The authoritative win/draw signal: each competitor carries a
            # `winner` boolean. No winner on a finished game -> drawn / tied.
            match.winner = self._winner_name(event.get("competitors") or [])
        match.day_number, match.total_days = _parse_day(status)
        match.starts_at = event.get("date") or ""
        match.ball_by_ball_available = bool(event.get("playByPlayAvailable"))
        return match

    def _format(self, cls: dict, event_type: str) -> Format:
        icid = _to_int(cls.get("internationalClassId"))
        if icid in _INTL_CLASS:
            return _INTL_CLASS[icid]
        card = (cls.get("generalClassCard") or "").lower()
        if "first-class" in card:
            return Format.FIRST_CLASS
        if "list a" in card:
            return Format.LIST_A
        if "women" in card and ("t20" in card or "twenty20" in card):
            return Format.T20  # domestic women's T20 (no domestic-women format)
        if "twenty20" in card or "t20" in card:
            return Format.T20
        if "odi" in card or "one-day" in card:
            return Format.LIST_A
        et = (event_type or "").upper()
        return {"TEST": Format.FIRST_CLASS, "ODI": Format.LIST_A,
                "T20": Format.T20}.get(et, Format.OTHER)

    def _phase(self, event: dict) -> Phase:
        state = (_dig(event, "fullStatus", "type", "state") or _dig(event, "status") or "").lower()
        detail = (
            (event.get("summary") or "")
            + " "
            + (_dig(event, "fullStatus", "type", "detail") or "")
        ).lower()
        if "stump" in detail:
            return Phase.STUMPS
        if any(w in detail for w in ("lunch", "tea", "drinks", "innings break", "rain", "bad light")):
            return Phase.BREAK
        if "abandon" in detail:
            return Phase.ABANDONED
        if state == "in":
            return Phase.LIVE
        if state == "post":
            return Phase.COMPLETE
        if state == "pre":
            return Phase.UPCOMING
        return Phase.UNKNOWN

    def _teams_and_innings(self, competitors: list[dict]) -> tuple[list[Team], list[Innings]]:
        teams, innings = [], []
        for c in competitors:
            t = c.get("team") or {}
            name = t.get("displayName") or t.get("name") or c.get("displayName") or "?"
            teams.append(Team(
                name=name,
                short_name=t.get("abbreviation") or name[:3].upper(),
                object_id=str(t.get("id") or "") or None,
            ))
            score = c.get("score")
            if score:
                runs, wkts, overs, target, declared, all_out = parse_score(str(score))
                innings.append(Innings(
                    batting_team=name, runs=runs, wickets=wkts, overs=overs,
                    target=target or None, declared=declared, all_out=all_out,
                ))
        return teams, innings

    # -- enrichment (structured innings + figures) --------------------------

    def enrich(self, match: Match) -> Match:
        if not match.series_id:
            return match
        try:
            data = self._get(_SUMMARY.format(league=match.series_id, event=match.match_id))
        except SourceError:
            return match
        innings = self._innings_from_summary(data)
        if innings:
            match.innings = innings
        self._apply_points(match, data)
        self._apply_standings(match, data)
        self._apply_match_info(match, data)
        if match.format.is_multi_day:
            self._apply_multiday_timing(match, data)
        if match.ball_by_ball_available and match.phase.is_active_today:
            match.recent_balls = self._recent_balls(match.match_id)
        return match

    @staticmethod
    def _apply_points(match: Match, data: dict) -> None:
        """League/tournament points awarded, from the summary `notes` (type
        `points`, e.g. "Surrey 15, Hampshire 13"). Present for any points-based
        competition — county championship, the various first-class leagues,
        round-robin limited-overs — and absent for bilateral series."""
        for note in data.get("notes") or []:
            if note.get("type") == "points":
                text = (note.get("text") or "").replace("*", "").strip()
                if text:
                    match.points = text
                return

    @staticmethod
    def _apply_standings(match: Match, data: dict) -> None:
        """Parse the league/division table from the summary `standings` block:
        `children[].standings.entries[]`, each an entry with a `team` and a list
        of named `stats` (rank, matchesPlayed, matchesWon/Lost/Draw, matchPoints).
        Entries arrive pre-ranked. Generic across competitions and formats."""
        block = data.get("standings") or {}
        children = block.get("children") or []
        entries: list[dict] = []
        for child in children:
            entries = (child.get("standings") or {}).get("entries") or []
            if entries:
                break  # the event's own group (one per division in practice)
        if not entries:
            return

        rows: list[StandingsRow] = []
        for entry in entries:
            stats = {s.get("name"): s.get("value") for s in entry.get("stats") or []}
            team = _dig(entry, "team", "displayName") or _dig(entry, "team", "abbreviation") or "?"
            qualified = str(stats.get("qualified") or "").upper() in ("Y", "YES", "TRUE", "1")
            rows.append(StandingsRow(
                rank=_to_int(stats.get("rank")),
                team=team,
                played=_to_int(stats.get("matchesPlayed")),
                won=_to_int(stats.get("matchesWon")),
                lost=_to_int(stats.get("matchesLost")),
                drawn=_to_int(stats.get("matchesDraw")),
                points=_to_int(stats.get("matchPoints")),
                nrr=_to_float(stats["netrr"]) if "netrr" in stats else None,
                qualified=qualified,
            ))
        match.standings = Standings(name=block.get("name") or match.series_name,
                                    rows=rows)

    @staticmethod
    def _apply_match_info(match: Match, data: dict) -> None:
        """Toss (from `notes`) and match officials (from `gameInfo`)."""
        for note in data.get("notes") or []:
            if note.get("type") == "toss":
                toss = (note.get("text") or "").replace(" ,", ",").strip()
                if toss:
                    match.toss = toss
                break
        officials = [o.get("displayName") for o in _dig(data, "gameInfo", "officials") or []
                     if o.get("displayName")]
        if officials:
            match.officials = officials

    @staticmethod
    def _partnerships(ls: dict) -> list[Partnership]:
        """Partnerships for one innings, from its linescore `partnerships` block
        (present for *every* innings, unlike the `matchcards` card)."""
        out = []
        for p in ls.get("partnerships") or []:
            batsmen = p.get("batsmen") or []

            def field(i: int, key: str):
                return batsmen[i].get(key) if i < len(batsmen) else None

            out.append(Partnership(
                wicket=p.get("wicketName") or "",
                runs=_to_int(p.get("runs")),
                overs=str(p.get("overs") or ""),
                batter1=_dig(batsmen[0], "athlete", "displayName") or "" if batsmen else "",
                batter2=_dig(batsmen[1], "athlete", "displayName") or "" if len(batsmen) > 1 else "",
                runs1=_to_int(field(0, "runs")),
                runs2=_to_int(field(1, "runs")),
            ))
        return out

    @staticmethod
    def _over_scores(ls: dict) -> list[OverScore]:
        """Runs/wickets per over, from the linescore `statistics.overs` block
        (a list of {number, runs, wicket[...]}), in over order."""
        overs = (ls.get("statistics") or {}).get("overs") or []
        rows = overs[0] if overs and isinstance(overs[0], list) else []
        return [OverScore(runs=_to_int(o.get("runs")),
                          wickets=len(o.get("wicket") or [])) for o in rows]

    @staticmethod
    def _fall_of_wickets(ls: dict) -> list[FallOfWicket]:
        """Fall of wickets for one innings, from its linescore `fow` block —
        populated for every innings of every match type (incl. county)."""
        out = [FallOfWicket(
            wicket=_to_int(f.get("wicketNumber")),
            team_runs=_to_int(f.get("runs")),
            over=str(f.get("wicketOver") or ""),
            batter=_dig(f, "athlete", "displayName") or "",
        ) for f in ls.get("fow") or []]
        out.sort(key=lambda w: w.wicket)
        return out

    @staticmethod
    def _winner_name(competitors: list[dict]) -> str:
        """The name of the competitor flagged `winner`, or '' (drawn/tied/none)."""
        for c in competitors:
            if c.get("winner"):
                t = c.get("team") or {}
                return (t.get("displayName") or t.get("name")
                        or c.get("displayName") or c.get("name") or "")
        return ""

    @staticmethod
    def _apply_multiday_timing(match: Match, data: dict) -> None:
        """Fill the multi-day timing context the win/draw estimate needs from the
        summary `notes` block (which the scoreboard list lacks): the scheduled
        close, total days, current day, and the present local time. All defensive
        — anything missing just stays as-is."""
        comp = (_dig(data, "header", "competitions") or [{}])[0]
        status = comp.get("status") or {}
        by_type: dict[str, list[dict]] = {}
        for note in data.get("notes") or []:
            by_type.setdefault(note.get("type") or "", []).append(note)

        local = status.get("presentLocalTime")
        if local:
            match.local_time = str(local)

        hours = (by_type.get("hoursofplay") or [{}])[0].get("text") or ""
        m = re.search(r"[Cc]lose\s+(\d{1,2})[.:](\d{2})", hours)
        if m:
            match.close_time = f"{int(m.group(1)):02d}:{m.group(2)}"

        days = (by_type.get("matchdays") or [{}])[0].get("text") or ""
        m = re.search(r"\((\d+)\s*-?\s*day", days)
        if m:
            match.total_days = int(m.group(1))

        # One `closeofplay` note per completed day -> current day is the next one.
        completed = len(by_type.get("closeofplay") or [])
        if completed and match.total_days:
            match.day_number = min(match.total_days, completed + 1)

    def _recent_balls(self, event_id: str, limit: int = 10) -> list[Ball]:
        """Most recent deliveries (newest first). Commentary paginates
        oldest-first, so we read page 1 for the page count, then the last page."""
        try:
            first = self._get(_PLAYBYPLAY.format(event=event_id, page=1))
        except SourceError:
            return []
        commentary = first.get("commentary") or {}
        page_count = _to_int(commentary.get("pageCount"), 1)
        items = commentary.get("items") or []
        if page_count > 1:
            try:
                last = self._get(_PLAYBYPLAY.format(event=event_id, page=page_count))
                items = (last.get("commentary") or {}).get("items") or items
            except SourceError:
                pass
        balls = [self._ball(it) for it in items if it.get("over")]
        return list(reversed(balls))[:limit]  # newest first

    @staticmethod
    def _ball(item: dict) -> Ball:
        over = item.get("over") or {}
        over_str = f"{_to_int(over.get('number'))}.{_to_int(over.get('ball'))}"
        runs = _to_int(item.get("scoreValue"))
        play = (_dig(item, "playType", "description") or "").lower()
        is_wicket = bool(item.get("dismissal")) or "out" in play or "wicket" in play
        is_boundary = runs in (4, 6) and "bye" not in play
        return Ball(
            over=over_str,
            description=item.get("shortText") or item.get("text") or "",
            runs=runs,
            is_wicket=is_wicket,
            is_boundary=is_boundary,
            period=_to_int(item.get("period"), 1),
        )

    def _innings_from_summary(self, data: dict) -> list[Innings]:
        comp = (_dig(data, "header", "competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        rosters = data.get("rosters") or []

        # Gather (period -> batting competitor + its linescore).
        by_period: dict[int, tuple[dict, dict]] = {}
        for c in competitors:
            for ls in c.get("linescores") or []:
                period = _to_int(ls.get("period"))
                stats = ls.get("statistics") or {}
                has_runs = _to_int(ls.get("runs")) > 0 or bool(stats.get("categories"))
                # The batting side for a period is the competitor whose linescore
                # actually carries runs/stats (the other is the fielding side).
                if period and (period not in by_period or has_runs):
                    if period not in by_period or _to_int(ls.get("runs")) >= _to_int(by_period[period][1].get("runs")):
                        by_period[period] = (c, ls)

        dismissals = self._dismissals(data)
        innings = []
        for period in sorted(by_period):
            comp_c, ls = by_period[period]
            team_name = _dig(comp_c, "team", "displayName") or "?"
            wkts = _to_int(ls.get("wickets"))
            score_str = str(comp_c.get("score") or "")
            _, _, _, score_target, _, _ = parse_score(score_str)
            target = _to_int(ls.get("target")) or score_target or None
            is_current = _to_int(ls.get("isCurrent")) == 1
            inns = Innings(
                batting_team=team_name,
                number=period,
                runs=_to_int(ls.get("runs")),
                wickets=wkts,
                overs=_to_float(ls.get("overs")),
                target=target,
                all_out=wkts >= 10,
                closed=not is_current,
                batters=self._batters(rosters, team_name, period, dismissals),
                bowlers=self._bowlers(rosters, team_name, period),
                partnerships=self._partnerships(ls),
                fall_of_wickets=self._fall_of_wickets(ls),
                over_scores=self._over_scores(ls),
            )
            innings.append(inns)
        return innings

    @staticmethod
    def _period_stats(entry: dict, period: int) -> dict[str, Any]:
        """Flatten a roster entry's stats for a given innings period to {name: value}."""
        for ls in entry.get("linescores") or []:
            if _to_int(ls.get("period")) != period:
                continue
            for inner in ls.get("linescores") or []:
                cats = _dig(inner, "statistics", "categories") or []
                for cat in cats:
                    flat = {s.get("name"): s.get("value") for s in cat.get("stats") or []}
                    if flat:
                        return flat
        return {}

    def _batters(self, rosters: list[dict], team_name: str, period: int,
                 dismissals: dict[tuple[int, str], str]) -> list[Batter]:
        roster = _find_roster(rosters, team_name)
        ranked: list[tuple[int, Batter]] = []
        for entry in roster:
            st = self._period_stats(entry, period)
            if not st or "ballsFaced" not in st:
                continue
            if _to_int(st.get("batted")) != 1 and _to_int(st.get("ballsFaced")) == 0:
                continue
            pid = str(_dig(entry, "athlete", "id") or "")
            # How-out: the `matchcards` scorecard has full text but only for the
            # latest innings, so fall back to the roster `dismissalCard` (present
            # every innings), then to a bare "out" for a dismissed batter.
            how = dismissals.get((period, pid)) or _expand_card(
                st.get("dismissalCard"), _to_int(st.get("fielderKeeper")))
            # matchcards may report "not out" — that's a not-out signal, not a
            # how-out — so clear it. A real how-out also means dismissed, in case
            # the `outs` stat is missing.
            how = "" if how == "not out" else how
            dismissed = _to_int(st.get("outs")) > 0 or bool(how)
            batter = Batter(
                name=_dig(entry, "athlete", "displayName") or "?",
                runs=_to_int(st.get("runs")),
                balls=_to_int(st.get("ballsFaced")),
                fours=_to_int(st.get("fours")),
                sixes=_to_int(st.get("sixes")),
                not_out=not dismissed,
                dismissal=(how or "out") if dismissed else None,
                on_strike=bool(entry.get("active")),
                captain=bool(entry.get("captain")),
            )
            ranked.append((_to_int(st.get("battingPosition")) or 99, batter))
        # Real batting order, not not-out-first.
        ranked.sort(key=lambda t: t[0])
        return [b for _, b in ranked]

    def _bowlers(self, rosters: list[dict], batting_team: str, period: int) -> list[Bowler]:
        # Bowlers come from the team that ISN'T batting this innings.
        out = []
        for roster in rosters:
            if (_dig(roster, "team", "displayName") or "").lower() == batting_team.lower():
                continue
            for entry in roster.get("roster") or []:
                st = self._period_stats(entry, period)
                if not st or "overs" not in st:
                    continue
                if _to_float(st.get("overs")) == 0 and _to_int(st.get("balls")) == 0:
                    continue
                out.append(Bowler(
                    name=_dig(entry, "athlete", "displayName") or "?",
                    overs=_to_float(st.get("overs")),
                    maidens=_to_int(st.get("maidens")),
                    runs=_to_int(st.get("conceded")),
                    wickets=_to_int(st.get("wickets")),
                    bowling_now=bool(entry.get("active")),
                ))
        return out

    @staticmethod
    def _dismissals(data: dict) -> dict[tuple[int, str], str]:
        """(innings number, player id) -> how-out text from the `matchcards`
        scorecard ("caught", "bowled", "lbw", "caught wk", "not out", …)."""
        out: dict[tuple[int, str], str] = {}
        for card in data.get("matchcards") or []:
            if (card.get("headline") or "").lower() != "batting":
                continue
            inns = _to_int(card.get("inningsNumber"))
            for p in card.get("playerDetails") or []:
                pid = str(p.get("playerID") or "")
                text = (p.get("dismissal") or "").strip()
                if pid and text:
                    out[(inns, pid)] = text
        return out


def _find_roster(rosters: list[dict], team_name: str) -> list[dict]:
    for roster in rosters:
        if (_dig(roster, "team", "displayName") or "").lower() == team_name.lower():
            return roster.get("roster") or []
    return []


def _parse_day(status: str) -> tuple[int | None, int | None]:
    """Pull 'Day 2' (and total days for Tests = 5, first-class = 4) from status."""
    low = status.lower()
    if "day" not in low:
        return None, None
    import re

    m = re.search(r"day\s*(\d)", low)
    if not m:
        return None, None
    return int(m.group(1)), None


def _dig(obj: Any, *path: str) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur

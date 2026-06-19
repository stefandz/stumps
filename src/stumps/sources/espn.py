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

import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
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

    @staticmethod
    def _new_session():
        try:
            from curl_cffi import requests as curl_requests
        except ImportError as exc:  # pragma: no cover
            raise SourceError("curl_cffi not installed (pip install curl_cffi)") from exc
        return curl_requests.Session(impersonate="chrome")

    def _get(self, url: str, ttl: int | None = None, session=None) -> dict:
        """Fetch a URL → JSON, cached. ``ttl`` overrides the cache freshness
        window (e.g. a long TTL for immutable commentary pages). ``session`` lets
        a concurrent caller pass its own curl_cffi session, since a single shared
        session is not safe to use from multiple threads at once."""
        cached = self.cache.get(url, ttl)
        if cached is not None:
            return cached  # type: ignore[return-value]
        if session is None:
            if self._session is None:
                self._session = self._new_session()
            session = self._session
        try:
            resp = session.get(url, timeout=self.settings.http_timeout_seconds)
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

    #: How many months to walk outward from the current one when hunting a
    #: team's last result / next fixture (≈ a year either way covers any gap
    #: between a side's matches; in season the current month usually has both).
    _LAST_NEXT_MONTH_CAP = 13

    def fetch_team_last_next(self, object_id: str) -> list[Match]:
        """A team's most-recent finished match and next scheduled one.

        The scoreboard header accepts `&team={id}&dates=YYYYMM`, returning *all*
        of that team's matches in a month (past and future) in one cached call —
        so this is one call in season, walking to adjacent months only when the
        current one has no result / no fixture. Unbounded by the broad
        results/upcoming day-window."""
        if not object_id:
            return []
        from datetime import date

        today = date.today()
        last = self._scan_months(object_id, today, forward=False)
        nxt = self._scan_months(object_id, today, forward=True)
        return [m for m in (last, nxt) if m is not None]

    def _months_from(self, today, forward: bool):
        """(year, month) pairs starting at the current month, stepping one month
        in `forward`'s direction, up to the cap."""
        year, month = today.year, today.month
        step = 1 if forward else -1
        for _ in range(self._LAST_NEXT_MONTH_CAP):
            yield year, month
            month += step
            if month > 12:
                month, year = 1, year + 1
            elif month < 1:
                month, year = 12, year - 1

    def _scan_fetch(self, url: str, attempts: int = 3) -> dict:
        """`_get` with a few quick retries to ride out ESPN's intermittent 5xx /
        timeout flakiness on the month-scoreboard calls. Raises the final
        `SourceError` if every attempt fails."""
        for attempt in range(1, attempts + 1):
            try:
                return self._get(url)
            except SourceError:
                if attempt == attempts:
                    raise
                time.sleep(0.2 * attempt)
        raise AssertionError("unreachable")  # pragma: no cover

    def _scan_months(self, object_id: str, today, forward: bool) -> Match | None:
        """Walk months from the current one outward and return the closest
        upcoming fixture (`forward`) or most-recent finished result, or None.

        A month whose request keeps failing (ESPN 504s intermittently) *aborts*
        the walk rather than being skipped: skipping a failed month would let a
        much older match surface as the team's "latest" result — the current
        month's call failing is exactly when an older month must not be trusted.
        We retry to ride out transient failures, then give up (no bookend this
        run) in preference to reporting something stale. A month that responds
        with no relevant match is genuinely empty, so the walk continues past
        it as before."""
        iso_today = today.isoformat()
        for year, month in self._months_from(today, forward):
            try:
                data = self._scan_fetch(f"{_SCOREBOARD}&team={object_id}&dates={year}{month:02d}")
            except SourceError:
                break  # can't see this month -> better no result than a stale one
            best: Match | None = None
            for match in self._parse_scoreboard(data):
                day = (match.starts_at or "")[:10]
                if not day:
                    continue
                if forward:
                    if match.phase is Phase.UPCOMING and day >= iso_today:
                        if best is None or day < (best.starts_at or "")[:10]:
                            best = match
                elif match.phase is Phase.COMPLETE and day <= iso_today:
                    match.finished_on = day
                    if best is None or day > (best.finished_on or ""):
                        best = match
            if best is not None:
                return best  # closest month wins; we walk outward
        return None

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
            generic = ("", "result", "final", "live")
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
        # A decided result is authoritative even when ESPN's `state` lags: the
        # scoreboard keeps state="in" (and detail/summary "Live") for a window
        # after a match ends, so a finished game would otherwise show as live.
        # A competitor's `winner` boolean settles it -> COMPLETE, overriding both
        # the stale "in" and any break/stumps keyword still left in the detail
        # text. (Draws/ties carry no winner but reach state="post" cleanly, so
        # the state check below catches them.)
        if state == "post" or any(c.get("winner") for c in event.get("competitors") or []):
            return Phase.COMPLETE
        if "stump" in detail:
            return Phase.STUMPS
        if any(w in detail for w in ("lunch", "tea", "drinks", "innings break", "rain", "bad light")):
            return Phase.BREAK
        if "abandon" in detail:
            return Phase.ABANDONED
        if state == "in":
            return Phase.LIVE
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
                short_name=t.get("abbreviation") or c.get("abbreviation") or name[:3].upper(),
                # The per-event summary nests the id under `team`; the scoreboard
                # header (and team-/month-scoped queries) put it on the competitor.
                object_id=str(t.get("id") or c.get("id") or "") or None,
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
        if match.phase.is_active_today:
            self._backfill_current_manhattan(match)
        return match

    def _backfill_current_manhattan(self, match: Match) -> None:
        """Rebuild the current innings' over-by-over manhattan from commentary
        when ESPN's summary couldn't supply it. ESPN duplicates a side's *first*-
        innings over data into its *second* innings (the 3rd/4th innings of the
        match), so `_over_scores` drops it as the wrong innings and leaves the
        current-innings sparkline blank — only a team batting twice is affected,
        so the rebuild is gated on innings ≥ 3 and never costs a request earlier.

        Not gated on `ball_by_ball_available`: ESPN's `playByPlayAvailable` flag
        is an unreliable false-negative on some matches that *do* serve full
        commentary, so we try regardless and degrade silently to a blank
        manhattan (one wasted page fetch) when commentary genuinely isn't there."""
        cur = match.current_innings
        if cur is None or cur.number < 3 or cur.over_scores or cur.overs <= 0:
            return
        rebuilt = self._commentary_over_scores(match.match_id, cur.number, cur.overs)
        if rebuilt:
            cur.over_scores = rebuilt

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
    def _infer_partnership_batters(inns: Innings) -> None:
        """ESPN's `partnerships` block carries the stand's runs/overs but often
        leaves `batsmen` as empty `athlete: {}` objects (no names, no split).
        We can't recover the per-batter contribution, but we *can* recover who
        was batting: the openers start the innings, and at each wicket the
        batter named in the fall-of-wickets is replaced by the next one in the
        batting order. Fills `batter1`/`batter2` in place; leaves `runs1`/`runs2`
        at 0 so the renderer still uses the no-bar fallback.

        Best-effort: skipped if the feed already named the batters, and bails out
        (leaving later stands blank rather than guessing) if a FoW name doesn't
        match anyone at the crease — e.g. a retirement breaks the simple chain."""
        ps = inns.partnerships
        if not ps or any(p.batter1 or p.batter2 for p in ps):
            return
        order = [b.name for b in inns.batters if b.name]
        if len(order) < 2:
            return
        out_at = {w.wicket: w.batter for w in inns.fall_of_wickets}
        crease = [order[0], order[1]]
        next_in = 2
        # Partnerships arrive in wicket order (1st, 2nd, …); the last one in a
        # live innings is the current unbroken stand (no matching fall).
        for wkt, p in enumerate(ps, start=1):
            p.batter1, p.batter2 = crease[0], crease[1]
            out_name = out_at.get(wkt)
            if not out_name:
                break  # current partnership — nothing fell to end it
            if out_name not in crease:
                # Chain broke (retirement, name mismatch); don't guess the rest.
                for later in ps[wkt:]:
                    later.batter1 = later.batter2 = ""
                break
            crease[crease.index(out_name)] = order[next_in] if next_in < len(order) else ""
            next_in += 1

    @staticmethod
    def _over_scores(ls: dict) -> list[OverScore]:
        """Runs/wickets per over, from the linescore `statistics.overs` block
        (a list of {number, runs, wicket[...]}), in over order."""
        overs = (ls.get("statistics") or {}).get("overs") or []
        rows = overs[0] if overs and isinstance(overs[0], list) else []
        # ESPN sometimes duplicates a side's *earlier*-innings manhattan into a
        # later innings' linescore (a team batting twice gets innings 1's 97 over
        # rows planted in its 2.2-over third innings). The row count must track the
        # overs actually bowled, so when it wildly exceeds them the block belongs to
        # another innings — drop it rather than draw a manhattan for the wrong one.
        overs_played = _to_float(ls.get("overs"))
        if overs_played and len(rows) > int(overs_played) + 2:
            return []
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
        m = re.search(r"(\d{1,2})[.:](\d{2})\s+start", hours)
        if m:
            match.start_time = f"{int(m.group(1)):02d}:{m.group(2)}"

        days = (by_type.get("matchdays") or [{}])[0].get("text") or ""
        dates = _parse_match_dates(days)
        m = re.search(r"\((\d+)\s*-?\s*day", days)
        if m:
            match.total_days = int(m.group(1))
        elif dates:
            match.total_days = len(dates)

        # Current day. The `matchdays` note lists every scheduled date, so match
        # today against it — this is accurate from day one (unlike counting
        # `closeofplay` notes, which can't see day 1 because none exist yet).
        # Fall back to that count (one note per completed day) when the dates
        # can't be parsed.
        day_number = None
        if dates:
            today = _venue_date(match.local_time)
            if today in dates:
                day_number = dates.index(today) + 1
            elif today < dates[0]:
                day_number = 1
            else:
                day_number = len(dates)
        elif match.total_days:
            completed = len(by_type.get("closeofplay") or [])
            day_number = min(match.total_days, completed + 1)
        if day_number is not None:
            match.day_number = day_number

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

    #: How far back the commentary-rebuilt manhattan reaches, in overs. The fetch
    #: is bounded by this: a longer innings shows only its most recent overs (its
    #: tail), which keeps the worst-case load time predictable. 150 overs covers
    #: any realistic third/fourth innings.
    _MANHATTAN_MAX_OVERS = 150
    #: Concurrency for the tail-page fetch. ESPN tolerates a burst of this size
    #: comfortably; it bounds the wall-clock to roughly one round-trip per worker.
    _COMMENTARY_WORKERS = 6

    def _commentary_over_scores(
        self, event_id: str, period: int, overs: float
    ) -> list[OverScore]:
        """Rebuild one innings' over-by-over manhattan from ball-by-ball
        commentary, for when ESPN's summary `statistics.overs` carries the wrong
        innings' copy. Commentary paginates oldest-first at 25 balls/page, so the
        innings lives in the *tail* pages; we fetch only those (concurrently,
        capped at `_MANHATTAN_MAX_OVERS`) and collapse this `period`'s balls by
        over number — each ball carries its over's running runs/wickets total."""
        try:
            first = self._get(_PLAYBYPLAY.format(event=event_id, page=1))
        except SourceError:
            return []
        com = first.get("commentary") or {}
        page_size = _to_int(com.get("pageSize"), 25) or 25
        last_page = _to_int(com.get("pageCount"), 1)

        capped = min(overs, self._MANHATTAN_MAX_OVERS)
        balls = int(math.ceil(capped)) * 6 + page_size  # + a page of slack
        span = max(1, math.ceil(balls / page_size))

        # The cached page-1 `pageCount` can lag the live match by a page or two,
        # which would make us fetch a stale tail window and miss the latest overs.
        # The freshly-fetched tail pages each report the *live* pageCount, so trust
        # that and top up any newer pages it reveals (at most a round or two).
        pages: dict[int, dict] = {}
        for _ in range(3):
            wanted = [p for p in range(max(1, last_page - span + 1), last_page + 1)
                      if p not in pages]
            if not wanted:
                break
            for page, page_com in self._fetch_commentary_pages(event_id, wanted, last_page):
                pages[page] = page_com
            live_last = max((_to_int(c.get("pageCount"), last_page) for c in pages.values()),
                            default=last_page)
            if live_last <= last_page:
                break
            last_page = live_last

        by_over: dict[int, OverScore] = {}
        for page_com in pages.values():
            for it in page_com.get("items") or []:
                if _to_int(it.get("period")) != period:
                    continue
                over = it.get("over") or {}
                n = _to_int(over.get("number"))
                if n:
                    by_over[n] = OverScore(runs=_to_int(over.get("runs")),
                                           wickets=_to_int(over.get("wickets")))
        return [by_over[n] for n in sorted(by_over)]

    def _fetch_commentary_pages(
        self, event_id: str, pages: list[int], last_page: int
    ) -> list[tuple[int, dict]]:
        """Fetch the given commentary pages concurrently, returning (page,
        commentary) pairs. Pages before the last are immutable — their 25 balls
        never change once bowled — so they cache for a week; only the live last
        page keeps the short default TTL. Each worker thread uses its own
        curl_cffi session, since one shared session isn't safe across threads."""
        week = 7 * 24 * 3600
        local = threading.local()

        def fetch(page: int) -> tuple[int, dict]:
            url = _PLAYBYPLAY.format(event=event_id, page=page)
            ttl = None if page >= last_page else week
            session = getattr(local, "session", None)
            if session is None:
                session = local.session = self._new_session()
            try:
                data = self._get(url, ttl=ttl, session=session)
            except SourceError:
                return page, {}
            return page, (data.get("commentary") or {})

        with ThreadPoolExecutor(max_workers=self._COMMENTARY_WORKERS) as ex:
            return list(ex.map(fetch, pages))

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
            self._infer_partnership_batters(inns)
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


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _parse_match_dates(text: str) -> list[date]:
    """Parse the scheduled play dates from a `matchdays` note, in order, e.g.
    '12,13,14,15 June 2026 (4-day match)' -> the four June dates. A run of day
    numbers is closed by the month that follows it (so cross-month spans like
    '31 May, 1,2 June 2026' work); the trailing year applies to all. Returns []
    if it can't be parsed (the rare year rollover is not handled)."""
    text = re.sub(r"\(.*?\)", "", text)
    ym = re.search(r"\d{4}", text)
    if not ym:
        return []
    year = int(ym.group())
    out: list[date] = []
    pending: list[int] = []
    for tok in re.findall(r"[A-Za-z]+|\d+", text[: ym.start()]):
        if tok.isdigit():
            pending.append(int(tok))
            continue
        month = _MONTHS.get(tok[:3].lower())
        if month:
            for day in pending:
                try:
                    out.append(date(year, month, day))
                except ValueError:
                    pass
            pending = []
    return out


def _venue_date(local_time: str) -> date:
    """The venue's *current* calendar date, anchored to the venue clock rather
    than the machine's timezone.

    We know UTC now (machine-timezone-independent, given a correct system clock)
    and the venue's wall-clock (the feed's ``presentLocalTime``, HH:MM). Their
    difference is the venue's UTC offset, which we normalise into the band every
    multi-day cricket venue lives in — UTC−5 (West Indies, the westernmost) to
    UTC+14 — then read the date off the venue's own clock. This removes the
    off-by-one a machine in a different timezone would otherwise hit around the
    venue's midnight. Falls back to the machine date when the clock is unknown."""
    parts = (local_time or "").strip().replace(".", ":").split(":")
    if len(parts) < 2:
        return date.today()
    try:
        venue_minutes = int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return date.today()
    if not (0 <= venue_minutes < 1440):
        return date.today()
    utc_now = datetime.now(timezone.utc)
    utc_minutes = utc_now.hour * 60 + utc_now.minute
    offset = venue_minutes - utc_minutes
    # Two offsets reproduce the same wall clock (±24h apart); pick the one inside
    # the cricket band (−5h, +19h] so the choice is unambiguous for any venue.
    offset = ((offset + 300) % 1440) - 300
    return (utc_now + timedelta(minutes=offset)).date()


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

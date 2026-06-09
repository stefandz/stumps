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

from typing import Any

from stumps import config
from stumps.models import Batter, Bowler, Format, Innings, Match, Phase, Team
from stumps.sources.base import DataSource, DiskCache, SourceError

_SCOREBOARD = (
    "https://site.api.espn.com/apis/personalized/v2/scoreboard/header"
    "?sport=cricket&region=gb"
)
_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/cricket/{league}/summary?event={event}"

# internationalClassId -> Format (the reliable signal for internationals).
_INTL_CLASS = {1: Format.TEST, 2: Format.ODI, 3: Format.T20I,
               10: Format.WT20I, 11: Format.WODI, 12: Format.WTEST}


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
        sports = data.get("sports") or []
        if not sports:
            raise SourceError("ESPN scoreboard returned no sports (shape changed?)")
        matches: list[Match] = []
        for league in sports[0].get("leagues") or []:
            league_id = str(league.get("id") or "")
            series_name = league.get("name") or ""
            for event in league.get("events") or []:
                matches.append(self._event_to_match(event, league_id, series_name))
        if not matches:
            raise SourceError("ESPN scoreboard listed no matches")
        return matches

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
            match.result_text = status
        match.day_number, match.total_days = _parse_day(status)
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
        return match

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
                batters=self._batters(rosters, team_name, period),
                bowlers=self._bowlers(rosters, team_name, period),
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

    def _batters(self, rosters: list[dict], team_name: str, period: int) -> list[Batter]:
        roster = _find_roster(rosters, team_name)
        out = []
        for entry in roster:
            st = self._period_stats(entry, period)
            if not st or "ballsFaced" not in st:
                continue
            if _to_int(st.get("batted")) != 1 and _to_int(st.get("ballsFaced")) == 0:
                continue
            name = _dig(entry, "athlete", "displayName") or "?"
            dismissal = _dig(self._batting_block(entry, period), "shortText")
            out.append(Batter(
                name=name,
                runs=_to_int(st.get("runs")),
                balls=_to_int(st.get("ballsFaced")),
                fours=_to_int(st.get("fours")),
                sixes=_to_int(st.get("sixes")),
                not_out=_to_int(st.get("outs")) == 0,
                dismissal=dismissal if _to_int(st.get("outs")) else None,
                on_strike=bool(entry.get("active")),
            ))
        out.sort(key=lambda b: (b.not_out is False, ))  # not-out batters first
        return out

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
    def _batting_block(entry: dict, period: int) -> dict:
        for ls in entry.get("linescores") or []:
            if _to_int(ls.get("period")) == period:
                for inner in ls.get("linescores") or []:
                    if inner.get("batting"):
                        return inner["batting"]
        return {}


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

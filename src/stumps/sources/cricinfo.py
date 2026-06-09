"""ESPNcricinfo source via the unofficial ``hs-consumer-api``.

No API key is required, but the endpoint sits behind a CDN that *can* serve a
JS/browser challenge instead of JSON. We send a browser-like User-Agent and
cache aggressively; if we're blocked we raise :class:`SourceError` and the
aggregator falls back to cricketdata.org.

Endpoints (all under ``hs-consumer-api.espncricinfo.com/v1/pages``):
  - ``matches/current?lang=en&latest=true`` — discover live/recent matches and
    their ``objectId`` (matchId) + ``series.objectId`` (seriesId).
  - ``match/scorecard?lang=en&seriesId=&matchId=`` — full batting/bowling figures.

The exact JSON field names are reverse-engineered and can drift; every access
below is defensive (``_dig`` with fallbacks) so a shape change degrades to
partial data rather than a crash. If the live shape has moved, the place to
adjust is the ``_normalise_*`` helpers here.
"""

from __future__ import annotations

from typing import Any

import httpx

from stumps import config
from stumps.models import Batter, Bowler, Format, Innings, Match, Phase, Team
from stumps.sources.base import DataSource, DiskCache, SourceError


def _dig(obj: Any, *path: str, default: Any = None) -> Any:
    """Walk nested dict keys, returning ``default`` if any step is missing."""
    cur = obj
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _to_float_overs(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class CricinfoSource(DataSource):
    name = "cricinfo"

    def __init__(self, settings: config.Settings):
        super().__init__(settings)
        self.cache = DiskCache(settings)

    # -- HTTP ---------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        cache_key = f"cricinfo_{path}_{sorted(params.items())}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        url = f"{config.CRICINFO_BASE}/{path}"
        headers = {
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.espncricinfo.com/",
        }
        try:
            resp = httpx.get(
                url,
                params=params,
                headers=headers,
                timeout=self.settings.http_timeout_seconds,
                follow_redirects=True,
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise SourceError(f"Cricinfo request failed: {exc}") from exc
        except ValueError as exc:  # JSON decode -> probably a CDN challenge page
            raise SourceError("Cricinfo returned non-JSON (likely blocked)") from exc

        self.cache.set(cache_key, data)
        return data

    # -- public API ---------------------------------------------------------

    def fetch_current_matches(self) -> list[Match]:
        data = self._get("matches/current", {"lang": "en", "latest": "true"})
        raw_matches = data.get("matches") or data.get("content", {}).get("matches") or []
        if not raw_matches:
            raise SourceError("Cricinfo returned no matches (shape may have changed)")
        return [self._normalise_summary(m) for m in raw_matches]

    def enrich(self, match: Match) -> Match:
        """Fetch the full scorecard and attach batting/bowling figures.

        Best-effort: on any failure we return the match unchanged so the caller
        still shows the summary-level data.
        """
        if match.series_id is None:
            return match
        try:
            data = self._get(
                "match/scorecard",
                {"lang": "en", "seriesId": match.series_id, "matchId": match.match_id},
            )
        except SourceError:
            return match
        innings = self._normalise_scorecard(data)
        if innings:
            match.innings = innings
        return match

    # -- normalisers --------------------------------------------------------

    def _normalise_summary(self, raw: dict) -> Match:
        match_id = str(_dig(raw, "objectId") or _dig(raw, "id") or "")
        series_id = str(_dig(raw, "series", "objectId") or "")
        series_name = _dig(raw, "series", "name", default="") or _dig(
            raw, "series", "longName", default=""
        )
        fmt = self._format(raw)
        teams = self._teams(raw)
        phase = self._phase(raw)
        status = (
            _dig(raw, "statusText")
            or _dig(raw, "status")
            or _dig(raw, "statusEnumText")
            or ""
        )
        venue = _dig(raw, "ground", "name", default="") or _dig(
            raw, "venue", "name", default=""
        )

        match = Match(
            match_id=match_id,
            format=fmt,
            teams=teams,
            phase=phase,
            series_id=series_id or None,
            series_name=series_name or "",
            status_text=status,
            venue=venue,
            source=self.name,
        )
        if phase is Phase.COMPLETE:
            match.result_text = status
        # Lightweight innings from team summary scores (enrich() adds figures).
        match.innings = self._summary_innings(raw, teams)
        return match

    def _format(self, raw: dict) -> Format:
        class_id = _dig(raw, "internationalClassId") or _dig(raw, "classId")
        if class_id in config.CRICINFO_CLASS_MAP:
            return Format[config.CRICINFO_CLASS_MAP[int(class_id)]]
        fmt_str = (
            _dig(raw, "format")
            or _dig(raw, "matchType")
            or _dig(raw, "internationalClassCard")
            or ""
        ).upper()
        mapping = {
            "TEST": Format.TEST,
            "ODI": Format.ODI,
            "ODM": Format.LIST_A,
            "T20": Format.T20,
            "T20I": Format.T20I,
            "FC": Format.FIRST_CLASS,
            "FIRST_CLASS": Format.FIRST_CLASS,
            "LIST A": Format.LIST_A,
            "HUNDRED": Format.HUNDRED,
            "THE HUNDRED": Format.HUNDRED,
        }
        return mapping.get(fmt_str, Format.OTHER)

    def _teams(self, raw: dict) -> list[Team]:
        teams = []
        for entry in raw.get("teams", []) or []:
            team = entry.get("team", entry)
            name = (
                _dig(team, "longName")
                or _dig(team, "name")
                or _dig(team, "abbreviation")
                or "?"
            )
            teams.append(
                Team(
                    name=name,
                    short_name=_dig(team, "abbreviation", default="") or name[:3].upper(),
                    object_id=str(_dig(team, "objectId") or "") or None,
                )
            )
        return teams

    def _phase(self, raw: dict) -> Phase:
        state = (_dig(raw, "state") or _dig(raw, "status") or "").upper()
        stage = (_dig(raw, "stage") or "").upper()
        status_text = (_dig(raw, "statusText") or "").lower()

        if "stump" in status_text:
            return Phase.STUMPS
        if any(w in status_text for w in ("lunch", "tea", "drinks", "innings break", "rain", "bad light")):
            return Phase.BREAK
        if "abandon" in status_text:
            return Phase.ABANDONED
        if state in {"LIVE", "IN_PROGRESS"} or stage == "RUNNING":
            return Phase.LIVE
        if state in {"POST", "RESULT", "COMPLETE"} or stage == "FINISHED":
            return Phase.COMPLETE
        if state in {"PRE", "UPCOMING", "SCHEDULED"} or stage == "SCHEDULED":
            return Phase.UPCOMING
        return Phase.UNKNOWN

    def _summary_innings(self, raw: dict, teams: list[Team]) -> list[Innings]:
        """Build minimal innings from the team-level score strings, when present."""
        innings: list[Innings] = []
        for entry in raw.get("teams", []) or []:
            team = entry.get("team", entry)
            name = _dig(team, "longName") or _dig(team, "name") or "?"
            score = entry.get("score") or _dig(entry, "scoreInfo", "score")
            if not score:
                continue
            runs, wkts, declared, all_out = _parse_score(str(score))
            innings.append(
                Innings(
                    batting_team=name,
                    runs=runs,
                    wickets=wkts,
                    declared=declared,
                    all_out=all_out,
                )
            )
        return innings

    def _normalise_scorecard(self, data: dict) -> list[Innings]:
        raw_innings = (
            _dig(data, "scorecard", "innings")
            or _dig(data, "content", "scorecard", "innings")
            or []
        )
        result: list[Innings] = []
        for idx, raw in enumerate(raw_innings, start=1):
            batting = _dig(raw, "team", "longName") or _dig(raw, "battingTeam", "name") or ""
            inns = Innings(
                batting_team=batting,
                number=_to_int(_dig(raw, "inningNumber"), idx),
                runs=_to_int(_dig(raw, "runs")),
                wickets=_to_int(_dig(raw, "wickets")),
                overs=_to_float_overs(_dig(raw, "overs")),
                declared=bool(_dig(raw, "isDeclared")),
                all_out=_to_int(_dig(raw, "wickets")) >= 10,
                target=_to_int(_dig(raw, "target"), None) if _dig(raw, "target") else None,
                extras=_to_int(_dig(raw, "extras")),
                batters=self._batters(raw),
                bowlers=self._bowlers(raw),
            )
            inns.closed = bool(_dig(raw, "isComplete")) or inns.all_out or inns.declared
            result.append(inns)
        return result

    def _batters(self, raw: dict) -> list[Batter]:
        out = []
        for b in raw.get("inningBatsmen", []) or []:
            name = _dig(b, "player", "name") or _dig(b, "batsman", "name") or "?"
            is_out = bool(_dig(b, "isOut"))
            out.append(
                Batter(
                    name=name,
                    runs=_to_int(_dig(b, "runs")),
                    balls=_to_int(_dig(b, "balls")),
                    fours=_to_int(_dig(b, "fours")),
                    sixes=_to_int(_dig(b, "sixes")),
                    not_out=not is_out,
                    dismissal=_dig(b, "dismissalText", "long") if is_out else None,
                    on_strike=bool(_dig(b, "isOnStrike") or _dig(b, "isStriker")),
                )
            )
        return out

    def _bowlers(self, raw: dict) -> list[Bowler]:
        out = []
        for b in raw.get("inningBowlers", []) or []:
            name = _dig(b, "player", "name") or _dig(b, "bowler", "name") or "?"
            out.append(
                Bowler(
                    name=name,
                    overs=_to_float_overs(_dig(b, "overs")),
                    maidens=_to_int(_dig(b, "maidens")),
                    runs=_to_int(_dig(b, "conceded")),
                    wickets=_to_int(_dig(b, "wickets")),
                    bowling_now=bool(_dig(b, "isBowling")),
                )
            )
        return out


def _parse_score(score: str) -> tuple[int, int, bool, bool]:
    """Parse a score string like '180/4', '425', '250/8d' -> (runs, wkts,
    declared, all_out). Falls back to zeros on anything unexpected."""
    score = score.strip()
    declared = score.endswith("d") or score.endswith("dec")
    core = score.rstrip("dec ").strip()
    if "/" in core:
        runs_s, _, wkts_s = core.partition("/")
        runs = _to_int(runs_s)
        wkts = _to_int(wkts_s)
        return runs, wkts, declared, False
    runs = _to_int(core)
    # No wicket count shown usually means all out.
    return runs, 10, declared, True

"""cricketdata.org / cricapi.com source (free API key).

Used as the automatic fallback when Cricinfo is unavailable. Requires a free
key in the ``CRICKETDATA_API_KEY`` environment variable (get one at
https://cricketdata.org/signup.aspx). The free tier has a daily request quota,
so we cache and only enrich the matches we actually display.

Endpoints (under ``api.cricapi.com/v1``):
  - ``currentMatches?apikey=&offset=0`` — live/recent matches with team scores.
  - ``match_scorecard?apikey=&id=`` — full batting/bowling figures.

Response envelope: ``{"status": "success", "data": [...], "info": {...}}``.

**Deliberately a degraded backstop.** This source provides live/recent scores,
the status/result line, start times, and full scorecards (with full
``dismissal-text`` — actually richer than ESPN's how-out *mode*). It does *not*
populate the ESPN-only extras: points, standings, partnerships, fall of wickets,
multi-day timing for the win/draw estimate, the past-days results lookback
(``fetch_recent_results``) or upcoming-fixtures fetch (``fetch_upcoming``). Every
such field defaults safely and every renderer guards on it, so a cricketdata
match degrades gracefully — it just shows less. Reaching parity isn't worth it
for a source only hit when ESPN is down.
"""

from __future__ import annotations

from typing import Any

import httpx

from stumps import config
from stumps.models import Batter, Bowler, Format, Innings, Match, Phase, Team
from stumps.sources.base import DataSource, DiskCache, SourceError


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class CricketDataSource(DataSource):
    name = "cricketdata"

    def __init__(self, settings: config.Settings):
        super().__init__(settings)
        self.cache = DiskCache(settings)
        self.api_key = settings.cricketdata_api_key

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _get(self, endpoint: str, params: dict[str, Any]) -> dict:
        if not self.api_key:
            raise SourceError(
                "cricketdata.org needs an API key (set CRICKETDATA_API_KEY)"
            )
        params = {"apikey": self.api_key, **params}
        # Cache key omits the apikey so it doesn't leak into filenames.
        cache_params = {k: v for k, v in params.items() if k != "apikey"}
        cache_key = f"cricketdata_{endpoint}_{sorted(cache_params.items())}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        url = f"{config.CRICKETDATA_BASE}/{endpoint}"
        try:
            resp = httpx.get(
                url, params=params, timeout=self.settings.http_timeout_seconds
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            raise SourceError(f"cricketdata.org request failed: {exc}") from exc
        except ValueError as exc:
            raise SourceError("cricketdata.org returned non-JSON") from exc

        if data.get("status") != "success":
            raise SourceError(
                f"cricketdata.org error: {data.get('status')} "
                f"{data.get('reason') or data.get('info', {})}"
            )
        self.cache.set(cache_key, data)
        return data

    def fetch_current_matches(self) -> list[Match]:
        data = self._get("currentMatches", {"offset": 0})
        raw = data.get("data") or []
        if not raw:
            raise SourceError("cricketdata.org returned no current matches")
        return [self._normalise(m) for m in raw]

    def enrich(self, match: Match) -> Match:
        try:
            data = self._get("match_scorecard", {"id": match.match_id})
        except SourceError:
            return match
        detail = data.get("data") or {}
        cards = detail.get("scorecard") or []
        if not cards:
            return match
        # The scorecard objects carry batting/bowling figures but NO innings
        # totals — those live in the separate 'score' array (already parsed into
        # match.innings by _summary_innings). So merge the figures in and keep
        # the existing runs/wickets/overs; only rebuild from scratch if we have
        # no summary innings to merge into.
        if match.innings and len(match.innings) >= len(cards):
            for idx, card in enumerate(cards):
                match.innings[idx].batters = self._batters(card.get("batting") or [])
                match.innings[idx].bowlers = self._bowlers(card.get("bowling") or [])
        else:
            built = self._scorecard(detail)
            if built:
                match.innings = built
        return match

    # -- normalisers --------------------------------------------------------

    def _normalise(self, raw: dict) -> Match:
        names = [str(t) for t in (raw.get("teams") or [])]
        team_info = {ti.get("name"): ti for ti in (raw.get("teamInfo") or [])}
        teams = [
            Team(
                name=n,
                short_name=str(team_info.get(n, {}).get("shortname") or n[:3].upper()),
            )
            for n in names
        ]
        fmt = self._format(raw.get("matchType", ""), names, raw.get("name", ""))
        phase = self._phase(raw)
        status = raw.get("status", "")

        match = Match(
            match_id=str(raw.get("id", "")),
            format=fmt,
            teams=teams,
            phase=phase,
            series_id=str(raw.get("series_id") or "") or None,
            series_name=raw.get("name", ""),
            status_text=status,
            venue=raw.get("venue", ""),
            source=self.name,
            starts_at=str(raw.get("dateTimeGMT") or ""),
        )
        if phase is Phase.COMPLETE:
            match.result_text = status
        match.innings = self._summary_innings(raw)
        return match

    def _format(self, match_type: str, team_names: list[str], name: str) -> Format:
        mt = (match_type or "").lower()
        womens = "women" in name.lower() or any("women" in t.lower() for t in team_names)
        international = self._is_international(team_names)

        if mt == "test":
            if womens:
                return Format.WTEST
            return Format.TEST if international else Format.FIRST_CLASS
        if mt == "odi":
            if womens:
                return Format.WODI
            return Format.ODI if international else Format.LIST_A
        if mt in {"t20", "t20i"}:
            if womens:
                return Format.WT20I
            return Format.T20I if international else Format.T20
        if mt == "hundred":
            return Format.HUNDRED
        return Format.OTHER

    @staticmethod
    def _is_international(team_names: list[str]) -> bool:
        lowered = [n.lower() for n in team_names]
        if len(lowered) < 2:
            return False
        return all(
            any(nation in name for nation in config.ALL_NATIONS) for name in lowered
        )

    def _phase(self, raw: dict) -> Phase:
        status = (raw.get("status") or "").lower()
        started = bool(raw.get("matchStarted"))
        ended = bool(raw.get("matchEnded"))

        if "stump" in status:
            return Phase.STUMPS
        if any(w in status for w in ("lunch", "tea", "drinks", "innings break", "rain", "bad light")):
            return Phase.BREAK
        if "abandon" in status:
            return Phase.ABANDONED
        if ended:
            return Phase.COMPLETE
        if started:
            return Phase.LIVE
        return Phase.UPCOMING

    @staticmethod
    def _label_team(label: str) -> str:
        return label.split(" Inning")[0].strip() if "Inning" in label else label.strip()

    @staticmethod
    def _team_from_label(label: str, team_names: list[str]) -> str:
        """Best-effort single-label resolution to a canonical team name."""
        raw = CricketDataSource._label_team(label)
        rl = raw.lower()
        for name in team_names:
            nl = name.lower()
            if nl and (nl in rl or rl in nl):
                return name
        return raw

    @staticmethod
    def _assign_team(label: str, pool: list[str]) -> str:
        """Resolve an inning's batting team, consuming it from ``pool`` so each
        of the two teams is used once. Handles labels that are lower-cased or
        comma-mangled (containing both names) by relying on innings order."""
        rl = CricketDataSource._label_team(label).lower()
        for name in list(pool):
            nl = name.lower()
            if nl and (nl in rl or rl in nl):
                pool.remove(name)
                return name
        return pool.pop(0) if pool else CricketDataSource._label_team(label)

    def _summary_innings(self, raw: dict) -> list[Innings]:
        # score entries look like {"r":280,"w":8,"o":50.0,"inning":"New Zealand Inning 1"}
        pool = [str(t) for t in (raw.get("teams") or [])]
        innings = []
        for s in raw.get("score") or []:
            team = self._assign_team(str(s.get("inning", "")), pool)
            wkts = _to_int(s.get("w"))
            innings.append(
                Innings(
                    batting_team=team,
                    runs=_to_int(s.get("r")),
                    wickets=wkts,
                    overs=_to_float(s.get("o")),
                    all_out=wkts >= 10,
                )
            )
        return innings

    def _scorecard(self, detail: dict) -> list[Innings]:
        """Build innings from a scorecard payload. Totals come from the 'score'
        array (the scorecard objects don't carry them); used only when there are
        no summary innings to merge figures into."""
        cards = detail.get("scorecard") or []
        scores = detail.get("score") or []
        team_names: list[str] = []
        for s in scores:
            t = self._team_from_label(str(s.get("inning", "")), [])
            if t:
                team_names.append(t)
        result = []
        for idx, raw in enumerate(cards):
            score = scores[idx] if idx < len(scores) else {}
            team = self._team_from_label(str(raw.get("inning", "")), team_names)
            wkts = _to_int(score.get("w"))
            result.append(
                Innings(
                    batting_team=team,
                    number=idx + 1,
                    runs=_to_int(score.get("r")),
                    wickets=wkts,
                    overs=_to_float(score.get("o")),
                    all_out=wkts >= 10,
                    batters=self._batters(raw.get("batting") or []),
                    bowlers=self._bowlers(raw.get("bowling") or []),
                )
            )
        return result

    def _batters(self, rows: list[dict]) -> list[Batter]:
        out = []
        for b in rows:
            dismissal = b.get("dismissal-text") or b.get("dismissal") or ""
            is_out = bool(dismissal) and dismissal.lower() not in {"not out", "batting"}
            out.append(
                Batter(
                    name=str((b.get("batsman") or {}).get("name") or b.get("name") or "?"),
                    runs=_to_int(b.get("r")),
                    balls=_to_int(b.get("b")),
                    fours=_to_int(b.get("4s")),
                    sixes=_to_int(b.get("6s")),
                    not_out=not is_out,
                    dismissal=dismissal if is_out else None,
                )
            )
        return out

    def _bowlers(self, rows: list[dict]) -> list[Bowler]:
        out = []
        for b in rows:
            out.append(
                Bowler(
                    name=str((b.get("bowler") or {}).get("name") or b.get("name") or "?"),
                    overs=_to_float(b.get("o")),
                    maidens=_to_int(b.get("m")),
                    runs=_to_int(b.get("r")),
                    wickets=_to_int(b.get("w")),
                )
            )
        return out

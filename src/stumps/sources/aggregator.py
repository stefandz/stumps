"""Pick the best available source, with automatic fallback.

Order: ESPNcricinfo (richest, no key) -> cricketdata.org (needs a free key) ->
the built-in demo data (so the CLI always shows *something*, clearly labelled).
"""

from __future__ import annotations

import json
import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from stumps import config
from stumps.config import Settings
from stumps.models import Match, Phase
from stumps.sources.base import DataSource, SourceError
from stumps.sources.cricketdata import CricketDataSource, norm_name
from stumps.sources.espn import EspnSource
from stumps.sources.fixtures import DemoSource

#: A last-good snapshot older than this is too stale to pass off as "current";
#: we drop to demo data instead.
_SNAPSHOT_MAX_AGE = 12 * 3600


def resolve_squad_ids(
    followed_teams: list[str], discovered: dict[str, str]
) -> list[str]:
    """ESPNcricinfo object-ids for the *senior* men's/women's squads behind each
    followed name, for the always-show last-result/next-fixture fetch.

    Curated seeds (``config.SENIOR_SQUADS``) win; otherwise we match the
    ``discovered`` name→id map (learned from feeds) by *exact* team name — the
    token itself (e.g. "australia", "sunrisers hyderabad") and "<token> women"
    (e.g. "australia women"). Exact-match keeps it to the senior sides: it
    deliberately won't pull in "England Lions"/"India A"/"… Under-19s", which a
    loose substring on the follow token would."""
    ids: list[str] = []
    for token in followed_teams:
        seed = config.SENIOR_SQUADS.get(token)
        if seed is not None:
            ids.extend(v for v in (seed.get("men"), seed.get("women")) if v)
            continue  # a curated seed is authoritative for that team
        for name in (token, f"{token} women"):
            found = discovered.get(name)
            if found:
                ids.append(found)
    # de-dupe, preserve order
    seen: set[str] = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


@dataclass
class FetchResult:
    matches: list[Match]
    source: DataSource
    used_fallback: bool  # True if we fell back to demo data
    notices: list[str]  # human-readable notes about what was tried
    stale_as_of: str = ""  # set when serving a cached last-good snapshot offline


class Aggregator:
    def __init__(self, settings: Settings, *, demo_only: bool = False):
        self.settings = settings
        if demo_only:
            self.sources: list[DataSource] = [DemoSource(settings)]
        else:
            self.sources = [
                EspnSource(settings),
                CricketDataSource(settings),
            ]
        self._demo = DemoSource(settings)

    def fetch(
        self,
        *,
        lookback_days: int = 0,
        upcoming_days: int = 0,
        followed_teams: list[str] | None = None,
        last_next: bool = False,
    ) -> FetchResult:
        notices: list[str] = []
        for src in self.sources:
            if isinstance(src, CricketDataSource) and not src.available:
                notices.append("cricketdata.org skipped (no CRICKETDATA_API_KEY set)")
                continue
            try:
                matches = src.fetch_current_matches()
            except SourceError as exc:
                notices.append(f"{src.name} unavailable: {exc}")
                continue
            if lookback_days > 0:
                matches = self._merge(matches, src.fetch_recent_results, lookback_days)
            if upcoming_days > 0:
                matches = self._merge(matches, src.fetch_upcoming, upcoming_days)
            # Always-show a followed team's last result + next fixture, however
            # far outside the day-window they fall. Learn team object-ids from
            # whatever we've fetched so far (and past runs), then fetch each
            # followed squad's bookends — deduped against the live list.
            if last_next and followed_teams:
                id_map = self._record_team_ids(matches)
                matches = self._merge_last_next(
                    matches, src, resolve_squad_ids(followed_teams, id_map))
            if not isinstance(src, DemoSource):
                self._save_snapshot(matches)
            return FetchResult(matches, src, used_fallback=False, notices=notices)

        # All live sources failed — serve the last-good snapshot (clearly stamped
        # with its age) in preference to demo data, if we have a recent one.
        snapshot = self._load_snapshot()
        if snapshot is not None:
            matches, as_of = snapshot
            notices.append(f"live sources unavailable — showing cached data from {as_of}")
            return FetchResult(matches, self._demo, used_fallback=False,
                               notices=notices, stale_as_of=as_of)

        # Everything live failed (or we're in demo_only mode and DemoSource is
        # the only source — handled above). Fall back to demo data.
        if any(isinstance(s, DemoSource) for s in self.sources):
            # demo_only mode: the loop above already tried DemoSource; if we're
            # here it genuinely failed, which shouldn't happen.
            raise SourceError("Demo source failed: " + "; ".join(notices))
        matches = self._demo.fetch_current_matches()
        return FetchResult(matches, self._demo, used_fallback=True, notices=notices)

    @staticmethod
    def _merge(current: list[Match], fetcher, days: int) -> list[Match]:
        """Append matches from a past/future fetcher that aren't already in the
        live list (which wins on conflicts, being the freshest). A failure here
        only loses the addendum — never the live result — so swallow anything,
        not just SourceError (the reverse-engineered feed can raise on odd shapes)."""
        try:
            extra = fetcher(days)
        except Exception:
            return current
        seen = {m.match_id for m in current}
        return current + [m for m in extra if m.match_id not in seen]

    def _merge_last_next(
        self, current: list[Match], src: DataSource, squad_ids: list[str]
    ) -> list[Match]:
        """Append each squad's most-recent result + next fixture, deduped against
        the live list (which wins, being freshest). Best-effort per id — a single
        squad's failure never loses the others or the live result."""
        seen = {m.match_id for m in current}
        extra: list[Match] = []
        for object_id in squad_ids:
            try:
                bookends = src.fetch_team_last_next(object_id)
            except Exception:
                continue
            for match in bookends:
                if match.match_id and match.match_id not in seen:
                    seen.add(match.match_id)
                    extra.append(match)
        return current + extra

    # -- discovered team object-ids -----------------------------------------

    def _team_ids_path(self) -> Path:
        return self.settings.cache_dir / "team_ids.json"

    def _record_team_ids(self, matches: list[Match]) -> dict[str, str]:
        """Accrue a name→object-id map from the teams we've seen (across runs),
        so a followed side's squads can be resolved even when idle today. Keyed
        by exact lowercased team name ("england", "england women") — cricinfo
        suffixes women's sides with "Women", so men's/women's don't collide.
        Returns the merged, persisted map."""
        id_map = self._load_team_ids()
        changed = False
        for match in matches:
            for team in match.teams:
                if team.object_id and team.name:
                    key = team.name.lower()
                    if id_map.get(key) != team.object_id:
                        id_map[key] = team.object_id
                        changed = True
        if changed:
            try:
                with self._team_ids_path().open("w", encoding="utf-8") as fh:
                    json.dump(id_map, fh)
            except OSError:
                pass  # best-effort cache
        return id_map

    def _load_team_ids(self) -> dict[str, str]:
        try:
            with self._team_ids_path().open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _snapshot_path(self) -> Path:
        return self.settings.cache_dir / "last_good.pkl"

    def _save_snapshot(self, matches: list[Match]) -> None:
        """Persist the latest good fetch so we can serve it if we later go
        offline. Best-effort; pickled because Match is our own dataclass tree."""
        try:
            with self._snapshot_path().open("wb") as fh:
                pickle.dump({"time": time.time(), "matches": matches}, fh)
        except Exception:
            pass

    def _load_snapshot(self) -> tuple[list[Match], str] | None:
        """The last good fetch and a human "as of" stamp, or None if missing,
        unreadable (e.g. a model/class change), or too old."""
        try:
            with self._snapshot_path().open("rb") as fh:
                data = pickle.load(fh)
            if time.time() - data["time"] > _SNAPSHOT_MAX_AGE:
                return None
            as_of = datetime.fromtimestamp(data["time"]).strftime("%a %d %b %H:%M")
            return data["matches"], as_of
        except Exception:
            return None

    def augment(self, match: Match) -> None:
        """Best-effort hybrid: upgrade ESPN's dismissal *mode* ("caught") to
        cricketdata's full text ("c X b Y") in a single match's scorecard.

        Silent on any failure (no key / quota / no match) — the primary data
        stands. Aggressively cached, since a fallen wicket's text never changes:
        a finished match's scorecard is held for a week, a live one for minutes."""
        cd = next((s for s in self.sources if isinstance(s, CricketDataSource)), None)
        if cd is None or not cd.available:
            return
        ttl = 7 * 24 * 3600 if match.phase is Phase.COMPLETE else 600
        texts = cd.dismissal_texts(match.team_names, match.starts_at, scorecard_ttl=ttl)
        if not texts:
            return
        for inns in match.innings:
            for batter in inns.batters:
                if batter.dismissal:  # ESPN gave a mode for a dismissed batter
                    full = texts.get(norm_name(batter.name))
                    if full:
                        batter.dismissal = full

    @staticmethod
    def enrich(result: FetchResult, matches: list[Match]) -> None:
        """Enrich the given matches in place using the source they came from."""
        for match in matches:
            result.source.enrich(match)

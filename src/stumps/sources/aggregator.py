"""Pick the best available source, with automatic fallback.

Order: ESPNcricinfo (richest, no key) -> cricketdata.org (needs a free key) ->
the built-in demo data (so the CLI always shows *something*, clearly labelled).
"""

from __future__ import annotations

import pickle
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from stumps.config import Settings
from stumps.models import Match
from stumps.sources.base import DataSource, SourceError
from stumps.sources.cricketdata import CricketDataSource
from stumps.sources.espn import EspnSource
from stumps.sources.fixtures import DemoSource

#: A last-good snapshot older than this is too stale to pass off as "current";
#: we drop to demo data instead.
_SNAPSHOT_MAX_AGE = 12 * 3600


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

    def fetch(self, *, lookback_days: int = 0, upcoming_days: int = 0) -> FetchResult:
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

    @staticmethod
    def enrich(result: FetchResult, matches: list[Match]) -> None:
        """Enrich the given matches in place using the source they came from."""
        for match in matches:
            result.source.enrich(match)

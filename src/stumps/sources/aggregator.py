"""Pick the best available source, with automatic fallback.

Order: ESPNcricinfo (richest, no key) -> cricketdata.org (needs a free key) ->
the built-in demo data (so the CLI always shows *something*, clearly labelled).
"""

from __future__ import annotations

from dataclasses import dataclass

from stumps.config import Settings
from stumps.models import Match
from stumps.sources.base import DataSource, SourceError
from stumps.sources.cricketdata import CricketDataSource
from stumps.sources.espn import EspnSource
from stumps.sources.fixtures import DemoSource


@dataclass
class FetchResult:
    matches: list[Match]
    source: DataSource
    used_fallback: bool  # True if we fell back to demo data
    notices: list[str]  # human-readable notes about what was tried


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

    def fetch(self) -> FetchResult:
        notices: list[str] = []
        for src in self.sources:
            if isinstance(src, CricketDataSource) and not src.available:
                notices.append("cricketdata.org skipped (no CRICKETDATA_API_KEY set)")
                continue
            try:
                matches = src.fetch_current_matches()
                return FetchResult(matches, src, used_fallback=False, notices=notices)
            except SourceError as exc:
                notices.append(f"{src.name} unavailable: {exc}")

        # Everything live failed (or we're in demo_only mode and DemoSource is
        # the only source — handled above). Fall back to demo data.
        if any(isinstance(s, DemoSource) for s in self.sources):
            # demo_only mode: the loop above already tried DemoSource; if we're
            # here it genuinely failed, which shouldn't happen.
            raise SourceError("Demo source failed: " + "; ".join(notices))
        matches = self._demo.fetch_current_matches()
        return FetchResult(matches, self._demo, used_fallback=True, notices=notices)

    @staticmethod
    def enrich(result: FetchResult, matches: list[Match]) -> None:
        """Enrich the given matches in place using the source they came from."""
        for match in matches:
            result.source.enrich(match)

"""The data-source interface plus a small disk cache.

A source knows how to fetch *current* matches (live, at a break, at stumps, or
recently finished) and return them as normalised :class:`~stumps.models.Match`
objects. The aggregator tries sources in order until one succeeds.
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path

from stumps.config import Settings
from stumps.models import Match


class SourceError(RuntimeError):
    """A source could not return data (network, blocked, bad payload…)."""


class DataSource(ABC):
    name: str = "base"

    def __init__(self, settings: Settings):
        self.settings = settings

    @abstractmethod
    def fetch_current_matches(self) -> list[Match]:
        """Return current/recent matches as normalised Match objects.

        Raise :class:`SourceError` if the source is unavailable so the
        aggregator can fall back to the next one.
        """
        raise NotImplementedError

    def enrich(self, match: Match) -> Match:
        """Attach full batting/bowling figures to a match (best-effort).

        Default is a no-op (the summary already carries scores). Network sources
        override this to fetch a detailed scorecard for matches we'll display.
        """
        return match

    def fetch_recent_results(self, days: int) -> list[Match]:
        """Finished matches from each of the last ``days`` days (excluding today,
        which `fetch_current_matches` already covers), stamped with `finished_on`.

        Lets the app surface results that have aged out of the live feed. Default
        is none — only sources that can query past dates override this.
        """
        return []


class DiskCache:
    """Tiny TTL cache for raw JSON payloads, keyed by an opaque string.

    Keeps us well under any rate limit and makes repeated runs snappy. We cache
    the *raw* payload (not normalised Match objects) so the cache survives code
    changes to the normalisers.
    """

    def __init__(self, settings: Settings):
        self.dir = settings.cache_dir
        self.ttl = settings.cache_ttl_seconds

    def _path(self, key: str) -> Path:
        safe = "".join(c if c.isalnum() else "_" for c in key)
        return self.dir / f"cache_{safe}.json"

    def get(self, key: str) -> object | None:
        path = self._path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.ttl:
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, key: str, value: object) -> None:
        try:
            with self._path(key).open("w", encoding="utf-8") as fh:
                json.dump(value, fh)
        except OSError:
            pass  # cache is best-effort

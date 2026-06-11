"""User preferences: who/what to follow, what to show, how to show it.

Built from ``~/.config/stumps/config.toml`` defaults overlaid with CLI flags, so
``stumps`` can serve any fan (set ``team``/``region``/``domestic`` once) and any
use (filters, compact view, JSON).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stumps.config import resolve_domestic_key
from stumps.models import Format

#: Format-category keywords (for --format) -> the concrete Formats they cover.
FORMAT_CATEGORIES: dict[str, set[Format]] = {
    "test": {Format.TEST, Format.WTEST, Format.FIRST_CLASS},
    "odi": {Format.ODI, Format.WODI, Format.LIST_A},
    "t20": {Format.T20I, Format.WT20I, Format.T20},
    "hundred": {Format.HUNDRED},
}

#: --tier values, most→least selective, mapping to the lowest Tier int to keep.
TIER_FLOORS = {"followed": 0, "premier": 1, "domestic": 2, "all": 3}


@dataclass
class Preferences:
    # A — identity
    followed_teams: list[str] = field(default_factory=lambda: ["england"])
    region: str = "gb"
    domestic: str | None = "england"

    # B — filtering
    formats: set[Format] | None = None  # None = all
    live_only: bool = False
    show_finished: bool = True
    show_upcoming: bool = True
    gender: str | None = None  # "men" | "women" | None
    series_filter: str | None = None
    include_warmups: bool = False
    include_all: bool = False
    tier_floor: int = 2  # show followed + premier + domestic by default
    limit: int | None = None
    #: How many past days of finished results to pull in (followed/domestic/
    #: premier only); 0 disables. Default surfaces yesterday's results.
    results_days: int = 1

    # C — display
    compact: bool = False
    show_figures: bool = True
    show_winprob: bool = True
    show_dls: bool = True
    show_commentary: bool = True
    balls: int = 6
    #: Use the trained multi-day Test/first-class model instead of the heuristic.
    use_multiday_model: bool = False

    # D — output
    json_output: bool = False

    @classmethod
    def resolve(cls, args, config: dict | None = None) -> "Preferences":
        """Merge config.toml defaults with argparse flags (flags win)."""
        config = config or {}
        prefs = cls()

        # config.toml defaults
        if config.get("team"):
            t = config["team"]
            prefs.followed_teams = [t] if isinstance(t, str) else list(t)
        prefs.region = config.get("region", prefs.region)
        if "domestic" in config:
            prefs.domestic = resolve_domestic_key(config["domestic"])
        if "results_days" in config:
            prefs.results_days = int(config["results_days"])

        # CLI overrides
        if getattr(args, "team", None):
            prefs.followed_teams = [t.lower() for t in args.team]
        else:
            prefs.followed_teams = [t.lower() for t in prefs.followed_teams]
        if getattr(args, "no_team", False):
            prefs.followed_teams = []
        if getattr(args, "region", None):
            prefs.region = args.region
        if getattr(args, "domestic", None) is not None:
            prefs.domestic = resolve_domestic_key(args.domestic)

        if getattr(args, "format", None):
            wanted: set[Format] = set()
            for key in args.format:
                wanted |= FORMAT_CATEGORIES.get(key.lower(), set())
            prefs.formats = wanted or None
        prefs.live_only = getattr(args, "live_only", False)
        prefs.show_finished = not getattr(args, "no_finished", False)
        prefs.show_upcoming = not getattr(args, "no_upcoming", False)
        if getattr(args, "womens_only", False):
            prefs.gender = "women"
        elif getattr(args, "mens_only", False):
            prefs.gender = "men"
        prefs.series_filter = getattr(args, "series", None)
        prefs.include_warmups = getattr(args, "include_warmups", False)
        prefs.include_all = getattr(args, "all", False)
        if getattr(args, "tier", None):
            prefs.tier_floor = TIER_FLOORS.get(args.tier, prefs.tier_floor)
        if prefs.include_all:
            prefs.tier_floor = TIER_FLOORS["all"]
        prefs.limit = getattr(args, "limit", None)

        prefs.compact = getattr(args, "compact", False)
        prefs.show_figures = not getattr(args, "no_figures", False)
        prefs.show_winprob = not getattr(args, "no_winprob", False)
        prefs.show_dls = not getattr(args, "no_dls", False)
        prefs.show_commentary = not getattr(args, "no_commentary", False)
        if getattr(args, "balls", None) is not None:
            prefs.balls = args.balls
        if getattr(args, "no_results", False):
            prefs.results_days = 0
        elif getattr(args, "results", None) is not None:
            prefs.results_days = max(0, args.results)
        prefs.use_multiday_model = getattr(args, "test_model", False)
        prefs.json_output = getattr(args, "json", False)
        return prefs

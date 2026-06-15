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


def _formats_from(keys) -> set[Format]:
    """Expand --format / config `format` category keywords into concrete Formats.
    Shared by the CLI and config paths so they can't diverge."""
    wanted: set[Format] = set()
    for key in keys:
        wanted |= FORMAT_CATEGORIES.get(str(key).lower(), set())
    return wanted


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
    #: Drill into a single match (substring of title/series) with a full scorecard.
    match_query: str | None = None
    #: How many past days of finished results to pull in; 0 disables. Default
    #: surfaces yesterday's results.
    results_days: int = 1
    #: Keep recent results to your core teams only (followed/domestic/premier).
    #: Off by default: a notable international you saw live also lingers after it
    #: finishes, so it doesn't vanish the moment it ends.
    core_results_only: bool = False
    #: How many days ahead to pull in scheduled fixtures (your core teams);
    #: 0 disables. Default shows the next few days.
    upcoming_days: int = 3
    #: Always show each followed team's most-recent result and next fixture
    #: (men's and women's senior squads), however far outside the day-windows
    #: above they fall. On by default; --no-last-next opts out.
    followed_last_next: bool = True

    # C — display
    compact: bool = False
    show_figures: bool = True
    show_winprob: bool = True
    show_dls: bool = True
    show_commentary: bool = True
    show_standings: bool = False  # append full league tables (--standings)
    show_table: bool = True  # inline league positions of the two teams (--no-table)
    show_bonus: bool = True  # computed first-innings bonus points (--no-bonus)
    #: Label paired national sides by gender in the *match title* ("England Men v
    #: New Zealand Men"; women's titles already read "… Women" from the feed),
    #: while mentions *inside* the panel stay prosaic ("England") — the title has
    #: set the context. On by default; --no-gender-labels turns it off.
    gender_labels: bool = True
    #: Pulse (blink) the live badge on an interactive terminal to reinforce that
    #: a match is active. On by default; --no-live-pulse / live_pulse=false off.
    live_pulse: bool = True
    balls: int = 6
    #: Use the trained multi-day Test/first-class model instead of the heuristic.
    use_multiday_model: bool = False
    #: Disable colour (and other styling). Forces a plain console.
    plain: bool = False
    #: Force a console width (None = auto-detect from the terminal).
    width: int | None = None

    # D — output
    json_output: bool = False
    #: Single plain status line for the top match (tmux / polybar / menu bar).
    oneline: bool = False
    #: Desktop notifications for wickets/results of followed teams during --refresh.
    notify: bool = False

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
        if "upcoming_days" in config:
            prefs.upcoming_days = int(config["upcoming_days"])
        if "last_next" in config:
            prefs.followed_last_next = bool(config["last_next"])
        # Filtering defaults.
        if config.get("tier") in TIER_FLOORS:
            prefs.tier_floor = TIER_FLOORS[config["tier"]]
        if config.get("format"):
            prefs.formats = _formats_from(config["format"]) or None
        if config.get("gender") in ("men", "women"):
            prefs.gender = config["gender"]
        if "series" in config:
            prefs.series_filter = config["series"] or None
        if "limit" in config:
            prefs.limit = int(config["limit"])
        # Display defaults.
        if "balls" in config:
            prefs.balls = int(config["balls"])
        if "width" in config:
            prefs.width = int(config["width"]) if config["width"] else None
        # Boolean toggles: config sets the default. On-only flags (no --no-…
        # counterpart) OR their store_true on top; off-flags (--no-…) force the
        # value False after this. Visibility keys default on; the rest default off.
        prefs.notify = bool(config.get("notify", prefs.notify))
        prefs.show_standings = bool(config.get("standings", prefs.show_standings))
        prefs.core_results_only = bool(config.get("core_results", prefs.core_results_only))
        prefs.compact = bool(config.get("compact", prefs.compact))
        prefs.live_only = bool(config.get("live_only", prefs.live_only))
        prefs.include_warmups = bool(config.get("include_warmups", prefs.include_warmups))
        prefs.include_all = bool(config.get("all", prefs.include_all))
        prefs.plain = bool(config.get("plain", prefs.plain))
        prefs.use_multiday_model = bool(config.get("test_model", prefs.use_multiday_model))
        if "gender_labels" in config:
            prefs.gender_labels = bool(config["gender_labels"])
        if "live_pulse" in config:
            prefs.live_pulse = bool(config["live_pulse"])
        prefs.show_figures = bool(config.get("figures", prefs.show_figures))
        prefs.show_winprob = bool(config.get("winprob", prefs.show_winprob))
        prefs.show_dls = bool(config.get("dls", prefs.show_dls))
        prefs.show_commentary = bool(config.get("commentary", prefs.show_commentary))
        prefs.show_table = bool(config.get("table", prefs.show_table))
        prefs.show_bonus = bool(config.get("bonus", prefs.show_bonus))
        prefs.show_finished = bool(config.get("finished", prefs.show_finished))
        prefs.show_upcoming = bool(config.get("upcoming", prefs.show_upcoming))

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

        # CLI overrides, layered on the config defaults above. On-only store_true
        # flags OR on top; --no-… flags force False; value flags win when given.
        if getattr(args, "format", None):
            prefs.formats = _formats_from(args.format) or None
        prefs.live_only = prefs.live_only or getattr(args, "live_only", False)
        if getattr(args, "no_finished", False):
            prefs.show_finished = False
        if getattr(args, "no_upcoming", False):
            prefs.show_upcoming = False
        if getattr(args, "womens_only", False):
            prefs.gender = "women"
        elif getattr(args, "mens_only", False):
            prefs.gender = "men"
        if getattr(args, "series", None) is not None:
            prefs.series_filter = args.series
        prefs.include_warmups = prefs.include_warmups or getattr(args, "include_warmups", False)
        prefs.include_all = prefs.include_all or getattr(args, "all", False)
        if getattr(args, "tier", None):
            prefs.tier_floor = TIER_FLOORS.get(args.tier, prefs.tier_floor)
        if prefs.include_all:
            prefs.tier_floor = TIER_FLOORS["all"]
        if getattr(args, "limit", None) is not None:
            prefs.limit = args.limit
        prefs.match_query = getattr(args, "match", None)

        prefs.compact = prefs.compact or getattr(args, "compact", False)
        if getattr(args, "no_figures", False):
            prefs.show_figures = False
        if getattr(args, "no_winprob", False):
            prefs.show_winprob = False
        if getattr(args, "no_dls", False):
            prefs.show_dls = False
        if getattr(args, "no_commentary", False):
            prefs.show_commentary = False
        prefs.show_standings = prefs.show_standings or getattr(args, "standings", False)
        if getattr(args, "no_table", False):
            prefs.show_table = False
        if getattr(args, "no_bonus", False):
            prefs.show_bonus = False
        if getattr(args, "no_gender_labels", False):
            prefs.gender_labels = False
        if getattr(args, "no_live_pulse", False):
            prefs.live_pulse = False
        if getattr(args, "balls", None) is not None:
            prefs.balls = args.balls
        prefs.plain = prefs.plain or getattr(args, "plain", False)
        if getattr(args, "width", None) is not None:
            prefs.width = args.width
        if getattr(args, "no_results", False):
            prefs.results_days = 0
        elif getattr(args, "results", None) is not None:
            prefs.results_days = max(0, args.results)
        prefs.core_results_only = prefs.core_results_only or getattr(args, "core_results", False)
        if getattr(args, "upcoming", None) is not None:
            prefs.upcoming_days = max(0, args.upcoming)
        if not prefs.show_upcoming:
            prefs.upcoming_days = 0  # don't even fetch them
        if getattr(args, "no_last_next", False):
            prefs.followed_last_next = False
        prefs.use_multiday_model = prefs.use_multiday_model or getattr(args, "test_model", False)
        prefs.json_output = getattr(args, "json", False)
        prefs.oneline = getattr(args, "oneline", False)
        prefs.notify = prefs.notify or getattr(args, "notify", False)
        return prefs

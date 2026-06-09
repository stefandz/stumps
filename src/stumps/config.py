"""Configuration: classification allow-lists, settings, and source constants.

There is no clean field in any feed that says "this is a top-tier Test" or "this
is a World Cup", so we maintain our own allow-lists here. Tweak these freely.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------
# Classification allow-lists
# --------------------------------------------------------------------------

#: Names that identify the England national side (men's or women's). Matched
#: case-insensitively against team names. Domestic English counties are handled
#: separately (see ENGLISH_DOMESTIC_TEAMS) — this is the *national* team only.
ENGLAND_NAMES: frozenset[str] = frozenset({"england", "england women", "eng"})

#: The 12 ICC full-member (Test-status) nations. A "top-tier Test" is a Test
#: match between two of these. Matched case-insensitively against team names.
TOP_TIER_TEST_NATIONS: frozenset[str] = frozenset(
    {
        "afghanistan",
        "australia",
        "bangladesh",
        "england",
        "india",
        "ireland",
        "new zealand",
        "pakistan",
        "south africa",
        "sri lanka",
        "west indies",
        "zimbabwe",
    }
)

#: Associate/affiliate nations we may still see in internationals. Combined with
#: the full members to recognise a fixture as international (vs domestic) when a
#: source doesn't tell us — used to pick e.g. T20I vs domestic-T20.
ASSOCIATE_NATIONS: frozenset[str] = frozenset(
    {
        "scotland",
        "netherlands",
        "nepal",
        "namibia",
        "oman",
        "united states",
        "usa",
        "united arab emirates",
        "uae",
        "papua new guinea",
        "canada",
        "jersey",
        "italy",
        "hong kong",
    }
)

#: Any national side (full member or associate).
ALL_NATIONS: frozenset[str] = TOP_TIER_TEST_NATIONS | ASSOCIATE_NATIONS

#: Substrings (case-insensitive) in a *series* name that mark a premier ICC
#: international tournament. Kept broad on purpose; refine if it over-matches.
ICC_TOURNAMENT_MARKERS: tuple[str, ...] = (
    "world cup",
    "champions trophy",
    "world test championship",
    "wtc final",
)

#: The 18 first-class counties plus the men's/women's regional competitions, used
#: to recognise English domestic cricket across all formats (County Championship,
#: One-Day Cup, T20 Blast, The Hundred, women's regional teams). Matched as
#: case-insensitive substrings of team names.
ENGLISH_DOMESTIC_TEAMS: frozenset[str] = frozenset(
    {
        # First-class counties
        "derbyshire",
        "durham",
        "essex",
        "glamorgan",
        "gloucestershire",
        "hampshire",
        "kent",
        "lancashire",
        "leicestershire",
        "middlesex",
        "northamptonshire",
        "nottinghamshire",
        "somerset",
        "surrey",
        "sussex",
        "warwickshire",
        "worcestershire",
        "yorkshire",
        # The Hundred franchises
        "london spirit",
        "manchester originals",
        "northern superchargers",
        "oval invincibles",
        "southern brave",
        "trent rockets",
        "welsh fire",
        "birmingham phoenix",
    }
)

#: Series-name markers for English domestic competitions (a secondary signal,
#: since franchise/regional team names vary).
ENGLISH_DOMESTIC_SERIES_MARKERS: tuple[str, ...] = (
    "county championship",
    "vitality blast",
    "t20 blast",
    "metro bank one-day cup",
    "one-day cup",
    "the hundred",
    "rachael heyhoe flint",
    "charlotte edwards cup",
)

# --------------------------------------------------------------------------
# Source constants
# --------------------------------------------------------------------------

CRICINFO_BASE = "https://hs-consumer-api.espncricinfo.com/v1/pages"
CRICKETDATA_BASE = "https://api.cricapi.com/v1"

#: ESPNcricinfo internationalClassId -> our Format.
CRICINFO_CLASS_MAP: dict[int, str] = {
    1: "TEST",
    2: "ODI",
    3: "T20I",
    10: "WT20I",
    11: "WODI",
    12: "WTEST",
}

#: A browser-like UA reduces the chance of tripping Cricinfo's CDN challenge.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# --------------------------------------------------------------------------
# Runtime settings
# --------------------------------------------------------------------------


def _default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "stumps"


@dataclass
class Settings:
    """Resolved runtime configuration, mostly from environment variables."""

    cricketdata_api_key: str | None = field(
        default_factory=lambda: os.environ.get("CRICKETDATA_API_KEY")
    )
    cache_dir: Path = field(default_factory=_default_cache_dir)
    #: How long a cached API response stays fresh (seconds). Live cricket changes
    #: slowly enough that ~30s respects the source while feeling live.
    cache_ttl_seconds: int = int(os.environ.get("STUMPS_CACHE_TTL", "30"))
    http_timeout_seconds: float = float(os.environ.get("STUMPS_HTTP_TIMEOUT", "12"))
    #: Path to a trained win-probability model (joblib/pickle). Optional.
    winprob_model_path: Path = field(
        default_factory=lambda: _default_cache_dir() / "winprob_model.pkl"
    )

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    return Settings()

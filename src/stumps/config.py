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
#: international tournament — the actual events, not the build-up.
ICC_TOURNAMENT_MARKERS: tuple[str, ...] = (
    "world cup",
    "champions trophy",
    "world test championship",
    "wtc final",
)

#: Substrings that disqualify an otherwise-matching series from being "premier":
#: warm-ups and the multi-year qualifier pathways are World-Cup-*named* but are
#: not the tournament itself, so they shouldn't outrank English domestic cricket.
ICC_TOURNAMENT_EXCLUSIONS: tuple[str, ...] = (
    "warm-up",
    "warm up",
    "warmup",
    "league two",
    "league 2",
    "qualifier",
    "challenge league",
    "sub regional",
    "sub-regional",
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

#: Markers (in team OR series names) for second-string / age-group / academy
#: cricket that we don't treat as the professional domestic game of interest.
#: A match matching any of these drops out of the "English domestic" tier.
MINOR_CRICKET_MARKERS: tuple[str, ...] = (
    "2nd xi",
    "second xi",
    "second eleven",
    "academy",
    "u19",
    "u-19",
    "under-19",
    "under 19",
    "development",
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


@dataclass(frozen=True)
class DomesticScene:
    """A country's domestic cricket: team-name fragments + series-name markers
    used to recognise its professional domestic game (any format)."""

    teams: frozenset[str]
    series_markers: tuple[str, ...]


#: Home-domestic scenes for every ICC full member, keyed by a CLI-friendly token
#: (multi-word nations are hyphenated; see --domestic). Detection uses team-name
#: fragments and series-name markers — series markers are the robust signal.
DOMESTIC_SCENES: dict[str, DomesticScene] = {
    "england": DomesticScene(ENGLISH_DOMESTIC_TEAMS, ENGLISH_DOMESTIC_SERIES_MARKERS),
    "india": DomesticScene(
        frozenset({
            "super kings", "mumbai indians", "royal challengers", "knight riders",
            "delhi capitals", "sunrisers", "rajasthan royals", "punjab kings",
            "gujarat titans", "lucknow super giants",
        }),
        (
            "indian premier league", "ipl", "ranji trophy", "syed mushtaq ali",
            "vijay hazare", "duleep trophy", "irani", "women's premier league",
        ),
    ),
    "australia": DomesticScene(
        frozenset({
            "sixers", "thunder", "stars", "renegades", "heat", "scorchers",
            "strikers", "hurricanes", "new south wales", "victoria", "queensland",
            "western australia", "south australia", "tasmania",
        }),
        ("sheffield shield", "big bash", "bbl", "wbbl", "marsh cup", "marsh one-day cup"),
    ),
    "pakistan": DomesticScene(
        frozenset({
            "karachi kings", "lahore qalandars", "islamabad united", "peshawar zalmi",
            "multan sultans", "quetta gladiators",
        }),
        (
            "pakistan super league", "psl", "quaid-e-azam", "national t20",
            "president's", "pakistan cup", "champions one-day cup", "champions t20",
        ),
    ),
    "south-africa": DomesticScene(
        frozenset({
            "mi cape town", "sunrisers eastern cape", "pretoria capitals",
            "joburg super kings", "super giants", "paarl royals",
            "dolphins", "cobras",
        }),
        ("sa20", "csa 4-day", "csa one-day", "csa t20", "4-day series", "betway sa20"),
    ),
    "new-zealand": DomesticScene(
        frozenset({
            "auckland", "canterbury", "central districts", "central stags",
            "northern districts", "northern brave", "otago", "otago volts",
            "wellington", "wellington firebirds", "canterbury kings", "auckland aces",
        }),
        ("plunket shield", "super smash", "ford trophy"),
    ),
    "sri-lanka": DomesticScene(
        frozenset({
            "colombo strikers", "galle", "jaffna kings", "dambulla", "kandy falcons",
            "b-love kandy",
        }),
        ("lanka premier league", "lpl", "major league", "national super league",
         "premier league tournament", "invitation tournament"),
    ),
    "bangladesh": DomesticScene(
        frozenset({
            "comilla victorians", "rangpur riders", "khulna tigers",
            "chattogram challengers", "sylhet strikers", "fortune barishal",
            "durdanto dhaka", "dhaka capitals",
        }),
        ("bangladesh premier league", "bpl", "national cricket league",
         "dhaka premier", "bangladesh cricket league"),
    ),
    "west-indies": DomesticScene(
        frozenset({
            "trinbago knight riders", "guyana amazon warriors", "barbados royals",
            "st kitts", "saint lucia", "st lucia kings", "jamaica tallawahs",
            "antigua", "leeward islands", "windward islands", "trinidad", "guyana",
        }),
        ("caribbean premier league", "cpl", "west indies championship", "super50",
         "regional 4-day", "headley"),
    ),
    "afghanistan": DomesticScene(
        frozenset({
            "band-e-amir", "mis ainak", "speen ghar", "amo sharks", "pamir legends",
            "hindukush strikers", "boost defenders",
        }),
        ("shpageeza", "ahmad shah abdali", "ghazi amanullah"),
    ),
    "ireland": DomesticScene(
        frozenset({
            "leinster lightning", "northern knights", "munster reds",
            "north-west warriors", "north west warriors",
        }),
        ("inter-provincial", "interprovincial"),
    ),
    "zimbabwe": DomesticScene(
        frozenset({
            "mountaineers", "mid west rhinos", "rhinos", "southern rocks",
            "matabeleland tuskers", "tuskers", "mashonaland eagles", "eagles",
        }),
        ("logan cup", "pro50", "domestic twenty20"),
    ),
}

#: Aliases so common short forms / spaced names resolve to a scene key.
DOMESTIC_ALIASES: dict[str, str] = {
    "sa": "south-africa",
    "rsa": "south-africa",
    "nz": "new-zealand",
    "wi": "west-indies",
    "windies": "west-indies",
    "sl": "sri-lanka",
    "uk": "england",
    "eng": "england",
    "aus": "australia",
    "ind": "india",
    "pak": "pakistan",
    "ban": "bangladesh",
    "ire": "ireland",
    "afg": "afghanistan",
    "zim": "zimbabwe",
}


def resolve_domestic_key(value: str | None) -> str | None:
    """Normalise a --domestic / config value to a DOMESTIC_SCENES key (or None)."""
    if not value:
        return None
    key = value.strip().lower().replace(" ", "-")
    if key in {"none", "off", ""}:
        return None
    return DOMESTIC_ALIASES.get(key, key)

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


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "stumps"


def _load_config_file() -> dict:
    """Read ``~/.config/stumps/config.toml`` if present (else empty).

    A handy, git-safe place to keep your cricketdata.org key:

        # ~/.config/stumps/config.toml
        cricketdata_api_key = "your-key-here"

    Environment variables still take precedence over this file.
    """
    path = _config_dir() / "config.toml"
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+

        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


def _resolve_api_key(config_file: dict) -> str | None:
    # Precedence: environment variable, then config file.
    return os.environ.get("CRICKETDATA_API_KEY") or config_file.get(
        "cricketdata_api_key"
    )


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
    #: ESPN scoreboard region (gb, in, au, …) — affects coverage emphasis.
    region: str = "gb"

    def __post_init__(self) -> None:
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


def config_file_path() -> Path:
    return _config_dir() / "config.toml"


def load_config_file() -> dict:
    """Public accessor for the parsed config.toml (used for preference defaults)."""
    return _load_config_file()


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def dump_toml(data: dict) -> str:
    """Serialise a flat dict of str/bool/number/list values to TOML."""
    lines = ["# stumps configuration — edit by hand or run `stumps config`", ""]
    for key, value in data.items():
        if value is None:
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


def save_config_file(data: dict) -> Path:
    """Write config.toml (chmod 600, since it may hold an API key)."""
    path = config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_toml(data), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def load_settings() -> Settings:
    config_file = _load_config_file()
    return Settings(
        cricketdata_api_key=_resolve_api_key(config_file),
        region=os.environ.get("STUMPS_REGION") or config_file.get("region") or "gb",
    )

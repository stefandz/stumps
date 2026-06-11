"""Normalised domain model shared across data sources, prioritisation,
win-probability and rendering.

Every data source (Cricinfo, cricketdata.org, fixtures) maps its raw payload
into these dataclasses, so nothing downstream needs to know where the data came
from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Format(Enum):
    """Match format. Women's variants are distinct because we surface them
    separately and because they carry a different default G50 for DLS."""

    TEST = "Test"
    ODI = "ODI"
    T20I = "T20I"
    WTEST = "Women's Test"
    WODI = "Women's ODI"
    WT20I = "Women's T20I"
    FIRST_CLASS = "First-class"
    LIST_A = "List A"
    T20 = "T20"
    HUNDRED = "The Hundred"
    OTHER = "Other"

    @property
    def is_womens(self) -> bool:
        return self in {Format.WTEST, Format.WODI, Format.WT20I}

    @property
    def is_multi_day(self) -> bool:
        """Tests and first-class games span days (so 'stumps' applies)."""
        return self in {Format.TEST, Format.WTEST, Format.FIRST_CLASS}

    @property
    def is_limited_overs(self) -> bool:
        return self in {
            Format.ODI,
            Format.T20I,
            Format.WODI,
            Format.WT20I,
            Format.LIST_A,
            Format.T20,
            Format.HUNDRED,
        }

    @property
    def is_international(self) -> bool:
        return self in {
            Format.TEST,
            Format.ODI,
            Format.T20I,
            Format.WTEST,
            Format.WODI,
            Format.WT20I,
        }

    @property
    def overs_per_innings(self) -> int | None:
        """Allotted overs per innings, for DLS. None for multi-day / Hundred
        (which is measured in balls, handled separately)."""
        if self in {Format.ODI, Format.WODI, Format.LIST_A}:
            return 50
        if self in {Format.T20I, Format.WT20I, Format.T20}:
            return 20
        return None


class Phase(Enum):
    """Where a match is in its lifecycle — drives whether we show live figures,
    an end-of-day summary, or a final result."""

    UPCOMING = "upcoming"
    LIVE = "live"
    BREAK = "break"  # lunch / tea / drinks / innings break
    STUMPS = "stumps"  # close of play between days of a multi-day game
    COMPLETE = "complete"
    ABANDONED = "abandoned"
    UNKNOWN = "unknown"

    @property
    def is_active_today(self) -> bool:
        """True if the match is worth showing as 'current' (playing, at a break,
        or paused at stumps), as opposed to finished or not yet started."""
        return self in {Phase.LIVE, Phase.BREAK, Phase.STUMPS}


@dataclass
class Team:
    name: str
    short_name: str = ""
    object_id: str | None = None

    def __post_init__(self) -> None:
        if not self.short_name:
            self.short_name = self.name[:3].upper()


@dataclass
class Batter:
    name: str
    runs: int = 0
    balls: int = 0
    fours: int = 0
    sixes: int = 0
    not_out: bool = True
    dismissal: str | None = None  # e.g. "c Smith b Cummins"; None if not out
    on_strike: bool = False

    @property
    def strike_rate(self) -> float:
        return 100.0 * self.runs / self.balls if self.balls else 0.0


@dataclass
class Bowler:
    name: str
    overs: float = 0.0  # decimal-over notation: 7.3 = 7 overs, 3 balls
    maidens: int = 0
    runs: int = 0
    wickets: int = 0
    bowling_now: bool = False

    @property
    def economy(self) -> float:
        balls = int(self.overs) * 6 + round((self.overs % 1) * 10)
        return 6.0 * self.runs / balls if balls else 0.0


@dataclass
class Ball:
    """A single delivery, for the recent ball-by-ball insights section."""

    over: str  # cricket notation, e.g. "12.3"
    description: str  # concise commentary, e.g. "Boult to Kohli, FOUR"
    runs: int = 0
    is_wicket: bool = False
    is_boundary: bool = False  # four or six off the bat
    period: int = 1  # innings number


@dataclass
class Innings:
    batting_team: str
    bowling_team: str = ""
    number: int = 1  # 1..4 (Tests can have up to 4)
    runs: int = 0
    wickets: int = 0
    overs: float = 0.0  # decimal-over notation
    declared: bool = False
    all_out: bool = False
    closed: bool = False  # innings finished
    target: int | None = None  # runs required to win, if this is a chase
    extras: int = 0
    batters: list[Batter] = field(default_factory=list)
    bowlers: list[Bowler] = field(default_factory=list)

    @property
    def score(self) -> str:
        if self.all_out:
            return f"{self.runs} all out"
        base = f"{self.runs}/{self.wickets}"
        if self.declared:
            base += "d"
        return base

    @property
    def run_rate(self) -> float:
        balls = int(self.overs) * 6 + round((self.overs % 1) * 10)
        return 6.0 * self.runs / balls if balls else 0.0


@dataclass
class Match:
    """A single match, normalised across sources."""

    match_id: str
    format: Format
    teams: list[Team]
    phase: Phase = Phase.UNKNOWN
    series_id: str | None = None
    series_name: str = ""
    status_text: str = ""  # the headline summary line, e.g. "England need 120"
    result_text: str = ""  # populated when COMPLETE
    winner: str = ""        # winning team name when COMPLETE; "" if drawn/unknown
    finished_on: str = ""   # ISO date a recent result finished (recent-results fetch)
    points: str = ""        # league/tournament points awarded, e.g. "Surrey 15, Hampshire 13"
    venue: str = ""
    innings: list[Innings] = field(default_factory=list)
    # Multi-day context
    day_number: int | None = None
    total_days: int | None = None
    session: str = ""  # "Lunch", "Tea", "Stumps", ...
    #: Wall-clock context at the ground, used to estimate overs left on the day
    #: for the multi-day win/draw model. HH:MM strings; empty when unknown.
    local_time: str = ""   # current local time at the venue
    close_time: str = ""   # scheduled close of play
    source: str = ""  # which data source produced this
    ball_by_ball_available: bool = False  # does the source expose commentary?
    recent_balls: list[Ball] = field(default_factory=list)  # most recent first

    # --- convenience views -------------------------------------------------

    @property
    def is_womens(self) -> bool:
        return self.format.is_womens

    @property
    def team_names(self) -> list[str]:
        return [t.name for t in self.teams]

    @property
    def title(self) -> str:
        return " v ".join(self.team_names) if self.teams else self.match_id

    @property
    def current_innings(self) -> Innings | None:
        """The innings in progress (last open one) or the most recent."""
        for inns in reversed(self.innings):
            if not inns.closed:
                return inns
        return self.innings[-1] if self.innings else None

    @property
    def first_innings(self) -> Innings | None:
        return self.innings[0] if self.innings else None

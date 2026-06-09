"""Par scores and revised targets via the DLS Standard Edition.

The headline number for a live limited-overs chase is the **par score**: where
the chasing team's total "should" be, right now, for the match to be even. It is
the revised-target formula *without the +1* (ECB regulations clause 5.5/5.7).
We use it as a state-of-play indicator exactly as broadcasters do.

Everything here assumes an *uninterrupted* match unless you pass the optional
overrides describing a reduction in overs — the live feeds we read don't reliably
expose interruption history, so the no-rain case is the sensible default.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from stumps.dls.table import resource_pct

# Average 50-over first-innings total, used only when Team 2 has *more*
# resources than Team 1 (clause 1.12). Configurable because it changes over
# time and by level of the game.
G50_FULL_MEMBER = 245.0
G50_ASSOCIATE_OR_WOMENS_ODI = 200.0


@dataclass(frozen=True)
class DLSResult:
    """Outcome of a Standard-Edition par/target calculation."""

    par_score: int
    """Par score at the current point of Team 2's innings (no +1)."""
    target: int
    """Revised target — the minimum Team 2 need to win."""
    runs_ahead: int | None
    """Team 2 score minus par. Positive = ahead of DLS par. None if no score."""
    r1: float
    """Resource % available to Team 1."""
    r2_total: float
    """Resource % available to Team 2 for their whole innings."""
    r2_used: float
    """Resource % Team 2 has consumed so far."""
    note: str = "DLS Standard Edition (unofficial, indicative)"

    @property
    def status_phrase(self) -> str:
        """Human phrase like 'ahead of DLS par by 7' or 'behind DLS par by 3'."""
        if self.runs_ahead is None:
            return f"DLS par {self.par_score}"
        if self.runs_ahead > 0:
            return f"{self.runs_ahead} ahead of DLS par ({self.par_score})"
        if self.runs_ahead < 0:
            return f"{-self.runs_ahead} behind DLS par ({self.par_score})"
        return f"level with DLS par ({self.par_score})"


def revised_target(
    first_innings_runs: int,
    r1: float,
    r2: float,
    g50: float = G50_FULL_MEMBER,
) -> int:
    """Team 2's revised target T, per ECB clause 5.6.

    - R2 < R1:  T = floor(S * R2/R1) + 1
    - R2 == R1: T = S + 1
    - R2 > R1:  T = S + floor((R2-R1) * G50/100) + 1
    """
    s = first_innings_runs
    if r1 <= 0:
        raise ValueError("r1 (Team 1 resource %) must be positive")
    if r2 < r1:
        return math.floor(s * r2 / r1) + 1
    if math.isclose(r2, r1):
        return s + 1
    return s + math.floor((r2 - r1) * g50 / 100.0) + 1


def par_score(
    first_innings_runs: int,
    overs_per_innings: float,
    team2_overs_used: float,
    team2_wickets_lost: int,
    team2_score: int | None = None,
    *,
    team1_resource_pct: float | None = None,
    team2_total_overs: float | None = None,
    g50: float = G50_FULL_MEMBER,
) -> DLSResult:
    """Compute the DLS Standard-Edition par score and revised target.

    Parameters
    ----------
    first_innings_runs:
        Team 1's total, ``S``.
    overs_per_innings:
        Overs each side was allotted at the start of the match, ``N`` (50, 20…).
    team2_overs_used / team2_wickets_lost:
        Team 2's current position (overs in decimal-over terms, wickets down).
    team2_score:
        Team 2's current runs. If given, ``runs_ahead`` is populated.
    team1_resource_pct:
        Override R1. Defaults to an uninterrupted innings of ``overs_per_innings``.
    team2_total_overs:
        Overs Team 2 will get in total (defaults to ``overs_per_innings``). Pass a
        smaller value for a rain-reduced chase.
    """
    if team2_total_overs is None:
        team2_total_overs = overs_per_innings

    r1 = (
        team1_resource_pct
        if team1_resource_pct is not None
        else resource_pct(overs_per_innings, 0)
    )
    r2_total = resource_pct(team2_total_overs, 0)

    overs_remaining = max(0.0, team2_total_overs - team2_overs_used)
    resource_remaining = resource_pct(overs_remaining, team2_wickets_lost)
    r2_used = max(0.0, r2_total - resource_remaining)

    par = math.floor(first_innings_runs * r2_used / r1)
    target = revised_target(first_innings_runs, r1, r2_total, g50=g50)

    runs_ahead = None if team2_score is None else team2_score - par

    return DLSResult(
        par_score=par,
        target=target,
        runs_ahead=runs_ahead,
        r1=round(r1, 1),
        r2_total=round(r2_total, 1),
        r2_used=round(r2_used, 1),
    )

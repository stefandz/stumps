"""Match-state extraction and the feature vector for win probability.

The clean, well-defined case is a limited-overs **second-innings chase**: a
fixed target, a known number of balls, wickets in hand. That's what both the
trained model and the heuristic operate on, and what we extract here.

The feature order defined in :data:`FEATURE_ORDER` is the single source of truth
shared by training (``winprob/train.py``) and inference (``winprob/estimator.py``)
so the two can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from stumps.models import Format, Match


def overs_to_balls(overs: float) -> int:
    """Decimal-over notation (7.3 = 7 overs 3 balls) -> ball count."""
    whole = int(overs)
    frac_balls = round((overs - whole) * 10)
    return whole * 6 + frac_balls


@dataclass
class ChaseState:
    """A second-innings chase position."""

    is_t20: bool
    target: int
    runs: int
    wickets_lost: int
    balls_bowled: int
    balls_total: int
    chasing_team: str = ""
    defending_team: str = ""

    @property
    def runs_needed(self) -> int:
        return max(0, self.target - self.runs)

    @property
    def balls_remaining(self) -> int:
        return max(0, self.balls_total - self.balls_bowled)

    @property
    def wickets_in_hand(self) -> int:
        return max(0, 10 - self.wickets_lost)

    @property
    def current_run_rate(self) -> float:
        return 6.0 * self.runs / self.balls_bowled if self.balls_bowled else 0.0

    @property
    def required_run_rate(self) -> float:
        balls = self.balls_remaining
        return 6.0 * self.runs_needed / balls if balls else float("inf")


#: Feature names in fixed order — used by both training and inference.
FEATURE_ORDER: tuple[str, ...] = (
    "balls_remaining",
    "wickets_in_hand",
    "runs_needed",
    "required_run_rate",
    "current_run_rate",
    "is_t20",
)


def chase_features(state: ChaseState) -> dict[str, float]:
    """Map a chase state to named features (then vectorised via FEATURE_ORDER)."""
    rrr = state.required_run_rate
    # Cap an infinite required rate (no balls left) to keep the model finite.
    if rrr == float("inf"):
        rrr = 36.0
    return {
        "balls_remaining": float(state.balls_remaining),
        "wickets_in_hand": float(state.wickets_in_hand),
        "runs_needed": float(state.runs_needed),
        "required_run_rate": float(min(rrr, 36.0)),
        "current_run_rate": float(state.current_run_rate),
        "is_t20": 1.0 if state.is_t20 else 0.0,
    }


def feature_vector(state: ChaseState) -> list[float]:
    feats = chase_features(state)
    return [feats[name] for name in FEATURE_ORDER]


def extract_chase_state(match: Match) -> ChaseState | None:
    """Pull a :class:`ChaseState` from a limited-overs second-innings chase.

    Returns None during the first innings (no target yet). Limited-overs games
    have exactly two innings, so the chase is the second one; its target is
    inferred as the first-innings total + 1 when the live feed omits it (the
    summary endpoints usually do).
    """
    if not match.format.is_limited_overs:
        return None
    overs = match.format.overs_per_innings
    if not overs:
        return None
    if len(match.innings) < 2:
        return None  # still the first innings — not a chase

    first = match.innings[0]
    chasing = match.innings[-1]
    target = chasing.target or (first.runs + 1)
    if target <= 0:
        return None

    return ChaseState(
        is_t20=match.format in {Format.T20I, Format.WT20I, Format.T20},
        target=target,
        runs=chasing.runs,
        wickets_lost=chasing.wickets,
        balls_bowled=overs_to_balls(chasing.overs),
        balls_total=overs * 6,
        chasing_team=chasing.batting_team,
        defending_team=chasing.bowling_team,
    )

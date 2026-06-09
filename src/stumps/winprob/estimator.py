"""Win-probability estimation: trained model with heuristic fallbacks.

Always labelled as an estimate — this is *not* CricViz WinViz. Three paths:
  * Limited-overs chase: a Cricsheet-trained model if available, else a
    transparent run-rate/wickets heuristic.
  * Limited-overs first innings: a rough projected-score-vs-par heuristic.
  * Tests / first-class: a crude lead/time-based 3-way (team/team/draw) lean.
"""

from __future__ import annotations

import math
import pickle
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from stumps.config import Settings, load_settings
from stumps.models import Format, Match
from stumps.winprob.state import (
    ChaseState,
    extract_chase_state,
    feature_vector,
    overs_to_balls,
)

NOT_WINVIZ = "Estimate only — home-grown, not CricViz WinViz"


@dataclass
class WinEstimate:
    probabilities: dict[str, float]  # outcome label -> probability (sums to ~1)
    method: str  # "model" | "heuristic" | "first-innings-heuristic" | "test-heuristic"
    note: str = NOT_WINVIZ
    extra: list[str] = field(default_factory=list)  # short context lines

    @property
    def favoured(self) -> tuple[str, float]:
        label = max(self.probabilities, key=self.probabilities.get)
        return label, self.probabilities[label]


def _logistic(z: float) -> float:
    if z < -60:
        return 0.0
    if z > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


# --------------------------------------------------------------------------
# Trained model
# --------------------------------------------------------------------------


@lru_cache(maxsize=2)
def _load_model(path_str: str) -> dict | None:
    """Load a pickled model artifact ``{"model", "order", ...}`` or None.

    Requires scikit-learn at import time to unpickle the estimator; if it's
    missing or the file is absent/corrupt we return None and the caller uses the
    heuristic.
    """
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        import sklearn  # noqa: F401  (needed to unpickle the estimator)

        with path.open("rb") as fh:
            artifact = pickle.load(fh)
        if "model" in artifact and "order" in artifact:
            return artifact
    except Exception:
        return None
    return None


def _model_chase_prob(state: ChaseState, settings: Settings) -> float | None:
    artifact = _load_model(str(settings.winprob_model_path))
    if artifact is None:
        return None
    try:
        vec = [feature_vector(state)]
        proba = artifact["model"].predict_proba(vec)[0]
        # Class 1 == chasing team wins (see train.py).
        classes = list(artifact["model"].classes_)
        idx = classes.index(1) if 1 in classes else 1
        return float(proba[idx])
    except Exception:
        return None


# --------------------------------------------------------------------------
# Heuristics
# --------------------------------------------------------------------------


def heuristic_chase_prob(state: ChaseState) -> float:
    """Transparent fallback P(chasing team wins).

    Compares the required run rate to a 'sustainable' rate implied by wickets in
    hand and format, nudged by how many wickets are standing. Monotonic and
    bounded; good enough as a backstop, not a substitute for the trained model.
    """
    if state.runs_needed <= 0:
        return 1.0
    if state.wickets_in_hand <= 0 or state.balls_remaining <= 0:
        return 0.0

    wih = state.wickets_in_hand
    if state.is_t20:
        sustainable = 6.5 + 0.55 * wih  # 10 wkts in hand ~ 12 rpo achievable
    else:
        sustainable = 4.0 + 0.45 * wih  # ODI

    margin = sustainable - state.required_run_rate
    z = 0.85 * margin + 0.18 * (wih - 5)
    # Late-innings damping: with very few balls left, variance is lower, so a
    # positive margin is more decisive.
    if state.balls_remaining <= 12:
        z *= 1.4
    return _logistic(z)


def _first_innings_estimate(match: Match) -> WinEstimate | None:
    inns = match.current_innings
    if inns is None or inns.target is not None:
        return None
    overs = match.format.overs_per_innings
    if not overs:
        return None
    balls_bowled = overs_to_balls(inns.overs)
    if balls_bowled == 0:
        return None
    balls_left = max(0, overs * 6 - balls_bowled)
    wih = max(0, 10 - inns.wickets)
    crr = inns.run_rate
    # Expected remaining runs: sustain current rate, scaled by wickets in hand.
    exp_remaining = crr * (balls_left / 6.0) * (0.55 + 0.045 * wih)
    projected = inns.runs + exp_remaining

    is_t20 = match.format in {Format.T20I, Format.WT20I, Format.T20}
    par = 165.0 if is_t20 else 245.0
    spread = par * 0.22
    p_bat = _logistic((projected - par) / spread)

    batting = inns.batting_team
    others = [t for t in match.team_names if t != batting]
    fielding = others[0] if others else "Opponent"
    return WinEstimate(
        probabilities={batting: round(p_bat, 3), fielding: round(1 - p_bat, 3)},
        method="first-innings-heuristic",
        extra=[f"projected ~{int(projected)} ({batting} batting first)"],
    )


def _test_estimate(match: Match) -> WinEstimate | None:
    if len(match.teams) < 2:
        return None
    a, b = match.team_names[0], match.team_names[1]

    def total(team: str) -> int:
        return sum(i.runs for i in match.innings if team.lower() in i.batting_team.lower())

    def innings_count(team: str) -> int:
        return sum(1 for i in match.innings if team.lower() in i.batting_team.lower())

    net = total(a) - total(b)
    dominance = math.tanh(net / 120.0)
    # Mute the lean until both sides have batted (a one-sided scorecard isn't a
    # real advantage — the other team simply hasn't batted yet).
    if min(innings_count(a), innings_count(b)) == 0:
        dominance *= 0.25

    # Time left drives draw likelihood: lots of time -> results; running out with
    # no dominance -> draw.
    if match.day_number and match.total_days:
        remaining = max(0.0, (match.total_days - match.day_number) / match.total_days)
    else:
        remaining = 0.4
    draw = (1 - abs(dominance)) * (0.6 - 0.5 * remaining)
    draw = min(0.9, max(0.03, draw))

    share = 1 - draw
    p_a = share * (0.5 + 0.5 * dominance)
    p_b = share * (0.5 - 0.5 * dominance)
    return WinEstimate(
        probabilities={a: round(p_a, 3), b: round(p_b, 3), "Draw": round(draw, 3)},
        method="test-heuristic",
        note=NOT_WINVIZ + " · Test leans are rough",
    )


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def estimate(match: Match, settings: Settings | None = None) -> WinEstimate | None:
    """Best available win-probability estimate for a match, or None."""
    settings = settings or load_settings()

    # No scoring data yet -> nothing meaningful to estimate from.
    if not any(i.runs > 0 for i in match.innings):
        return None

    chase = extract_chase_state(match)
    if chase is not None:
        chasing = chase.chasing_team or "Chasing"
        others = [t for t in match.team_names if t != chasing]
        defending = chase.defending_team or (others[0] if others else "Defending")

        if chase.runs_needed <= 0:
            return WinEstimate({chasing: 1.0, defending: 0.0}, method="settled")

        p = _model_chase_prob(chase, settings)
        method = "model"
        if p is None:
            p = heuristic_chase_prob(chase)
            method = "heuristic"
        extra = [
            f"need {chase.runs_needed} off {chase.balls_remaining} "
            f"(req {chase.required_run_rate:.1f}/over, {chase.wickets_in_hand} wkts in hand)"
        ]
        return WinEstimate(
            probabilities={chasing: round(p, 3), defending: round(1 - p, 3)},
            method=method,
            extra=extra,
        )

    if match.format.is_limited_overs:
        return _first_innings_estimate(match)

    if match.format.is_multi_day:
        return _test_estimate(match)

    return None

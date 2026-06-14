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
from stumps.winprob.multiday import (
    MultiDayState,
    extract_multiday_state,
    feature_vector_md,
    overs_per_day,
)
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


@lru_cache(maxsize=2)
def _load_multiday_model(path_str: str) -> dict | None:
    """Load the multi-day model artifact (must carry ``multiday: True``)."""
    path = Path(path_str)
    if not path.exists():
        return None
    try:
        import sklearn  # noqa: F401

        with path.open("rb") as fh:
            artifact = pickle.load(fh)
        if artifact.get("multiday") and "model" in artifact and "order" in artifact:
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


def _chase_outcome(
    target: float, overs: float, chaser_wih: int
) -> tuple[float, float, float]:
    """(chaser win, defender win via bowl-out, draw) for a side chasing
    ``target`` in ``overs`` with ``chaser_wih`` wickets standing.

    The asymmetry a lead-based lean misses: the side batting last can secure a
    *draw* simply by surviving the overs left, so a big target is not a
    near-certain loss for them. A result needs either the target reached (chaser
    wins) or all standing wickets taken in time (defender wins). Shared by the
    real fourth innings and the *projected* fourth innings implied by a
    third-innings position."""
    if target <= 0:                    # target already overhauled / no lead to defend
        return 1.0, 0.0, 0.0
    if chaser_wih <= 0 or overs <= 0:  # innings over / time up, target not reached
        return 0.0, 1.0, 0.0

    rrr = target / overs               # runs per over required
    # Can the target realistically be chased? ~4.5 rpo is a stiff-but-doable
    # fourth-innings rate; temper by wickets in hand.
    gettable = _logistic(1.1 * (4.5 - rrr))
    wkt_ok = _logistic(0.5 * (chaser_wih - 3))
    # Absolute size matters too: a low required rate over a huge number of overs
    # still implies a massive chase, and 400+ fourth-innings targets are almost
    # never overhauled however much time there is. (~380 = highest real chase.)
    feasible = _logistic(0.015 * (380.0 - target))
    p_chase = gettable * wkt_ok * feasible
    # Time to bowl them out? A side batting to save the game loses a wicket
    # roughly every ~12.5 overs; enough such overs to take the standing wickets
    # favours the bowling side.
    p_bowl = (1.0 - p_chase) * _logistic(0.55 * (overs / 12.5 - chaser_wih))
    p_draw = max(0.0, 1.0 - p_chase - p_bowl)
    return p_chase, p_bowl, p_draw


def _fourth_innings_probs(state: MultiDayState) -> tuple[float, float, float]:
    """(batting-side win, bowling-side win, draw) for a fourth-innings position.
    The batting side is the chaser; the bowling side wins by bowling them out."""
    return _chase_outcome(
        float(state.runs_to_win), max(0.0, state.overs_remaining), state.wickets_in_hand
    )


#: Rough runs a third-innings side adds per remaining wicket when projecting the
#: target it will eventually set (first-class lower-order partnerships).
_RUNS_PER_REMAINING_WICKET = 24.0
#: Run rate the third-innings side bats at while extending the lead, used to
#: estimate how many overs it consumes before the final-innings chase begins.
_THIRD_INNINGS_RUN_RATE = 3.3


def _third_innings_projection(state: MultiDayState) -> tuple[int, float]:
    """Project the (target, overs-for-the-chase) the third-innings position
    implies for the side batting *last*."""
    extra_runs = max(0, state.wickets_in_hand) * _RUNS_PER_REMAINING_WICKET
    target = max(0, int(round(state.lead + extra_runs + 1)))
    overs_used = extra_runs / _THIRD_INNINGS_RUN_RATE
    chase_overs = max(0.0, state.overs_remaining - overs_used)
    return target, chase_overs


def _third_innings_probs(state: MultiDayState) -> dict[str, float]:
    """Third innings: the batting side is *setting* a target for the opponent to
    chase last, so a modest lead favours the **bowling** side, not the batting
    side — the opposite of a raw lead-dominance lean. We project the final lead
    the batting side reaches, then assess the implied fourth-innings chase for
    the bowling side (a fresh innings, 10 wickets in hand)."""
    target, chase_overs = _third_innings_projection(state)
    p_chaser, p_defender, p_draw = _chase_outcome(float(target), chase_overs, 10)
    return {
        state.batting_team: p_defender,   # set the target, must bowl the chaser out
        state.bowling_team: p_chaser,      # bats last
        "Draw": p_draw,
    }


def _early_innings_probs(state: MultiDayState) -> dict[str, float]:
    """Innings 1–3: a lead-and-time lean. Plenty of overs left -> a result is
    likely; running out of time with no dominance -> a draw."""
    dominance = math.tanh(state.lead / 120.0)
    # A one-sided scorecard (only one side has batted) isn't a real advantage.
    if state.innings_number <= 1:
        dominance *= 0.25

    total_overs = max(1, state.total_days) * overs_per_day(
        # any multi-day format works for the per-day figure
        Format.TEST if state.total_days >= 5 else Format.FIRST_CLASS
    )
    fraction_left = min(1.0, state.overs_remaining / total_overs)
    draw = (1 - abs(dominance)) * (0.6 - 0.5 * fraction_left)
    draw = min(0.9, max(0.03, draw))

    share = 1 - draw
    return {
        state.batting_team: share * (0.5 + 0.5 * dominance),
        state.bowling_team: share * (0.5 - 0.5 * dominance),
        "Draw": draw,
    }


def _test_estimate(match: Match) -> WinEstimate | None:
    state = extract_multiday_state(match)
    if state is None:
        return None

    extra: list[str] = []
    if state.is_fourth_innings:
        p_bat, p_bowl, p_draw = _fourth_innings_probs(state)
        probs = {
            state.batting_team: p_bat,
            state.bowling_team: p_bowl,
            "Draw": p_draw,
        }
        if state.runs_to_win > 0:
            extra.append(
                f"~{state.overs_remaining:.0f} overs left; {state.batting_team} "
                f"need {state.runs_to_win} at {state.required_run_rate:.1f}/over, "
                f"{state.wickets_in_hand} wkts in hand"
            )
    elif state.innings_number == 3:
        probs = _third_innings_probs(state)
        target, chase_overs = _third_innings_projection(state)
        verb = "lead" if state.lead >= 0 else "trail"
        extra.append(
            f"{state.batting_team} {verb} by {abs(state.lead)} with "
            f"{state.wickets_in_hand} wkts left; projecting ~{target} for "
            f"{state.bowling_team} to chase in ~{chase_overs:.0f} overs"
        )
    else:
        probs = _early_innings_probs(state)
        extra.append(f"~{state.overs_remaining:.0f} overs left in the match")

    probs = {k: round(v, 3) for k, v in probs.items()}
    return WinEstimate(
        probabilities=probs,
        method="test-heuristic",
        note=NOT_WINVIZ + " · Test leans are rough",
        extra=extra,
    )


def _multiday_model_estimate(match: Match, settings: Settings) -> WinEstimate | None:
    """Trained multi-day model estimate, or None (no model / not applicable)."""
    artifact = _load_multiday_model(str(settings.winprob_multiday_model_path))
    if artifact is None:
        return None
    state = extract_multiday_state(match)
    if state is None:
        return None
    try:
        model = artifact["model"]
        proba = model.predict_proba([feature_vector_md(state)])[0]
        classes = list(model.classes_)
        # Class framing (see cricsheet.multiday_rows_from_match): 1 = batting side
        # wins, 0 = bowling side wins, 2 = draw.
        def p(cls: int) -> float:
            return float(proba[classes.index(cls)]) if cls in classes else 0.0
        probs = {
            state.batting_team: round(p(1), 3),
            state.bowling_team: round(p(0), 3),
            "Draw": round(p(2), 3),
        }
    except Exception:
        return None
    extra = [f"~{state.overs_remaining:.0f} overs left in the match"]
    if state.is_fourth_innings and state.runs_to_win > 0:
        extra = [
            f"~{state.overs_remaining:.0f} overs left; {state.batting_team} "
            f"need {state.runs_to_win} at {state.required_run_rate:.1f}/over, "
            f"{state.wickets_in_hand} wkts in hand"
        ]
    return WinEstimate(probabilities=probs, method="multiday-model", extra=extra)


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


def estimate(
    match: Match,
    settings: Settings | None = None,
    *,
    use_multiday_model: bool = False,
) -> WinEstimate | None:
    """Best available win-probability estimate for a match, or None.

    For multi-day games the transparent heuristic is the default; the trained
    multi-day model is used only when ``use_multiday_model`` is set (CLI
    ``--test-model``) and a model is present, falling back to the heuristic."""
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
        if use_multiday_model:
            est = _multiday_model_estimate(match, settings)
            if est is not None:
                return est
        return _test_estimate(match)

    return None

"""Win-probability: state extraction, heuristics, Cricsheet parsing, model path."""

import pickle

import pytest

from stumps.config import Settings
from stumps.models import Format, Innings, Match, Phase, Team
from stumps.winprob import estimate, extract_chase_state
from stumps.winprob.cricsheet import chase_rows_from_match
from stumps.winprob.estimator import heuristic_chase_prob
from stumps.winprob.state import (
    FEATURE_ORDER,
    ChaseState,
    feature_vector,
    overs_to_balls,
)
from stumps.sources.fixtures import sample_matches


def _wc_chase():
    return next(m for m in sample_matches() if m.match_id == "demo-ind-nz-wc")


# -- state ------------------------------------------------------------------


def test_overs_to_balls():
    assert overs_to_balls(38.0) == 228
    assert overs_to_balls(7.3) == 45  # 7*6 + 3


def test_extract_chase_state_from_world_cup():
    state = extract_chase_state(_wc_chase())
    assert state is not None
    assert state.target == 281
    assert state.runs == 210
    assert state.runs_needed == 71
    assert state.balls_total == 300
    assert state.balls_remaining == 300 - 228
    assert state.wickets_in_hand == 6


def test_feature_vector_matches_order():
    state = extract_chase_state(_wc_chase())
    vec = feature_vector(state)
    assert len(vec) == len(FEATURE_ORDER)


def test_no_chase_state_for_first_innings():
    m = Match("x", Format.ODI, [Team("A"), Team("B")], phase=Phase.LIVE,
              innings=[Innings("A", "B", 1, 150, 3, 30.0)])
    assert extract_chase_state(m) is None


def test_chase_target_inferred_when_feed_omits_it():
    # Live feeds (cricketdata/Cricinfo summaries) don't include a target; it
    # must be inferred as first-innings total + 1.
    m = Match("x", Format.ODI, [Team("A"), Team("B")], phase=Phase.LIVE,
              innings=[
                  Innings("A", "B", 1, 250, 10, 50.0, all_out=True, closed=True),
                  Innings("B", "A", 2, 120, 3, 25.0),  # no target set
              ])
    state = extract_chase_state(m)
    assert state is not None
    assert state.target == 251  # 250 + 1
    assert state.runs == 120
    assert state.chasing_team == "B"


# -- heuristic --------------------------------------------------------------


def test_heuristic_bounds():
    state = ChaseState(False, 250, 100, 4, 180, 300)
    p = heuristic_chase_prob(state)
    assert 0.0 <= p <= 1.0


def test_heuristic_easier_chase_is_more_likely():
    # 20 needed off 60 balls, 8 wickets vs 80 needed off 24 balls, 2 wickets.
    easy = ChaseState(False, 250, 230, 2, 240, 300)  # need 20 off 60
    hard = ChaseState(False, 250, 170, 8, 276, 300)  # need 80 off 24
    assert heuristic_chase_prob(easy) > heuristic_chase_prob(hard)


def test_heuristic_won_and_lost_edges():
    assert heuristic_chase_prob(ChaseState(False, 100, 100, 0, 120, 300)) == 1.0
    assert heuristic_chase_prob(ChaseState(False, 200, 50, 10, 300, 300)) == 0.0


# -- estimate entry point ---------------------------------------------------


def test_estimate_chase_probabilities_sum_to_one():
    est = estimate(_wc_chase(), Settings(cricketdata_api_key=None))
    assert est is not None
    assert est.method in {"model", "heuristic"}
    assert abs(sum(est.probabilities.values()) - 1.0) < 1e-6
    assert set(est.probabilities) == {"India", "New Zealand"}


def test_estimate_test_has_draw_outcome():
    test_match = next(m for m in sample_matches() if m.format is Format.TEST)
    est = estimate(test_match)
    assert est is not None
    assert "Draw" in est.probabilities
    assert abs(sum(est.probabilities.values()) - 1.0) < 1e-6


def test_estimate_first_innings_heuristic():
    m = Match("x", Format.ODI, [Team("A"), Team("B")], phase=Phase.LIVE,
              innings=[Innings("A", "B", 1, 180, 3, 30.0)])
    est = estimate(m)
    assert est is not None
    assert est.method == "first-innings-heuristic"


# -- cricsheet parsing ------------------------------------------------------


def _synthetic_cricsheet_match(winner="India"):
    deliveries = [{"runs": {"total": 1}} for _ in range(12)]
    return {
        "info": {
            "match_type": "ODI",
            "overs": 50,
            "teams": ["New Zealand", "India"],
            "outcome": {"winner": winner},
        },
        "innings": [
            {"team": "New Zealand", "overs": [{"over": 0, "deliveries": deliveries}]},
            {
                "team": "India",
                "target": {"runs": 281, "overs": 50},
                "overs": [{"over": i, "deliveries": deliveries} for i in range(2)],
            },
        ],
    }


def test_chase_rows_extraction_and_label():
    rows = chase_rows_from_match(_synthetic_cricsheet_match("India"), sample_every=6)
    assert rows  # at least one sampled row
    assert all(len(vec) == len(FEATURE_ORDER) for vec, _ in rows)
    assert all(label == 1 for _, label in rows)  # India (chasing) won

    rows_lost = chase_rows_from_match(_synthetic_cricsheet_match("New Zealand"))
    assert all(label == 0 for _, label in rows_lost)


def test_chase_rows_skips_no_result():
    m = _synthetic_cricsheet_match()
    m["info"]["outcome"] = {"result": "no result"}
    assert chase_rows_from_match(m) == []


# -- trained-model integration (no network) ---------------------------------


def test_model_path_is_used_when_present(tmp_path):
    sklearn = pytest.importorskip("sklearn")
    from sklearn.ensemble import HistGradientBoostingClassifier
    import numpy as np

    # Tiny synthetic model: easier chases (more balls, fewer needed) -> win.
    rng = np.random.default_rng(0)
    X, y = [], []
    for _ in range(400):
        balls_rem = rng.integers(1, 250)
        wih = rng.integers(1, 11)
        needed = rng.integers(1, 200)
        rrr = 6 * needed / balls_rem
        crr = rng.uniform(3, 9)
        X.append([balls_rem, wih, needed, min(rrr, 36), crr, 0.0])
        y.append(1 if (rrr < 7 and wih > 3) else 0)
    model = HistGradientBoostingClassifier(max_iter=50).fit(np.array(X), np.array(y))

    model_path = tmp_path / "winprob_model.pkl"
    with model_path.open("wb") as fh:
        pickle.dump({"model": model, "order": list(FEATURE_ORDER)}, fh)

    settings = Settings(cache_dir=tmp_path, winprob_model_path=model_path,
                        cricketdata_api_key=None)
    est = estimate(_wc_chase(), settings)
    assert est is not None
    assert est.method == "model"
    assert 0.0 <= est.probabilities["India"] <= 1.0

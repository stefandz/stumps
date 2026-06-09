"""DLS Standard Edition tests, anchored on the ECB regulations' worked examples."""

import math

import pytest

from stumps.dls import par_score, resource_pct, revised_target
from stumps.dls.table import resource_pct_from_balls


# --- Resource table anchors (verified against ECB/Wikipedia Standard Edition) ---


@pytest.mark.parametrize(
    "overs,wkts,expected",
    [
        (50, 0, 100.0),
        (40, 0, 89.3),
        (30, 0, 75.1),
        (20, 0, 56.6),
        (10, 0, 32.1),
        (30, 2, 67.3),
        (0, 0, 0.0),
        (50, 10, 0.0),  # all out -> no resources
    ],
)
def test_resource_table_anchors(overs, wkts, expected):
    assert resource_pct(overs, wkts) == pytest.approx(expected, abs=0.05)


def test_resource_interpolation_is_between_whole_overs():
    # 30.5 overs / 0 wkts must sit between the 30 and 31 over rows.
    assert resource_pct(30, 0) < resource_pct(30.5, 0) < resource_pct(31, 0)


def test_resource_from_balls_matches_over_notation():
    # 30 overs exactly = 180 balls.
    assert resource_pct_from_balls(180, 2) == pytest.approx(resource_pct(30, 2))


# --- The two canonical worked examples from the ECB regulations document ---


def test_ecb_worked_example_par_110():
    # 50-over match, Team 1 = 200 (target 201). Team 2 reach 115/4 after 30
    # overs when abandoned. The regulations state the par score is 110, so
    # Team 2 (115) win by 5.
    result = par_score(
        first_innings_runs=200,
        overs_per_innings=50,
        team2_overs_used=30,
        team2_wickets_lost=4,
        team2_score=115,
    )
    assert result.par_score == 110
    assert result.runs_ahead == 5
    assert result.target == 201  # uninterrupted: R1 == R2


def test_revised_target_r2_less_than_r1():
    # Team 2 given fewer resources -> target reduced, +1.
    t = revised_target(first_innings_runs=250, r1=100.0, r2=80.0)
    assert t == math.floor(250 * 0.8) + 1  # 201


def test_revised_target_equal_resources_is_plus_one():
    assert revised_target(200, r1=100.0, r2=100.0) == 201


def test_revised_target_r2_greater_uses_g50():
    # Team 1 interrupted, Team 2 has more resources -> add expected extra runs.
    t = revised_target(150, r1=70.0, r2=100.0, g50=245)
    assert t == 150 + math.floor((100.0 - 70.0) * 245 / 100.0) + 1  # 150+73+1=224


def test_uninterrupted_t20_par_equals_score():
    # In an uninterrupted T20 (R1 == R2 == resource@20overs), par == current
    # score projection: a side level on DLS sits right on par.
    result = par_score(
        first_innings_runs=180,
        overs_per_innings=20,
        team2_overs_used=20,
        team2_wickets_lost=5,
        team2_score=180,
    )
    # Having used all overs/the full innings, r2_used == r1, so par == S.
    assert result.par_score == 180
    assert result.target == 181

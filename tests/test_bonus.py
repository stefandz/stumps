"""Bonus-point computation: rules per competition + match-level aggregation."""

from stumps import bonus
from stumps.models import Format, Innings, Match, OverScore, Team


def _match(series, fmt, innings):
    return Match(
        match_id="x", format=fmt,
        teams=[Team("Sussex"), Team("Glamorgan")],
        series_name=series, innings=innings,
    )


# --- rule selection -----------------------------------------------------------

def test_rule_for_matches_known_competitions():
    assert bonus.rule_for("Rothesay County Championship Division One").over_cap == 110.0
    assert bonus.rule_for("Sheffield Shield 2025-26").over_cap == 100.0
    assert bonus.rule_for("Plunket Shield").over_cap == 110.0


def test_rule_for_unknown_competition_is_none():
    assert bonus.rule_for("Vitality Blast") is None
    assert bonus.rule_for("") is None


# --- per-rule point tables ----------------------------------------------------

def test_county_thresholds():
    r = bonus.rule_for("County Championship")
    assert [r.batting_points(x) for x in (249, 250, 300, 350, 400, 450, 500)] == \
        [0, 1, 2, 3, 4, 5, 5]
    assert [r.bowling_points(w) for w in (2, 3, 5, 6, 9, 10)] == [0, 1, 1, 2, 3, 3]


def test_plunket_thresholds():
    r = bonus.rule_for("Plunket Shield")
    assert [r.batting_points(x) for x in (199, 200, 250, 300, 350)] == [0, 1, 2, 3, 4]
    assert [r.bowling_points(w) for w in (3, 5, 7, 9)] == [1, 2, 3, 4]


def test_sheffield_shield_linear_batting():
    r = bonus.rule_for("Sheffield Shield")
    assert r.batting_points(200) == 0
    assert r.batting_points(350) == 1.5  # 150 runs above 200 * 0.01
    assert r.batting_points(400) == 2.0
    # uncapped — keeps accruing beyond 400
    assert round(r.batting_points(500), 2) == 3.0
    assert [r.bowling_points(w) for w in (4, 5, 7, 9)] == [0, 0.5, 1.0, 1.5]


# --- match aggregation --------------------------------------------------------

def test_match_bonus_live_first_innings():
    # Glamorgan 107/6 batting first: 0 batting points; Sussex 6 wkts → 2 bowling.
    m = _match("County Championship", Format.FIRST_CLASS, [
        Innings("Glamorgan", number=1, runs=107, wickets=6, overs=36.0),
    ])
    by = {b.team: b for b in bonus.match_bonus(m)}
    assert by["Glamorgan"].batting == 0 and by["Glamorgan"].batting_seen
    assert not by["Glamorgan"].bowling_seen  # Glamorgan haven't bowled yet
    assert by["Sussex"].bowling == 2 and by["Sussex"].bowling_seen
    assert not by["Sussex"].batting_seen
    assert by["Sussex"].total == 2


def test_match_bonus_both_first_innings():
    m = _match("County Championship", Format.FIRST_CLASS, [
        Innings("Glamorgan", number=1, runs=420, wickets=10, overs=119.0),
        Innings("Sussex", number=2, runs=300, wickets=9, overs=95.0),
    ])
    by = {b.team: b for b in bonus.match_bonus(m)}
    # Glamorgan: 420 → 4 batting; bowled Sussex out (9 wkts in window) → 3 bowling.
    assert by["Glamorgan"].batting == 4 and by["Glamorgan"].bowling == 3
    # Sussex: 300 → 2 batting; took 10 Glamorgan wkts → 3 bowling.
    assert by["Sussex"].batting == 2 and by["Sussex"].bowling == 3


def test_second_innings_earns_nothing():
    m = _match("County Championship", Format.FIRST_CLASS, [
        Innings("Glamorgan", number=1, runs=300, wickets=10, overs=90.0),
        Innings("Sussex", number=2, runs=250, wickets=10, overs=80.0),
        Innings("Glamorgan", number=3, runs=450, wickets=2, overs=100.0),  # ignored
    ])
    by = {b.team: b for b in bonus.match_bonus(m)}
    assert by["Glamorgan"].batting == 2  # from the 300, not the 3rd-innings 450


def test_over_window_uses_over_scores_when_present():
    # 150 overs bowled, but only the first 110 count. over_scores: 3 runs/over and
    # a wicket every 10 overs → first 110 overs = 330 runs, 11 wickets.
    overs = [OverScore(runs=3, wickets=(1 if (i + 1) % 10 == 0 else 0))
             for i in range(150)]
    m = _match("County Championship", Format.FIRST_CLASS, [
        Innings("Glamorgan", number=1, runs=450, wickets=15, overs=150.0,
                over_scores=overs),
    ])
    by = {b.team: b for b in bonus.match_bonus(m)}
    # window runs = 330 → 2 batting; window wkts = 11 → capped at 3 bowling. Exact.
    assert by["Glamorgan"].batting == 2 and not by["Glamorgan"].approx
    assert by["Sussex"].bowling == 3 and not by["Sussex"].approx


def test_past_window_without_over_scores_flags_approx():
    # Innings ran past the 110-over cap and we have no over-by-over data, so the
    # current score may overstate the locked figure — flagged approximate.
    m = _match("County Championship", Format.FIRST_CLASS, [
        Innings("Glamorgan", number=1, runs=460, wickets=10, overs=130.0),
    ])
    by = {b.team: b for b in bonus.match_bonus(m)}
    assert by["Glamorgan"].batting == 5 and by["Glamorgan"].approx
    # within the cap, no flag
    m2 = _match("County Championship", Format.FIRST_CLASS, [
        Innings("Glamorgan", number=1, runs=460, wickets=10, overs=108.0),
    ])
    assert not bonus.match_bonus(m2)[0].approx


def test_non_bonus_competition_is_none():
    assert bonus.match_bonus(_match("Vitality Blast", Format.T20, [])) is None
    # right competition, wrong format (bonus schemes are first-class only)
    assert bonus.match_bonus(_match("County Championship", Format.ODI, [])) is None


def test_no_innings_yet_is_none():
    assert bonus.match_bonus(_match("County Championship", Format.FIRST_CLASS, [])) is None

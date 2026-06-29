"""Win-probability: state extraction, heuristics, Cricsheet parsing, model path."""

import pickle

import pytest

from stumps.config import Settings
from stumps.models import Format, Innings, Match, Phase, Team
from stumps.winprob import estimate, extract_chase_state
from stumps.winprob.cricsheet import chase_rows_from_match, multiday_rows_from_match
from stumps.winprob.estimator import _chase_outcome, heuristic_chase_prob
from stumps.winprob.multiday import (
    FEATURE_ORDER_MD,
    extract_multiday_state,
    overs_remaining_estimate,
)
from stumps.winprob.state import (
    FEATURE_ORDER,
    ChaseState,
    feature_vector,
    overs_to_balls,
)
from stumps.sources.fixtures import sample_matches


def _fourth_innings_match(
    *, runs=67, wickets=2, day=4, total=4, local="16:30", start="11:00",
    close="18:00", fmt=Format.FIRST_CLASS,
):
    """Surrey lead big; the side batting last (Hampshire) blocks for a draw."""
    return Match(
        match_id="md", format=fmt, phase=Phase.LIVE,
        teams=[Team("Surrey", "SUR"), Team("Hampshire", "HAM")],
        day_number=day, total_days=total, local_time=local,
        start_time=start, close_time=close,
        innings=[
            Innings("Surrey", "Hampshire", 1, 421, 10, 110.0, all_out=True, closed=True),
            Innings("Hampshire", "Surrey", 2, 333, 10, 95.0, all_out=True, closed=True),
            Innings("Surrey", "Hampshire", 3, 259, 5, 48.0, declared=True, closed=True),
            Innings("Hampshire", "Surrey", 4, runs, wickets, 34.3),
        ],
    )


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


# -- multi-day (Option A: heuristic) ----------------------------------------


def test_overs_remaining_from_clock():
    m = _fourth_innings_match(local="16:00", close="18:00", day=4, total=4)
    # 2h to close, first-class ≈ 3.75 min/over -> ~32 overs, no full days left.
    assert 28 <= overs_remaining_estimate(m) <= 36


def test_overs_remaining_fallback_without_clock():
    m = _fourth_innings_match(local="", close="", day=3, total=4)
    # No clock -> mid-day prior (~half of 96) plus one full day (96).
    assert 130 <= overs_remaining_estimate(m) <= 150


def test_overs_remaining_zero_at_stumps():
    # Genuine end-of-day stumps: it's the afternoon (past the scheduled start),
    # so today's play is done and no full days remain.
    m = _fourth_innings_match(day=4, total=4, local="16:30", start="11:00")
    m.phase = Phase.STUMPS
    assert overs_remaining_estimate(m) == 0.0


def test_overs_remaining_full_day_at_morning_of_final_day():
    # Morning of the final day, before the first ball: the feed still carries
    # the previous evening's "stumps" label, but a whole day is to come — must
    # NOT collapse to zero (which produced bogus 100% probabilities).
    m = _fourth_innings_match(day=4, total=4, local="09:30", start="11:00")
    m.phase = Phase.STUMPS
    # No full days left after today, but today's full ~96-over allocation remains.
    assert overs_remaining_estimate(m) == 96.0


def test_overs_remaining_full_day_at_morning_mid_match():
    # Same morning-before-play case at a day-2/4 transition: today's allocation
    # plus the two remaining full days.
    m = _fourth_innings_match(day=2, total=4, local="09:30", start="11:00")
    m.phase = Phase.STUMPS
    assert overs_remaining_estimate(m) == 96.0 * 3


def test_overs_remaining_morning_uses_start_prior_from_close():
    # Feed gave no start time: the prior (close − 7h = 11:00) still recognises
    # the morning-before-play case, so a full day remains.
    m = _fourth_innings_match(day=4, total=4, local="09:30", start="",
                              close="18:00")
    m.phase = Phase.STUMPS
    assert overs_remaining_estimate(m) == 96.0


def test_overs_remaining_morning_uses_default_prior_without_close():
    # No start and no close: fall back to the plain 11:00 prior.
    m = _fourth_innings_match(day=4, total=4, local="09:30", start="", close="")
    m.phase = Phase.STUMPS
    assert overs_remaining_estimate(m) == 96.0


def test_overs_remaining_evening_stumps_no_start_still_zero():
    # No start time, but it's the evening (past the prior): genuine end of day.
    m = _fourth_innings_match(day=4, total=4, local="19:00", start="",
                              close="18:00")
    m.phase = Phase.STUMPS
    assert overs_remaining_estimate(m) == 0.0


def test_multiday_state_fourth_innings():
    st = extract_multiday_state(_fourth_innings_match(runs=67, wickets=2))
    assert st.is_fourth_innings
    assert st.batting_team == "Hampshire"
    # Surrey 680, Hampshire 400 -> need 281 to win, 8 wickets in hand.
    assert st.runs_to_win == 281
    assert st.wickets_in_hand == 8


def test_test_heuristic_draw_dominant_in_dead_fourth_innings():
    # The reported bug: huge lead + little time = a draw, not a 96% home win.
    est = estimate(_fourth_innings_match())
    assert est.method == "test-heuristic"
    assert est.probabilities["Draw"] > 0.85
    assert est.probabilities["Surrey"] < 0.15
    assert abs(sum(est.probabilities.values()) - 1.0) < 1e-6


def test_chase_outcome_time_up_with_wickets_is_a_draw_not_a_bowling_win():
    # Regression: when the overs run out with the chaser still holding wickets,
    # the match is DRAWN (the batting side survived) — not a bowling-side win.
    # The old code returned (0, 1, 0) for both "all out" and "time up", flipping
    # a near-certain draw to 100% to the bowling side as the clock estimate hit 0.
    assert _chase_outcome(257, 0.0, 5) == (0.0, 0.0, 1.0)   # time up, wkts in hand
    assert _chase_outcome(257, 20.0, 0) == (0.0, 1.0, 0.0)  # bowled out -> bowling win
    # And it's continuous: at one over left a survival-bound chase is mostly a draw,
    # so dropping to zero overs should not discontinuously hand it to the bowlers.
    _, p_bowl_one_over, p_draw_one_over = _chase_outcome(257, 1.0, 5)
    assert p_draw_one_over > 0.9
    assert p_bowl_one_over < 0.1


def test_test_heuristic_gettable_chase_favours_batting_side():
    # Small target, lots of time, wickets in hand -> the chasing side wins.
    m = _fourth_innings_match(runs=560, wickets=2, day=3, total=4)  # needs ~21
    est = estimate(m)
    assert est.probabilities["Hampshire"] > est.probabilities["Surrey"]
    assert est.probabilities["Hampshire"] > est.probabilities["Draw"]


def _big_chase_match(day=3, total=4):
    """Yorkshire 469 & 246/6d, Warwickshire 263 & 44/1 — chasing 409 in a day."""
    return Match(
        match_id="bigchase", format=Format.FIRST_CLASS, phase=Phase.STUMPS,
        teams=[Team("Yorkshire", "YOR"), Team("Warwickshire", "WAR")],
        day_number=day, total_days=total,
        innings=[
            Innings("Yorkshire", "Warwickshire", 1, 469, 10, 130.0, all_out=True, closed=True),
            Innings("Warwickshire", "Yorkshire", 2, 263, 10, 80.0, all_out=True, closed=True),
            Innings("Yorkshire", "Warwickshire", 3, 246, 6, 60.0, declared=True, closed=True),
            Innings("Warwickshire", "Yorkshire", 4, 44, 1, 15.0),
        ],
    )


def test_test_heuristic_huge_fourth_innings_target_is_near_hopeless():
    # CricViz reference (Yorks v Warwicks, day-3 stumps): Yorks 70 / Draw 29 /
    # Warwicks 1. A 409 chase in a day is essentially bowl-out-or-draw, not a
    # real chance for the chasing side — and the bowling side is the favourite.
    est = estimate(_big_chase_match())
    p = est.probabilities
    assert p["Warwickshire"] < 0.05           # chasing 409 ~ hopeless
    assert p["Yorkshire"] > p["Draw"]         # bowling side favoured to bowl them out
    assert p["Yorkshire"] > 0.55
    assert abs(sum(p.values()) - 1.0) < 0.01


def _moderate_chase_match(day=3, total=4):
    """Middlesex 339 & 283/6d, Worcestershire 265 & 33/2 — chasing 325 in a day."""
    return Match(
        match_id="modchase", format=Format.FIRST_CLASS, phase=Phase.STUMPS,
        teams=[Team("Worcestershire", "WOR"), Team("Middlesex", "MID")],
        day_number=day, total_days=total,
        innings=[
            Innings("Middlesex", "Worcestershire", 1, 339, 10, 100.0, all_out=True, closed=True),
            Innings("Worcestershire", "Middlesex", 2, 265, 10, 85.0, all_out=True, closed=True),
            Innings("Middlesex", "Worcestershire", 3, 283, 6, 75.0, declared=True, closed=True),
            Innings("Worcestershire", "Middlesex", 4, 33, 2, 12.0),
        ],
    )


def test_test_heuristic_moderate_chase_still_favours_bowling_side():
    # CricViz reference (Worcs v Middx, day-3 stumps): Worcs 8 / Draw 11 /
    # Middx 81. A 325 chase looks gettable by required rate (~3.4/over) but is
    # historically hard; the spare time helps the bowlers, not the chaser.
    est = estimate(_moderate_chase_match())
    p = est.probabilities
    assert p["Worcestershire"] < 0.15        # chasing 325 ~ unlikely, not ~70%
    assert p["Middlesex"] > p["Worcestershire"]
    # Bowling side is the heavy favourite with a low draw, not a draw-dominant
    # stalemate: with only 8 wickets standing a full day is enough to bowl them
    # out (cf. the 9-wicket Yorkshire case, where the draw is far likelier).
    assert p["Middlesex"] > 0.65
    assert p["Draw"] < 0.20


def _third_innings_match(*, third_runs, third_wkts, day=3, total=4):
    """Real-shaped follow-on: Essex 401, Leicestershire 187 (follow-on), then
    batting again in the 3rd innings — a modest lead with the *opponent* to
    chase last, so the bowling side (Essex) is favoured, not the batting side."""
    return Match(
        match_id="md3", format=Format.FIRST_CLASS, phase=Phase.STUMPS,
        teams=[Team("Leicestershire", "LEI"), Team("Essex", "ESS")],
        day_number=day, total_days=total,
        innings=[
            Innings("Essex", "Leicestershire", 1, 401, 10, 97.4, all_out=True, closed=True),
            Innings("Leicestershire", "Essex", 2, 187, 10, 63.2, all_out=True, closed=True),
            Innings("Leicestershire", "Essex", 3, third_runs, third_wkts, 117.0),
        ],
    )


def test_test_heuristic_third_innings_modest_lead_favours_bowling_side():
    # The reported bug: a follow-on side leading by 112 with 3 wickets left at
    # stumps day 3 is NOT 76% to win — they're *setting* a small target for the
    # opponent to chase last. Essex (bowling side) must be the favourite.
    m = _third_innings_match(third_runs=326, third_wkts=7)  # lead 112, 3 wkts left
    est = estimate(m)
    assert est.method == "test-heuristic"
    p = est.probabilities
    assert p["Essex"] > p["Draw"] > p["Leicestershire"]
    assert p["Essex"] > 0.5
    assert p["Leicestershire"] < 0.15
    assert abs(sum(p.values()) - 1.0) < 0.01  # 3-dp rounding residual


def test_test_heuristic_third_innings_huge_lead_favours_batting_side():
    # A commanding third-innings lead flips it back: the target becomes
    # unchaseable and there's time to bowl the opponent out.
    m = _third_innings_match(third_runs=560, third_wkts=4, day=2, total=4)  # lead ~346
    est = estimate(m)
    p = est.probabilities
    assert p["Leicestershire"] > p["Essex"]


# -- innings 1-2 leans (real states from the Jun 2026 ENG v NZ 2nd Test) ----
# These pin the projected-first-innings-lead fix: the raw aggregate lead used to
# read a side still building its first innings as near-beaten. WinViz references
# captured live at each moment.


def _first_innings_match(*, bat_runs, bat_wkts, bat_overs, day=1, total=5):
    """New Zealand alone have batted (or are batting) first — one-sided card."""
    return Match(
        match_id="engnz1", format=Format.TEST, phase=Phase.LIVE,
        teams=[Team("New Zealand", "NZ"), Team("England", "ENG")],
        day_number=day, total_days=total,
        innings=[
            Innings("New Zealand", "England", 1, bat_runs, bat_wkts, bat_overs),
        ],
    )


def test_eng_nz_day1_first_innings_in_progress_is_a_coin_flip():
    # NZ 291/7, end of day 1 (WinViz ENG 53 / draw 2 / NZ 45). A still-building
    # first innings is a near coin-flip, not a draw-heavy stalemate.
    p = estimate(_first_innings_match(bat_runs=291, bat_wkts=7, bat_overs=77.0,
                                      day=1)).probabilities
    assert p["Draw"] < 0.05                       # was ~15% pre-fix
    assert 0.35 < p["New Zealand"] < 0.65         # neither side dominant
    assert 0.35 < p["England"] < 0.65


def test_eng_nz_first_innings_par_plus_total_leans_to_batting_side():
    # NZ 391 all out (WinViz ENG 41 / draw 3 / NZ 56). An above-par total earns
    # the batting side a modest lean — and the draw stays low with days left.
    p = estimate(_first_innings_match(bat_runs=391, bat_wkts=10, bat_overs=96.2,
                                      day=2)).probabilities
    assert p["Draw"] < 0.05
    assert p["New Zealand"] > p["England"]


def test_eng_nz_second_innings_trailing_is_not_a_near_loss():
    # THE regression: NZ 391 a.o.; England 118/2 in reply, ~273 behind but with
    # 8 wickets standing (WinViz ENG 49 / draw 3 / NZ 48). Pre-fix the raw
    # aggregate lead gave England *1%*; trailing in your own first innings is
    # expected, so this must read as ~level.
    m = Match(
        match_id="engnz2", format=Format.TEST, phase=Phase.LIVE,
        teams=[Team("New Zealand", "NZ"), Team("England", "ENG")],
        day_number=2, total_days=5,
        innings=[
            Innings("New Zealand", "England", 1, 391, 10, 96.2, all_out=True, closed=True),
            Innings("England", "New Zealand", 2, 118, 2, 27.0),
        ],
    )
    p = estimate(m).probabilities
    assert p["England"] > 0.40                    # was 0.01 pre-fix
    assert p["Draw"] < 0.05
    assert abs(p["England"] - p["New Zealand"]) < 0.20   # roughly level
    assert abs(sum(p.values()) - 1.0) < 0.01


# -- multi-day (Option B: model) --------------------------------------------


def _synthetic_test_match(outcome):
    deliveries = [{"runs": {"total": 1}} for _ in range(12)]
    overs = [{"over": i, "deliveries": deliveries} for i in range(8)]
    return {
        "info": {"match_type": "Test", "teams": ["England", "Australia"],
                 "outcome": outcome},
        "innings": [
            {"team": "England", "overs": overs},
            {"team": "Australia", "overs": overs},
            {"team": "England", "overs": overs},
            {"team": "Australia", "overs": overs},
        ],
    }


def test_multiday_rows_labels_and_width():
    won = multiday_rows_from_match(_synthetic_test_match({"winner": "England"}),
                                   sample_every=12)
    assert won and all(len(vec) == len(FEATURE_ORDER_MD) for vec, _ in won)
    # Labels are framed from the batting side: England innings -> 1, Australia -> 0.
    assert set(label for _, label in won) <= {0, 1}

    drawn = multiday_rows_from_match(_synthetic_test_match({"result": "draw"}),
                                     sample_every=12)
    assert drawn and all(label == 2 for _, label in drawn)

    tied = multiday_rows_from_match(_synthetic_test_match({"result": "tie"}))
    assert tied == []  # ambiguous -> skipped


def test_multiday_model_used_only_when_opted_in(tmp_path):
    pytest.importorskip("sklearn")
    from sklearn.ensemble import HistGradientBoostingClassifier
    import numpy as np

    # Tiny 3-class model over the multi-day feature space.
    rng = np.random.default_rng(0)
    X, y = [], []
    for _ in range(600):
        inns = rng.integers(1, 5)
        lead = rng.integers(-300, 300)
        wih = rng.integers(0, 11)
        overs = rng.integers(0, 400)
        is4 = 1.0 if inns == 4 else 0.0
        rtw = max(0, -lead + 1) if is4 else 0
        rrr = (rtw / overs) if (is4 and overs) else 0.0
        X.append([inns, lead, wih, overs, is4, rtw, min(rrr, 36)])
        # Label: draw when little time, else lead decides.
        y.append(2 if overs < 60 else (1 if lead > 0 else 0))
    model = HistGradientBoostingClassifier(max_iter=60).fit(np.array(X), np.array(y))

    path = tmp_path / "md.pkl"
    with path.open("wb") as fh:
        pickle.dump({"model": model, "order": list(FEATURE_ORDER_MD),
                     "classes": [0, 1, 2], "multiday": True}, fh)
    settings = Settings(cache_dir=tmp_path, winprob_multiday_model_path=path,
                        cricketdata_api_key=None)

    m = _fourth_innings_match()
    # Default: heuristic, even though the model exists.
    assert estimate(m, settings).method == "test-heuristic"
    # Opt in -> the trained model is used.
    est = estimate(m, settings, use_multiday_model=True)
    assert est.method == "multiday-model"
    assert set(est.probabilities) == {"Surrey", "Hampshire", "Draw"}
    assert abs(sum(est.probabilities.values()) - 1.0) < 0.05

"""Notification event detection (the pure diff; no actual desktop calls)."""

from stumps.models import Format, Innings, Match, Phase, Team
from stumps.notify import detect_events
from stumps.prioritise import classify
from stumps.options import Preferences


def _eng_match(match_id="m", wickets=2, phase=Phase.LIVE):
    return Match(
        match_id=match_id, format=Format.ODI, phase=phase,
        teams=[Team("England"), Team("Australia")],
        innings=[Innings("England", "Australia", 1, 150, wickets, 30.0)],
    )


def _ranked(match):
    # England is followed by default -> FOLLOWED tier, is_followed True.
    return [(match, classify(match, Preferences()))]


def test_first_sighting_is_baseline_only():
    events, state = detect_events({}, _ranked(_eng_match(wickets=2)))
    assert events == []  # no alert on first sight
    assert state["m"][0] == 2


def test_new_wicket_fires():
    _, state = detect_events({}, _ranked(_eng_match(wickets=2)))
    events, _ = detect_events(state, _ranked(_eng_match(wickets=3)))
    assert len(events) == 1 and "Wicket" in events[0].title


def test_no_wicket_no_event():
    _, state = detect_events({}, _ranked(_eng_match(wickets=2)))
    events, _ = detect_events(state, _ranked(_eng_match(wickets=2)))
    assert events == []


def test_result_fires_once():
    _, state = detect_events({}, _ranked(_eng_match(phase=Phase.LIVE)))
    done = _eng_match(phase=Phase.COMPLETE)
    done.result_text = "England won by 5 wickets"
    events, state2 = detect_events(state, _ranked(done))
    assert len(events) == 1 and events[0].title == "🏏 Result"
    assert "England won" in events[0].body
    # Already COMPLETE next time -> no repeat.
    events2, _ = detect_events(state2, _ranked(done))
    assert events2 == []


def test_only_followed_teams_notify():
    other = Match("x", Format.T20, phase=Phase.LIVE,
                  teams=[Team("Surrey"), Team("Kent")],
                  innings=[Innings("Surrey", "Kent", 1, 80, 1, 10.0)])
    _, state = detect_events({}, [(other, classify(other, Preferences()))])
    other.innings[0].wickets = 4
    events, _ = detect_events(state, [(other, classify(other, Preferences()))])
    assert events == []  # not a followed team

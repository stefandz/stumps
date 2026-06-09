"""Tests for match classification, ranking policy, and preference-driven filters."""

from stumps.models import Format, Match, Phase, Team
from stumps.options import Preferences
from stumps.prioritise import (
    Tier,
    classify,
    is_followed,
    is_home_domestic,
    is_icc_tournament,
    is_top_tier_test,
    prioritise,
)
from stumps.sources.fixtures import sample_matches


def _match(fmt, series, *team_names, phase=Phase.LIVE):
    return Match(
        match_id="t",
        format=fmt,
        series_name=series,
        teams=[Team(n) for n in team_names],
        phase=phase,
    )


def test_followed_team_detection():
    eng = ["england"]
    assert is_followed(_match(Format.ODI, "x", "England", "India"), eng)
    assert is_followed(_match(Format.WT20I, "x", "England Women", "Australia Women"), eng)
    assert not is_followed(_match(Format.T20, "x", "Surrey", "Kent"), eng)
    # Follow someone else entirely.
    assert is_followed(_match(Format.ODI, "x", "India", "England"), ["india"])
    assert not is_followed(_match(Format.ODI, "x", "India", "England"), [])


def test_top_tier_test_requires_two_full_members():
    assert is_top_tier_test(_match(Format.TEST, "x", "Australia", "England"))
    assert not is_top_tier_test(_match(Format.TEST, "x", "England", "Nepal"))
    assert not is_top_tier_test(_match(Format.ODI, "x", "Australia", "England"))


def test_icc_tournament_by_series_name():
    assert is_icc_tournament(_match(Format.ODI, "ICC Cricket World Cup 2027", "A", "B"))
    assert is_icc_tournament(_match(Format.ODI, "ICC Champions Trophy 2025", "A", "B"))
    assert not is_icc_tournament(_match(Format.ODI, "Bilateral ODI Series", "A", "B"))


def test_icc_warmups_and_qualifiers_are_not_premier():
    warmup = _match(Format.WT20I,
                    "ICC Womens T20 World Cup Warm-up Matches 2026", "A", "B")
    league2 = _match(Format.ODI,
                     "ICC Cricket World Cup League Two 2023-27", "Canada", "USA")
    assert not is_icc_tournament(warmup)
    assert not is_icc_tournament(league2)
    assert classify(warmup).tier is Tier.OTHER
    # ...unless you opt them back in.
    assert is_icc_tournament(warmup, include_warmups=True)


def test_finished_warmup_is_filtered_but_live_one_is_kept():
    live_warmup = _match(Format.WT20I, "ICC Womens T20 World Cup Warm-up Matches",
                         "Ireland Women", "Bangladesh Women", phase=Phase.LIVE)
    done_warmup = _match(Format.WT20I, "ICC Womens T20 World Cup Warm-up Matches",
                         "New Zealand Women", "Bangladesh Women", phase=Phase.COMPLETE)
    ranked = prioritise([live_warmup, done_warmup])
    assert live_warmup in [m for m, _ in ranked]
    assert done_warmup not in [m for m, _ in ranked]


def test_home_domestic_england_india_australia():
    assert is_home_domestic(_match(Format.FIRST_CLASS, "County Championship", "Surrey", "Kent"))
    assert is_home_domestic(_match(Format.T20, "Vitality Blast", "Somerset", "Sussex"))
    assert not is_home_domestic(_match(Format.TEST, "The Ashes", "England", "Australia"))
    # Other countries' scenes.
    ipl = _match(Format.T20, "Indian Premier League", "Mumbai Indians", "Chennai Super Kings")
    assert is_home_domestic(ipl, "india")
    assert not is_home_domestic(ipl, "england")
    bbl = _match(Format.T20, "Big Bash League", "Sydney Sixers", "Perth Scorchers")
    assert is_home_domestic(bbl, "australia")
    assert is_home_domestic(ipl, None) is False


def test_second_xi_and_academy_excluded_from_domestic():
    second_xi = _match(Format.FIRST_CLASS, "Second Eleven Championship",
                       "Derbyshire 2nd XI", "Sussex 2nd XI")
    assert not is_home_domestic(second_xi)
    assert classify(second_xi).tier is Tier.OTHER


def test_classification_tiers():
    assert classify(_match(Format.ODI, "ICC Cricket World Cup", "England", "India")).tier is Tier.FOLLOWED
    assert classify(_match(Format.TEST, "Border-Gavaskar", "India", "Australia")).tier is Tier.PREMIER
    assert classify(_match(Format.T20, "Vitality Blast", "Surrey", "Kent")).tier is Tier.HOME_DOMESTIC
    assert classify(_match(Format.ODI, "Bilateral", "Nepal", "USA")).tier is Tier.OTHER


def test_following_a_different_team_reprioritises():
    # An India fan: India v NZ World Cup is FOLLOWED, England's game is just premier.
    prefs = Preferences(followed_teams=["india"], domestic="india")
    ind = _match(Format.ODI, "ICC Cricket World Cup", "India", "New Zealand")
    eng = _match(Format.TEST, "The Ashes", "England", "Australia")
    assert classify(ind, prefs).tier is Tier.FOLLOWED
    assert classify(eng, prefs).tier is Tier.PREMIER


def test_full_priority_order_on_sample():
    ranked = prioritise(sample_matches())
    tiers = [cls.tier for _, cls in ranked]
    assert tiers == sorted(tiers)
    assert ranked[0][1].tier is Tier.FOLLOWED


def test_filter_live_only():
    ranked = prioritise(sample_matches(), Preferences(live_only=True))
    assert all(m.phase.is_active_today for m, _ in ranked)
    assert not any(m.phase is Phase.COMPLETE for m, _ in ranked)


def test_filter_by_format():
    prefs = Preferences(formats={Format.WT20I, Format.T20I, Format.T20})
    ranked = prioritise(sample_matches(), prefs)
    assert ranked  # something matches
    assert all(m.format in prefs.formats for m, _ in ranked)


def test_filter_womens_only():
    ranked = prioritise(sample_matches(), Preferences(gender="women"))
    assert ranked
    assert all("women" in (m.series_name + " ".join(m.team_names)).lower()
               for m, _ in ranked)


def test_limit_applied():
    ranked = prioritise(sample_matches(), Preferences(limit=2))
    assert len(ranked) == 2

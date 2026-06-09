"""Tests for match classification and ranking policy."""

from stumps.models import Format, Match, Phase, Team
from stumps.prioritise import (
    Tier,
    classify,
    is_england_match,
    is_english_domestic,
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


def test_england_men_and_women_detected():
    assert is_england_match(_match(Format.ODI, "x", "England", "India"))
    assert is_england_match(_match(Format.WT20I, "x", "England Women", "Australia Women"))
    assert not is_england_match(_match(Format.T20, "x", "Surrey", "Kent"))


def test_top_tier_test_requires_two_full_members():
    assert is_top_tier_test(_match(Format.TEST, "x", "Australia", "England"))
    # A Test against a non-full-member is not "top tier".
    assert not is_top_tier_test(_match(Format.TEST, "x", "England", "Nepal"))
    # An ODI between full members is not a Test.
    assert not is_top_tier_test(_match(Format.ODI, "x", "Australia", "England"))


def test_icc_tournament_by_series_name():
    assert is_icc_tournament(_match(Format.ODI, "ICC Cricket World Cup 2027", "A", "B"))
    assert is_icc_tournament(_match(Format.T20I, "ICC Men's T20 World Cup", "A", "B"))
    assert is_icc_tournament(_match(Format.ODI, "ICC Champions Trophy 2025", "A", "B"))
    assert not is_icc_tournament(_match(Format.ODI, "Bilateral ODI Series", "A", "B"))


def test_icc_warmups_and_qualifiers_are_not_premier():
    # Real series names from a live run — World-Cup-named but not the event.
    warmup = _match(Format.WT20I,
                    "ICC Womens T20 World Cup Warm-up Matches 2026", "A", "B")
    league2 = _match(Format.ODI,
                     "ICC Cricket World Cup League Two 2023-27", "Canada", "USA")
    assert not is_icc_tournament(warmup)
    assert not is_icc_tournament(league2)
    assert classify(warmup).tier is Tier.OTHER
    assert classify(league2).tier is Tier.OTHER


def test_finished_warmup_is_filtered_but_live_one_is_kept():
    live_warmup = _match(Format.WT20I, "ICC Womens T20 World Cup Warm-up Matches",
                         "Ireland Women", "Bangladesh Women", phase=Phase.LIVE)
    done_warmup = _match(Format.WT20I, "ICC Womens T20 World Cup Warm-up Matches",
                         "New Zealand Women", "Bangladesh Women", phase=Phase.COMPLETE)
    ranked = prioritise([live_warmup, done_warmup])
    ids = [m.series_name for m, _ in ranked]
    # The live international warm-up still shows; the finished one is dropped.
    assert live_warmup in [m for m, _ in ranked]
    assert done_warmup not in [m for m, _ in ranked]


def test_english_domestic_by_team_and_series():
    assert is_english_domestic(_match(Format.FIRST_CLASS, "County Championship", "Surrey", "Kent"))
    assert is_english_domestic(_match(Format.T20, "Vitality Blast", "Somerset", "Sussex"))
    # International games are never "English domestic" even if England plays.
    assert not is_english_domestic(_match(Format.TEST, "The Ashes", "England", "Australia"))


def test_second_xi_and_academy_excluded_from_domestic():
    # Real noise from the live feed: "Derbyshire 2nd XI" matches "derbyshire"
    # as a substring but isn't the professional game.
    second_xi = _match(Format.FIRST_CLASS, "Second Eleven Championship",
                       "Derbyshire 2nd XI", "Sussex 2nd XI")
    academy = _match(Format.FIRST_CLASS, "West Indies Academy tour of Sri Lanka",
                     "Sri Lanka A", "West Indies Academy")
    assert not is_english_domestic(second_xi)
    assert classify(second_xi).tier is Tier.OTHER
    assert classify(academy).tier is Tier.OTHER
    # The real county still counts.
    real = _match(Format.FIRST_CLASS, "County Championship", "Surrey", "Kent")
    assert is_english_domestic(real)


def test_classification_tiers():
    assert classify(_match(Format.ODI, "ICC Cricket World Cup", "England", "India")).tier is Tier.ENGLAND
    assert classify(_match(Format.TEST, "Border-Gavaskar", "India", "Australia")).tier is Tier.PREMIER
    assert classify(_match(Format.ODI, "ICC Cricket World Cup", "India", "NZ")).tier is Tier.PREMIER
    assert classify(_match(Format.T20, "Vitality Blast", "Surrey", "Kent")).tier is Tier.ENGLISH_DOMESTIC
    assert classify(_match(Format.ODI, "Bilateral", "Nepal", "USA")).tier is Tier.OTHER


def test_full_priority_order_on_sample():
    ranked = prioritise(sample_matches())
    tiers = [cls.tier for _, cls in ranked]
    # Tiers must be non-decreasing: England, then premier, then domestic, then other.
    assert tiers == sorted(tiers)
    # England (men's Test at stumps OR women's T20I live) must be first.
    first_match, first_cls = ranked[0]
    assert first_cls.tier is Tier.ENGLAND


def test_other_tier_only_kept_when_live_international():
    # Bangladesh v Zimbabwe ODI is 'other' but live+international -> kept.
    ranked = prioritise(sample_matches())
    ids = {m.match_id for m, _ in ranked}
    assert "demo-ban-zim-odi" in ids


def test_live_sorts_before_stumps_within_tier():
    # England women's T20I (live) should rank above England men's Test (stumps).
    ranked = prioritise(sample_matches())
    england = [m for m, c in ranked if c.tier is Tier.ENGLAND]
    assert england[0].phase is Phase.LIVE
    assert england[0].match_id == "demo-engw-ausw-t20i"

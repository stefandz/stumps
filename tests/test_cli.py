"""CLI helpers (no network)."""

from stumps.cli import _find_match, _normalise
from stumps.models import Format, Match, Team


def _m(a, b, series=""):
    return Match("x", Format.ODI, [Team(a), Team(b)], series_name=series)


def test_normalise_vs_separator():
    assert _normalise("Bangladesh vs Australia") == "bangladesh v australia"
    assert _normalise("Bangladesh vs. Australia") == "bangladesh v australia"
    assert _normalise("Bangladesh v Australia") == "bangladesh v australia"


def test_find_match_accepts_v_and_vs():
    matches = [_m("Bangladesh", "Australia"), _m("England", "India")]
    for q in ("Bangladesh v Australia", "Bangladesh vs Australia",
              "Bangladesh vs. Australia", "bangladesh vs australia"):
        assert _find_match(matches, q) is matches[0]
    # Single-team substring still works; unknown returns None.
    assert _find_match(matches, "england") is matches[1]
    assert _find_match(matches, "South Africa vs Kenya") is None


def test_find_match_by_series():
    m = _m("A", "B", series="Australia vs Bangladesh ODI Series")
    assert _find_match([m], "australia v bangladesh") is m

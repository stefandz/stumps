"""Source normalisers and aggregator fallback (no network)."""

import pytest

from stumps.config import Settings
from stumps.models import Format, Phase
from stumps.sources.aggregator import Aggregator
from stumps.sources.base import SourceError
from stumps.sources.cricinfo import CricinfoSource, _parse_score
from stumps.sources.cricketdata import CricketDataSource
from stumps.sources.fixtures import DemoSource


@pytest.fixture
def settings(tmp_path):
    return Settings(cache_dir=tmp_path, cricketdata_api_key=None)


# -- score parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        ("180/4", (180, 4, False, False)),
        ("250/8d", (250, 8, True, False)),
        ("425", (425, 10, False, True)),
        ("312/10", (312, 10, False, False)),
    ],
)
def test_parse_score(score, expected):
    assert _parse_score(score) == expected


# -- cricketdata format detection ------------------------------------------


def test_cricketdata_format_international_vs_domestic(settings):
    src = CricketDataSource(settings)
    # Two nations + test -> Test.
    assert src._format("test", ["England", "Australia"], "The Ashes") is Format.TEST
    # Two counties + test -> first-class (domestic).
    assert src._format("test", ["Surrey", "Kent"], "County Championship") is Format.FIRST_CLASS
    # Women's international T20.
    assert src._format("t20", ["England Women", "India Women"], "Women's T20I") is Format.WT20I
    # Domestic T20.
    assert src._format("t20", ["Somerset", "Kent"], "Vitality Blast") is Format.T20


def test_cricketdata_is_international(settings):
    src = CricketDataSource(settings)
    assert src._is_international(["India", "New Zealand"])
    assert not src._is_international(["Surrey", "Kent"])


def test_cricketdata_requires_key(settings):
    src = CricketDataSource(settings)
    assert not src.available
    with pytest.raises(SourceError):
        src.fetch_current_matches()


# -- cricinfo normaliser ----------------------------------------------------


def test_cricinfo_normalise_summary(settings):
    src = CricinfoSource(settings)
    raw = {
        "objectId": 12345,
        "series": {"objectId": 999, "name": "ICC Cricket World Cup 2027"},
        "internationalClassId": 2,  # ODI
        "statusText": "India need 71 runs",
        "state": "LIVE",
        "ground": {"name": "Eden Gardens"},
        "teams": [
            {"team": {"objectId": 1, "longName": "India", "abbreviation": "IND"},
             "score": "210/4"},
            {"team": {"objectId": 2, "longName": "New Zealand", "abbreviation": "NZ"},
             "score": "280/8"},
        ],
    }
    match = src._normalise_summary(raw)
    assert match.match_id == "12345"
    assert match.series_id == "999"
    assert match.format is Format.ODI
    assert match.phase is Phase.LIVE
    assert match.team_names == ["India", "New Zealand"]
    assert match.venue == "Eden Gardens"
    assert len(match.innings) == 2
    assert match.innings[0].runs == 210 and match.innings[0].wickets == 4


def test_cricinfo_phase_detects_stumps_and_breaks(settings):
    src = CricinfoSource(settings)
    assert src._phase({"state": "LIVE", "statusText": "Stumps, Day 2"}) is Phase.STUMPS
    assert src._phase({"state": "LIVE", "statusText": "Tea Break"}) is Phase.BREAK
    assert src._phase({"state": "POST", "statusText": "India won by 5 wickets"}) is Phase.COMPLETE


# -- aggregator fallback ----------------------------------------------------


def test_aggregator_falls_back_to_demo(settings, monkeypatch):
    # Force the live source to fail; no cricketdata key -> should land on demo.
    monkeypatch.setattr(
        CricinfoSource, "fetch_current_matches",
        lambda self: (_ for _ in ()).throw(SourceError("blocked")),
    )
    agg = Aggregator(settings)
    result = agg.fetch()
    assert result.used_fallback is True
    assert result.source.name == "demo"
    assert len(result.matches) > 0
    assert any("cricinfo unavailable" in n for n in result.notices)


def test_aggregator_demo_only_mode(settings):
    agg = Aggregator(settings, demo_only=True)
    result = agg.fetch()
    assert result.source.name == "demo"
    assert not result.used_fallback


def test_demo_source_has_england_and_chase(settings):
    matches = DemoSource(settings).fetch_current_matches()
    assert any("England" in t for m in matches for t in m.team_names)
    # At least one limited-overs chase (target set) for DLS/win-prob.
    assert any(
        inns.target for m in matches for inns in m.innings if inns.target
    )

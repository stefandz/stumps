"""Source normalisers and aggregator fallback (no network)."""

import pytest

from stumps.config import Settings
from stumps.models import Format, Phase
from stumps.sources.aggregator import Aggregator
from stumps.sources.base import SourceError
from stumps.sources.cricketdata import CricketDataSource
from stumps.sources.espn import EspnSource, parse_score
from stumps.sources.fixtures import DemoSource


@pytest.fixture
def settings(tmp_path):
    return Settings(cache_dir=tmp_path, cricketdata_api_key=None)


# -- score parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        # runs, wkts, overs, target, declared, all_out
        ("180/4", (180, 4, 0.0, 0, False, False)),
        ("250/8d", (250, 8, 0.0, 0, True, False)),
        ("425", (425, 10, 0.0, 0, False, True)),
        ("174/6 (49.2 ov)", (174, 6, 49.2, 0, False, False)),
        ("191/9 (42.2/50 ov, target 285)", (191, 9, 42.2, 285, False, False)),
        ("421 & 50/2", (50, 2, 0.0, 0, False, False)),  # multi-day: current seg
    ],
)
def test_parse_score(score, expected):
    assert parse_score(score) == expected


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


def test_cricketdata_enrich_preserves_summary_totals(settings):
    # Reproduces the real bug: cricapi scorecard objects carry batting/bowling
    # but NO totals (those are in the separate 'score' array). enrich() must
    # merge figures WITHOUT zeroing the summary's runs/wickets/overs.
    src = CricketDataSource(settings)
    raw_summary = {
        "id": "m1",
        "name": "England Women vs Australia Women",
        "matchType": "t20",
        "status": "England Women need 50 runs in 30 balls",
        "matchStarted": True,
        "matchEnded": False,
        "teams": ["England Women", "Australia Women"],
        "score": [
            {"r": 165, "w": 6, "o": 20.0, "inning": "Australia Women Inning 1"},
            {"r": 116, "w": 3, "o": 15.0, "inning": "England Women Inning 1"},
        ],
    }
    match = src._normalise(raw_summary)
    assert [(i.runs, i.wickets) for i in match.innings] == [(165, 6), (116, 3)]

    scorecard_payload = {
        "data": {
            "score": raw_summary["score"],
            "scorecard": [
                {"inning": "Australia Women Inning 1", "batting": [], "bowling": []},
                {
                    "inning": "England Women Inning 1",
                    "batting": [{"batsman": {"name": "N Sciver-Brunt"}, "r": 44,
                                 "b": 31, "4s": 5, "6s": 1, "dismissal-text": "batting"}],
                    "bowling": [{"bowler": {"name": "A Gardner"}, "o": 3, "m": 0,
                                 "r": 19, "w": 1}],
                },
            ],
        }
    }
    src._get = lambda endpoint, params: scorecard_payload  # type: ignore[assignment]
    src.enrich(match)
    # Totals preserved...
    assert [(i.runs, i.wickets) for i in match.innings] == [(165, 6), (116, 3)]
    # ...and figures merged in.
    assert match.innings[1].batters[0].name == "N Sciver-Brunt"
    assert match.innings[1].bowlers[0].wickets == 1


def test_cricketdata_normalises_mangled_team_labels(settings):
    src = CricketDataSource(settings)
    raw = {
        "id": "m2", "name": "Sri Lanka Women vs Pakistan Women", "matchType": "t20",
        "status": "Sri Lanka Women won", "matchStarted": True, "matchEnded": True,
        "teams": ["Sri Lanka Women", "Pakistan Women"],
        "score": [
            {"r": 150, "w": 4, "o": 20.0, "inning": "sri lanka women Inning 1"},
            {"r": 120, "w": 9, "o": 20.0, "inning": "Sri Lanka Women,Pakistan Women Inning 1"},
        ],
    }
    match = src._normalise(raw)
    # Lower-cased and comma-mangled labels resolve to canonical team names.
    assert match.innings[0].batting_team == "Sri Lanka Women"
    assert match.innings[1].batting_team == "Pakistan Women"


def test_cricketdata_requires_key(settings):
    src = CricketDataSource(settings)
    assert not src.available
    with pytest.raises(SourceError):
        src.fetch_current_matches()


# -- ESPN normaliser (real scoreboard / summary shapes) ---------------------


def _espn_event():
    return {
        "id": "1532480",
        "name": "Bangladesh v Australia",
        "summary": "Live",
        "eventType": "ODI",
        "location": "Dhaka",
        "class": {"internationalClassId": "2", "generalClassCard": "ODI",
                  "eventType": "ODI"},
        "fullStatus": {"type": {"state": "in", "detail": "Live"}},
        "competitors": [
            {"team": {"displayName": "Bangladesh", "abbreviation": "BAN", "id": "1"},
             "score": "284/8"},
            {"team": {"displayName": "Australia", "abbreviation": "AUS", "id": "2"},
             "score": "191/9 (42.2/50 ov, target 285)"},
        ],
    }


def test_espn_event_to_match(settings):
    src = EspnSource(settings)
    match = src._event_to_match(_espn_event(), "24324", "Australia tour of Bangladesh")
    assert match.match_id == "1532480"
    assert match.series_id == "24324"
    assert match.format is Format.ODI
    assert match.phase is Phase.LIVE
    assert match.team_names == ["Bangladesh", "Australia"]
    assert match.venue == "Dhaka"
    assert match.innings[0].runs == 284 and match.innings[0].wickets == 8
    assert match.innings[1].runs == 191 and match.innings[1].target == 285


def test_espn_format_from_class():
    src = EspnSource(Settings(cricketdata_api_key=None))
    assert src._format({"internationalClassId": "1"}, "Test") is Format.TEST
    assert src._format({"internationalClassId": "10"}, "T20") is Format.WT20I
    assert src._format({"internationalClassId": "0", "generalClassCard": "First-class"}, "Test") is Format.FIRST_CLASS
    assert src._format({"internationalClassId": "0", "generalClassCard": "Twenty20"}, "T20") is Format.T20


def test_espn_phase_from_status():
    src = EspnSource(Settings(cricketdata_api_key=None))
    assert src._phase({"fullStatus": {"type": {"state": "in"}}, "summary": "Stumps Day 2"}) is Phase.STUMPS
    assert src._phase({"fullStatus": {"type": {"state": "post", "detail": "won"}}}) is Phase.COMPLETE
    assert src._phase({"fullStatus": {"type": {"state": "pre"}}}) is Phase.UPCOMING


def test_espn_completed_prefers_result_detail(settings):
    # ESPN's `summary` for a finished game is often the bare label "Result";
    # the real outcome lives in fullStatus.type.detail.
    src = EspnSource(settings)
    event = _espn_event()
    event["summary"] = "Result"
    event["fullStatus"] = {"type": {"state": "post",
                                    "detail": "England Women won by 5 runs"}}
    match = src._event_to_match(event, "1", "Series")
    assert match.phase is Phase.COMPLETE
    assert match.result_text == "England Women won by 5 runs"
    assert match.status_text == "England Women won by 5 runs"


def test_espn_completed_captures_winner_flag(settings):
    src = EspnSource(settings)
    # A win: one competitor flagged winner.
    won = _espn_event()
    won["fullStatus"] = {"type": {"state": "post", "detail": "Final"}}
    won["competitors"][0]["winner"] = True
    m = src._event_to_match(won, "1", "Series")
    assert m.phase is Phase.COMPLETE
    assert m.winner == "Bangladesh"
    # A draw: neither competitor flagged winner.
    drawn = _espn_event()
    drawn["fullStatus"] = {"type": {"state": "post", "detail": "Final"}}
    for c in drawn["competitors"]:
        c["winner"] = False
    assert src._event_to_match(drawn, "1", "Series").winner == ""


def test_espn_points_from_notes(settings):
    from stumps.models import Format, Match, Phase, Team
    src = EspnSource(settings)
    m = Match("p", Format.FIRST_CLASS, [Team("Surrey"), Team("Hampshire")],
              phase=Phase.COMPLETE)
    # Real shape, plus a provisional asterisk that should be stripped.
    src._apply_points(m, {"notes": [
        {"type": "toss", "text": "Hampshire elected to field"},
        {"type": "points", "text": "Surrey 15*, Hampshire 13*"},
    ]})
    assert m.points == "Surrey 15, Hampshire 13"
    # Bilateral series (no points note) -> stays empty.
    m2 = Match("p2", Format.ODI, [Team("A"), Team("B")], phase=Phase.COMPLETE)
    src._apply_points(m2, {"notes": [{"type": "toss", "text": "A elected to bat"}]})
    assert m2.points == ""


def test_espn_multiday_timing_from_notes(settings):
    from stumps.models import Format, Match, Phase, Team
    src = EspnSource(settings)
    match = Match(match_id="m", format=Format.FIRST_CLASS, phase=Phase.LIVE,
                  teams=[Team("Surrey"), Team("Hampshire")])
    summary = {
        "header": {"competitions": [{"status": {"presentLocalTime": "16:30"}}]},
        "notes": [
            {"type": "hoursofplay",
             "text": "10.00 start, Lunch 12.00-12.40, Tea 14.40-15.00, Close 17.00"},
            {"type": "matchdays", "text": "7,8,9,10 June 2026 (4-day match)"},
            {"type": "closeofplay", "text": "day 1 - ..."},
            {"type": "closeofplay", "text": "day 2 - ..."},
            {"type": "closeofplay", "text": "day 3 - ..."},
        ],
    }
    src._apply_multiday_timing(match, summary)
    assert match.local_time == "16:30"
    assert match.close_time == "17:00"
    assert match.total_days == 4
    assert match.day_number == 4  # 3 completed days -> day 4 in progress


def test_espn_completed_falls_back_to_summary(settings):
    # If detail is absent, keep whatever summary we have rather than blanking it.
    src = EspnSource(settings)
    event = _espn_event()
    event["summary"] = "South Africa won by 4 wickets"
    event["fullStatus"] = {"type": {"state": "post"}}
    match = src._event_to_match(event, "1", "Series")
    assert match.result_text == "South Africa won by 4 wickets"


def test_espn_innings_from_summary_linescores():
    src = EspnSource(Settings(cricketdata_api_key=None))
    summary = {
        "header": {"competitions": [{"competitors": [
            {"team": {"displayName": "Bangladesh"}, "score": "284/8", "linescores": [
                {"period": 1, "runs": 284, "wickets": 8, "overs": 50.0,
                 "isCurrent": 0, "statistics": {"categories": [{}]}}]},
            {"team": {"displayName": "Australia"}, "score": "191/9 (target 285)",
             "linescores": [
                {"period": 1, "runs": 0, "wickets": 0, "overs": 50.0},
                {"period": 2, "runs": 191, "wickets": 9, "overs": 42.2,
                 "isCurrent": 1, "target": 285, "statistics": {"categories": [{}]}}]},
        ]}]},
        "rosters": [],
    }
    innings = src._innings_from_summary(summary)
    assert [(i.batting_team, i.runs, i.number) for i in innings] == [
        ("Bangladesh", 284, 1), ("Australia", 191, 2)]
    assert innings[0].closed is True
    assert innings[1].closed is False and innings[1].target == 285


def test_espn_ball_parsing():
    wicket = EspnSource._ball({
        "over": {"number": 37, "ball": 2}, "scoreValue": 0,
        "playType": {"description": "out"}, "dismissal": {"type": "caught"},
        "shortText": "Boult to Pant, OUT", "period": 2,
    })
    assert wicket.over == "37.2" and wicket.is_wicket and not wicket.is_boundary
    four = EspnSource._ball({
        "over": {"number": 38, "ball": 0}, "scoreValue": 4,
        "playType": {"description": "four"}, "shortText": "Boult to Kohli, FOUR",
        "period": 2,
    })
    assert four.is_boundary and four.runs == 4 and not four.is_wicket


def test_espn_recent_balls_newest_first(settings):
    src = EspnSource(settings)
    pages = {
        1: {"commentary": {"pageCount": 2, "items": [
            {"over": {"number": 1, "ball": 1}, "shortText": "ball 1"}]}},
        2: {"commentary": {"pageCount": 2, "items": [
            {"over": {"number": 40, "ball": i}, "shortText": f"ball 40.{i}"}
            for i in range(1, 5)]}},
    }
    src._get = lambda url: pages[1 if "page=1" in url else 2]  # type: ignore[assignment]
    balls = src._recent_balls("e", limit=3)
    assert [b.over for b in balls] == ["40.4", "40.3", "40.2"]  # newest first, capped


# -- aggregator fallback ----------------------------------------------------


def test_aggregator_falls_back_to_demo(settings, monkeypatch):
    # Force the live source to fail; no cricketdata key -> should land on demo.
    monkeypatch.setattr(
        EspnSource, "fetch_current_matches",
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


def test_espn_fetch_recent_results(settings, monkeypatch):
    src = EspnSource(settings)

    def fake_get(url):
        assert "dates=" in url  # only the dated header is queried here
        done = _espn_event()
        done["id"] = "111"
        done["fullStatus"] = {"type": {"state": "post", "detail": "Final"}}
        done["competitors"][0]["winner"] = True
        live = _espn_event()
        live["id"] = "222"  # state "in" (default) -> not a result
        return {"sports": [{"leagues": [
            {"id": "9", "name": "Series", "events": [done, live]}]}]}

    monkeypatch.setattr(src, "_get", fake_get)
    out = src.fetch_recent_results(2)
    assert {m.match_id for m in out} == {"111"}  # only the finished match
    assert out[0].finished_on  # stamped with a date
    assert src.fetch_recent_results(0) == []  # disabled


def test_aggregator_merges_recent_without_duplicates():
    from stumps.models import Match, Team

    live = [Match("a", Format.ODI, [Team("X"), Team("Y")], phase=Phase.LIVE)]
    recent = [
        Match("a", Format.ODI, [Team("X"), Team("Y")], phase=Phase.COMPLETE),
        Match("b", Format.TEST, [Team("P"), Team("Q")], phase=Phase.COMPLETE),
    ]

    class _Src:
        def fetch_recent_results(self, days):
            return recent

    merged = Aggregator._with_recent(_Src(), live, 2)
    # Live "a" is kept (freshest); the recent duplicate is dropped; "b" is added.
    assert [m.match_id for m in merged] == ["a", "b"]
    assert merged[0].phase is Phase.LIVE


def test_datasource_recent_default_is_empty(settings):
    assert DemoSource(settings).fetch_recent_results(5) == []


def test_demo_source_has_england_and_chase(settings):
    matches = DemoSource(settings).fetch_current_matches()
    assert any("England" in t for m in matches for t in m.team_names)
    # At least one limited-overs chase (target set) for DLS/win-prob.
    assert any(
        inns.target for m in matches for inns in m.innings if inns.target
    )

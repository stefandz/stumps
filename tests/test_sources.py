"""Source normalisers and aggregator fallback (no network)."""

from datetime import date

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


def test_cricketdata_sets_start_time(settings):
    src = CricketDataSource(settings)
    raw = {"id": "u", "name": "Upcoming Match", "matchType": "t20",
           "status": "Match not started", "teams": ["A", "B"],
           "dateTimeGMT": "2026-06-20T14:00:00", "matchStarted": False}
    m = src._normalise(raw)
    assert m.starts_at == "2026-06-20T14:00:00" and m.phase is Phase.UPCOMING


def test_norm_name():
    from stumps.sources.cricketdata import norm_name
    assert norm_name("  Beth   Mooney ") == "beth mooney"
    assert norm_name(None) == ""


def test_cricketdata_dismissal_texts(settings, monkeypatch):
    src = CricketDataSource(settings)
    monkeypatch.setattr(src, "api_key", "k")  # make .available true

    def fake_get(endpoint, params, ttl=None):
        if endpoint == "currentMatches":
            return {"data": [{"id": "X", "teams": ["Australia Women", "West Indies Women"],
                              "dateTimeGMT": "2026-06-10T14:00:00"}]}
        if endpoint == "match_scorecard":
            assert params["id"] == "X"
            return {"data": {"scorecard": [{"batting": [
                {"batsman": {"name": "Qiana Joseph"}, "dismissal-text": "c Mooney b Hamilton"},
                {"batsman": {"name": "Hayley Matthews"}, "dismissal-text": "not out"},
            ]}]}}
        return {"data": []}

    monkeypatch.setattr(src, "_get", fake_get)
    texts = src.dismissal_texts(["West Indies Women", "Australia Women"],
                                "2026-06-10T14:00:00Z")
    assert texts == {"qiana joseph": "c Mooney b Hamilton"}  # not-out excluded


def test_aggregator_augment_upgrades_dismissals(settings, monkeypatch):
    from stumps.models import Batter, Format, Innings, Match, Phase, Team
    agg = Aggregator(Settings(cache_dir=settings.cache_dir, cricketdata_api_key="k"))
    cd = next(s for s in agg.sources if isinstance(s, CricketDataSource))
    monkeypatch.setattr(cd, "dismissal_texts",
                        lambda teams, date, *, scorecard_ttl=None:
                        {"qiana joseph": "c Mooney b Hamilton"})
    m = Match("m", Format.WT20I, [Team("Australia Women"), Team("West Indies Women")],
              phase=Phase.COMPLETE,
              innings=[Innings("West Indies Women", "Australia Women", 1, 120, 5, 20.0,
                  batters=[Batter("Qiana Joseph", 5, 6, 1, 0, not_out=False, dismissal="caught wk"),
                           Batter("Hayley Matthews", 40, 30, 5, 1, not_out=True)])])
    agg.augment(m)
    assert m.innings[0].batters[0].dismissal == "c Mooney b Hamilton"  # upgraded
    assert m.innings[0].batters[1].dismissal is None  # not-out untouched


def test_aggregator_augment_silent_without_key(settings):
    from stumps.models import Batter, Format, Innings, Match, Phase, Team
    agg = Aggregator(settings)  # no cricketdata key
    m = Match("m", Format.WT20I, [Team("A"), Team("B")], phase=Phase.COMPLETE,
              innings=[Innings("A", "B", 1, 5, 1, 1.0,
                  batters=[Batter("X", 0, 1, 0, 0, not_out=False, dismissal="caught")])])
    agg.augment(m)  # no key -> no-op, no error
    assert m.innings[0].batters[0].dismissal == "caught"


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


def test_espn_dismissal_card_expansion():
    from stumps.sources.espn import _expand_card
    assert _expand_card("c", 0) == "caught"
    assert _expand_card("c", 1) == "caught wk"   # fielderKeeper -> behind
    assert _expand_card("b", 0) == "bowled"
    assert _expand_card("lbw", 0) == "lbw"
    assert _expand_card("run out", 0) == "run out"
    assert _expand_card("st", 0) == "stumped"
    # Empties / not-out are handled via the `outs` stat, not the card.
    assert _expand_card("", 0) == "" and _expand_card("0", 0) == ""
    assert _expand_card("not out", 0) == ""


def test_espn_dismissals_from_matchcards(settings):
    src = EspnSource(settings)
    dis = src._dismissals({"matchcards": [
        {"headline": "Batting", "inningsNumber": "2", "playerDetails": [
            {"playerID": "p1", "dismissal": "caught wk"},
            {"playerID": "p2", "dismissal": "not out"},
        ]},
        {"headline": "Bowling", "inningsNumber": "2",
         "playerDetails": [{"playerID": "x", "dismissal": ""}]},
    ]})
    assert dis == {(2, "p1"): "caught wk", (2, "p2"): "not out"}  # batting card only


def test_espn_partnerships_from_linescore(settings):
    # Per-innings partnerships live on the linescore (every innings), not the
    # matchcards card (latest innings only).
    src = EspnSource(settings)
    ls = {"period": 1, "partnerships": [
        {"wicketName": "1st", "runs": 0, "overs": 0.2,
         "batsmen": [{"athlete": {"displayName": "Sarkar"}},
                     {"athlete": {"displayName": "Hasan"}}]},
        {"wicketName": "2nd", "runs": 86, "overs": 15.3,
         "batsmen": [{"athlete": {"displayName": "Sarkar"}},
                     {"athlete": {"displayName": "Shanto"}}]},
    ]}
    p = src._partnerships(ls)
    assert len(p) == 2
    assert p[1].runs == 86 and p[1].wicket == "2nd" and p[1].batter2 == "Shanto"
    assert src._partnerships({}) == []


def _innings_for_partnership_inference():
    from stumps.models import Batter, FallOfWicket, Innings, Partnership
    # Batting order != dismissal order: Ingram bats #5 but falls before #4
    # Kellaway. Reconstruction must follow the FoW names, not the order.
    order = ["Zain", "Tribe", "Carlson", "Kellaway", "Ingram", "Dickson", "Cooke"]
    return Innings(
        "Glamorgan", "Sussex", 1, 96, 5, 31.2,
        batters=[Batter(name=n) for n in order],
        fall_of_wickets=[
            FallOfWicket(1, 5, "1.1", "Zain"),
            FallOfWicket(2, 11, "4.6", "Tribe"),
            FallOfWicket(3, 42, "12.5", "Carlson"),
            FallOfWicket(4, 43, "13.4", "Ingram"),
            FallOfWicket(5, 94, "29.3", "Kellaway"),
        ],
        # Feed gave the stands but left the batters blank (empty athlete objects).
        partnerships=[Partnership(w, r, o) for w, r, o in [
            ("1st", 5, "1.1"), ("2nd", 6, "3.5"), ("3rd", 31, "7.5"),
            ("4th", 1, "0.5"), ("5th", 51, "15.5"), ("6th", 11, "3.1")]],
    )


def test_espn_infers_partnership_batters_from_fow():
    inns = _innings_for_partnership_inference()
    EspnSource._infer_partnership_batters(inns)
    pairs = [(p.batter1, p.batter2) for p in inns.partnerships]
    assert pairs == [
        ("Zain", "Tribe"),
        ("Carlson", "Tribe"),
        ("Carlson", "Kellaway"),
        ("Ingram", "Kellaway"),
        ("Dickson", "Kellaway"),
        ("Dickson", "Cooke"),  # current unbroken stand: the two not-out batters
    ]
    # Per-batter runs are genuinely unknown, so they stay 0 (no misleading bar).
    assert all(p.runs1 == 0 and p.runs2 == 0 for p in inns.partnerships)


def test_espn_partnership_inference_respects_feed_names():
    # If the feed already named the batters, don't second-guess it.
    inns = _innings_for_partnership_inference()
    inns.partnerships[0].batter1 = "Someone"
    EspnSource._infer_partnership_batters(inns)
    assert inns.partnerships[0].batter1 == "Someone"
    assert inns.partnerships[1].batter1 == ""  # untouched


def test_espn_partnership_inference_bails_on_broken_chain():
    # A FoW name that matches nobody at the crease (e.g. a retirement) stops the
    # chain: stands up to that point are kept, the rest left blank, never guessed.
    inns = _innings_for_partnership_inference()
    inns.fall_of_wickets[2].batter = "Ghost"  # 3rd wicket: unknown name
    EspnSource._infer_partnership_batters(inns)
    assert (inns.partnerships[2].batter1, inns.partnerships[2].batter2) == ("Carlson", "Kellaway")
    assert (inns.partnerships[3].batter1, inns.partnerships[3].batter2) == ("", "")


def test_espn_over_scores_from_linescore(settings):
    src = EspnSource(settings)
    ov = src._over_scores({"statistics": {"overs": [[
        {"number": "1", "runs": "5", "wicket": []},
        {"number": "2", "runs": "9", "wicket": [{"shortText": "OUT"}]},
        {"number": "3", "runs": "0", "wicket": []},
    ]]}})
    assert [o.runs for o in ov] == [5, 9, 0]
    assert ov[1].wickets == 1 and ov[0].wickets == 0
    assert src._over_scores({}) == []


def test_espn_fall_of_wickets_from_linescore(settings):
    src = EspnSource(settings)
    fow = src._fall_of_wickets({"fow": [
        {"wicketNumber": 2, "runs": 53, "wicketOver": 17.3,
         "athlete": {"displayName": "Will Jacks"}},
        {"wicketNumber": 1, "runs": 27, "wicketOver": 9.2,
         "athlete": {"displayName": "Rory Burns"}},
    ]})
    assert [w.wicket for w in fow] == [1, 2]  # sorted by wicket number
    assert fow[0].team_runs == 27 and fow[0].batter == "Rory Burns" and fow[0].over == "9.2"


def test_espn_match_info_toss_and_officials(settings):
    from stumps.models import Format, Match, Phase, Team
    src = EspnSource(settings)
    m = Match("mi", Format.ODI, [Team("Australia"), Team("Bangladesh")], phase=Phase.COMPLETE)
    src._apply_match_info(m, {
        "notes": [{"type": "toss", "text": "Australia , elected to bat first"}],
        "gameInfo": {"officials": [{"displayName": "Ahsan Raza"}, {"displayName": "Alex Wharf"}]},
    })
    assert m.toss == "Australia, elected to bat first"  # stray space before comma fixed
    assert m.officials == ["Ahsan Raza", "Alex Wharf"]


def test_espn_standings_from_summary(settings):
    from stumps.models import Format, Match, Phase, Team
    src = EspnSource(settings)
    m = Match("s", Format.FIRST_CLASS, [Team("Surrey"), Team("Kent")],
              phase=Phase.COMPLETE, series_name="County Championship Division One")
    data = {"standings": {"name": "County Championship Division One", "children": [
        {"standings": {"entries": [
            {"team": {"displayName": "Nottinghamshire", "abbreviation": "NOT"},
             "stats": [{"name": "rank", "value": 1}, {"name": "matchesPlayed", "value": 6},
                       {"name": "matchesWon", "value": 2}, {"name": "matchesLost", "value": 0},
                       {"name": "matchesDraw", "value": 4}, {"name": "matchPoints", "value": 91}]},
            {"team": {"displayName": "Surrey"},
             "stats": [{"name": "rank", "value": 2}, {"name": "matchPoints", "value": 89}]},
        ]}}]}}
    src._apply_standings(m, data)
    assert m.standings.name == "County Championship Division One"
    assert [r.team for r in m.standings.rows] == ["Nottinghamshire", "Surrey"]
    assert m.standings.rows[0].points == 91 and m.standings.rows[0].won == 2
    assert m.standings.rows[0].nrr is None  # first-class: no NRR

    # Limited-overs entries carry net run rate and a qualification flag.
    lo = Match("lo", Format.ODI, [Team("USA"), Team("Oman")], phase=Phase.COMPLETE)
    src._apply_standings(lo, {"standings": {"name": "WC League 2", "children": [
        {"standings": {"entries": [
            {"team": {"displayName": "USA"},
             "stats": [{"name": "rank", "value": 1}, {"name": "matchPoints", "value": 40},
                       {"name": "netrr", "value": "0.717"}, {"name": "qualified", "value": "Y"}]},
        ]}}]}})
    assert lo.standings.rows[0].nrr == 0.717
    assert lo.standings.rows[0].qualified is True

    # No standings block in the payload -> stays None.
    m2 = Match("s2", Format.ODI, [Team("A"), Team("B")])
    src._apply_standings(m2, {})
    assert m2.standings is None


def _freeze_today(monkeypatch, today, utc_now=None):
    """Pin the clocks inside espn.py so day-number logic is deterministic.

    Freezes both ``date.today()`` (the no-clock fallback) and
    ``datetime.now(tz)`` (the venue-date anchor). UTC defaults to noon on
    ``today``, at which point any venue wall-clock resolves to ``today``; pass
    ``utc_now`` to exercise cross-timezone date rollover."""
    from datetime import date as _date
    from datetime import datetime as _datetime
    from datetime import timezone as _timezone

    class _FrozenDate(_date):
        @classmethod
        def today(cls):
            return today
    monkeypatch.setattr("stumps.sources.espn.date", _FrozenDate)

    frozen = utc_now or _datetime(today.year, today.month, today.day, 12, 0,
                                  tzinfo=_timezone.utc)

    class _FrozenDateTime(_datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen if tz is None else frozen.astimezone(tz)
    monkeypatch.setattr("stumps.sources.espn.datetime", _FrozenDateTime)


def test_espn_parse_match_dates():
    from datetime import date

    from stumps.sources.espn import _parse_match_dates
    assert _parse_match_dates("12,13,14,15 June 2026 (4-day match)") == [
        date(2026, 6, d) for d in (12, 13, 14, 15)
    ]
    # Cross-month span: a run of days is closed by the month that follows it.
    assert _parse_match_dates("31 May, 1,2,3 June 2026 (4-day match)") == [
        date(2026, 5, 31), date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3),
    ]
    assert _parse_match_dates("no dates here") == []


def test_espn_multiday_timing_from_notes(settings, monkeypatch):
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
        ],
    }
    # Day 3 is today -> matched against the explicit date list, not the
    # (stale-by-design) closeofplay count.
    _freeze_today(monkeypatch, date(2026, 6, 9))
    src._apply_multiday_timing(match, summary)
    assert match.local_time == "16:30"
    assert match.start_time == "10:00"
    assert match.close_time == "17:00"
    assert match.total_days == 4
    assert match.day_number == 3


def test_espn_multiday_day_one_with_no_closeofplay(settings, monkeypatch):
    # Day 1 has no closeofplay notes yet; the date list still pins it to Day 1
    # (the old count-based path left day_number unset here).
    from stumps.models import Format, Match, Phase, Team
    src = EspnSource(settings)
    match = Match(match_id="m", format=Format.FIRST_CLASS, phase=Phase.LIVE,
                  teams=[Team("Surrey"), Team("Hampshire")])
    summary = {
        "header": {"competitions": [{"status": {"presentLocalTime": "11:00"}}]},
        "notes": [{"type": "matchdays", "text": "12,13,14,15 June 2026 (4-day match)"}],
    }
    _freeze_today(monkeypatch, date(2026, 6, 12))
    src._apply_multiday_timing(match, summary)
    assert match.total_days == 4
    assert match.day_number == 1


def test_espn_multiday_day_number_anchored_to_venue_timezone(settings, monkeypatch):
    # A UTC+12 venue (e.g. New Zealand) at 10:00 local on day 3 is, in UTC,
    # still 22:00 the previous day — so a machine on UTC reads "yesterday".
    # The day number must follow the *venue's* date (day 3), not the machine's.
    from datetime import datetime as _datetime
    from datetime import timezone as _timezone

    from stumps.models import Format, Match, Phase, Team
    src = EspnSource(settings)
    match = Match(match_id="m", format=Format.FIRST_CLASS, phase=Phase.LIVE,
                  teams=[Team("New Zealand"), Team("England")])
    summary = {
        "header": {"competitions": [{"status": {"presentLocalTime": "10:00"}}]},
        "notes": [{"type": "matchdays", "text": "7,8,9,10 June 2026 (4-day match)"}],
    }
    # Machine/UTC clock sits on 8 June 22:00 UTC; the venue has already ticked
    # over to 9 June (day 3).
    _freeze_today(monkeypatch, date(2026, 6, 8),
                  utc_now=_datetime(2026, 6, 8, 22, 0, tzinfo=_timezone.utc))
    src._apply_multiday_timing(match, summary)
    assert match.day_number == 3  # venue date 9 June, not machine date 8 June


def test_espn_multiday_timing_closeofplay_fallback(settings):
    # When the dates can't be parsed, fall back to counting closeofplay notes.
    from stumps.models import Format, Match, Phase, Team
    src = EspnSource(settings)
    match = Match(match_id="m", format=Format.FIRST_CLASS, phase=Phase.LIVE,
                  teams=[Team("Surrey"), Team("Hampshire")])
    summary = {
        "header": {"competitions": [{"status": {}}]},
        "notes": [
            {"type": "matchdays", "text": "(4-day match)"},
            {"type": "closeofplay", "text": "day 1 - ..."},
            {"type": "closeofplay", "text": "day 2 - ..."},
        ],
    }
    src._apply_multiday_timing(match, summary)
    assert match.total_days == 4
    assert match.day_number == 3  # 2 completed days -> day 3 in progress


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


def test_aggregator_merge_survives_addendum_error():
    # A failing recent/upcoming fetcher must lose only the addendum, not the
    # whole live result.
    from stumps.models import Format, Match, Team
    live = [Match("a", Format.ODI, [Team("X"), Team("Y")])]

    def boom(days):
        raise KeyError("odd payload shape")

    assert Aggregator._merge(live, boom, 2) == live


def test_aggregator_serves_last_good_snapshot_when_offline(settings, monkeypatch):
    from stumps.models import Format, Match, Phase, Team

    # First, a successful live fetch saves a snapshot.
    live = [Match("a", Format.ODI, [Team("England"), Team("India")], phase=Phase.LIVE)]
    monkeypatch.setattr(EspnSource, "fetch_current_matches", lambda self: live)
    agg = Aggregator(settings)
    ok = agg.fetch()
    assert not ok.used_fallback and ok.matches[0].match_id == "a"

    # Now the live source fails -> we serve the cached snapshot, not demo.
    monkeypatch.setattr(
        EspnSource, "fetch_current_matches",
        lambda self: (_ for _ in ()).throw(SourceError("offline")),
    )
    stale = agg.fetch()
    assert stale.used_fallback is False
    assert stale.stale_as_of  # stamped with an "as of" time
    assert [m.match_id for m in stale.matches] == ["a"]
    assert any("cached data" in n for n in stale.notices)


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


def test_espn_fetch_upcoming(settings, monkeypatch):
    src = EspnSource(settings)

    def fake_get(url):
        assert "dates=" in url
        pre = _espn_event()
        pre["id"] = "900"
        pre["date"] = "2026-06-20T10:00:00Z"
        pre["fullStatus"] = {"type": {"state": "pre", "detail": "Scheduled"}}
        live = _espn_event()
        live["id"] = "901"  # state "in" -> not upcoming
        return {"sports": [{"leagues": [
            {"id": "9", "name": "Series", "events": [pre, live]}]}]}

    monkeypatch.setattr(src, "_get", fake_get)
    out = src.fetch_upcoming(2)
    assert {m.match_id for m in out} == {"900"}
    assert out[0].starts_at == "2026-06-20T10:00:00Z"
    assert src.fetch_upcoming(0) == []


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

    merged = Aggregator._merge(live, _Src().fetch_recent_results, 2)
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


# -- followed team last result / next fixture -------------------------------


def _month_event(eid, day, state):
    ev = _espn_event()
    ev["id"] = eid
    ev["date"] = f"{day}T10:00:00Z"
    ev["fullStatus"] = {"type": {"state": state, "detail": state}}
    if state == "post":
        ev["competitors"][0]["winner"] = True
    return ev


def _month_payload(events):
    return {"sports": [{"leagues": [{"id": "9", "name": "S", "events": events}]}]}


def test_espn_scan_months_picks_latest_result_and_next_fixture(settings, monkeypatch):
    from datetime import date

    src = EspnSource(settings)

    def fake_get(url):
        assert "team=975" in url and "dates=202606" in url  # current month only
        return _month_payload([
            _month_event("r1", "2026-06-02", "post"),   # older result
            _month_event("r2", "2026-06-10", "post"),   # latest result <= today
            _month_event("liv", "2026-06-12", "in"),    # in play -> neither
            _month_event("n1", "2026-06-16", "pre"),    # next fixture
            _month_event("n2", "2026-06-20", "pre"),    # later fixture
        ])

    monkeypatch.setattr(src, "_get", fake_get)
    today = date(2026, 6, 12)
    last = src._scan_months("975", today, forward=False)
    nxt = src._scan_months("975", today, forward=True)
    assert last.match_id == "r2" and last.finished_on == "2026-06-10"
    assert nxt.match_id == "n1"


def test_espn_scan_months_walks_to_adjacent_month(settings, monkeypatch):
    from datetime import date

    src = EspnSource(settings)
    seen = []

    def fake_get(url):
        seen.append(url)
        if "dates=202606" in url:        # current month: only an upcoming game
            return _month_payload([_month_event("n", "2026-06-20", "pre")])
        if "dates=202605" in url:        # previous month: a finished result
            return _month_payload([_month_event("old", "2026-05-28", "post")])
        return _month_payload([])

    monkeypatch.setattr(src, "_get", fake_get)
    last = src._scan_months("975", date(2026, 6, 12), forward=False)
    assert last.match_id == "old" and last.finished_on == "2026-05-28"
    assert any("dates=202605" in u for u in seen)  # it walked back a month


def test_espn_fetch_team_last_next_combines_and_filters_none(settings, monkeypatch):
    src = EspnSource(settings)
    assert src.fetch_team_last_next("") == []  # no id -> nothing

    from stumps.models import Format, Match, Team
    last = Match("L", Format.ODI, [Team("England")], phase=Phase.COMPLETE)
    monkeypatch.setattr(src, "_scan_months",
                        lambda oid, today, forward: last if not forward else None)
    out = src.fetch_team_last_next("1")
    assert [m.match_id for m in out] == ["L"]  # only the result; missing next dropped


def test_resolve_squad_ids_seed_and_discovery():
    from stumps.sources.aggregator import resolve_squad_ids

    # Seeded England -> both senior squads, regardless of what's been discovered.
    assert resolve_squad_ids(["england"], {}) == ["1", "975"]

    # Non-seeded nation resolves by exact name + "<token> women" from discovery,
    # and must NOT pull in age-group / A / Lions sides sharing the token.
    discovered = {
        "australia": "5", "australia women": "6",
        "australia a": "7", "australia under-19s": "8",
    }
    ids = resolve_squad_ids(["australia"], discovered)
    assert ids == ["5", "6"]
    assert "7" not in ids and "8" not in ids

    # A franchise with only a men's side resolves to just that, and ids dedupe.
    assert resolve_squad_ids(["sunrisers hyderabad"],
                             {"sunrisers hyderabad": "42"}) == ["42"]


def test_aggregator_records_and_persists_team_ids(settings):
    from stumps.models import Format, Match, Team

    agg = Aggregator(settings)
    matches = [
        Match("m", Format.WT20I,
              [Team("England Women", object_id="975"), Team("Sri Lanka Women")]),
        Match("n", Format.ODI, [Team("England", object_id="1"), Team("India")]),
    ]
    id_map = agg._record_team_ids(matches)
    assert id_map["england women"] == "975" and id_map["england"] == "1"
    # Persisted across instances.
    assert Aggregator(settings)._load_team_ids()["england women"] == "975"


def test_aggregator_fetch_merges_last_next(settings, monkeypatch):
    from stumps.models import Format, Match, Team

    # Live feed has England's current ODI; the bookends come from the schedule.
    live = [Match("live1", Format.ODI,
                  [Team("England", object_id="1"), Team("India", object_id="6")],
                  phase=Phase.LIVE)]
    bookends = {
        "1": [Match("res-m", Format.TEST, [Team("England")], phase=Phase.COMPLETE),
              Match("fix-m", Format.T20I, [Team("England")], phase=Phase.UPCOMING)],
        "975": [Match("res-w", Format.WT20I, [Team("England Women")],
                      phase=Phase.COMPLETE)],
    }
    monkeypatch.setattr(EspnSource, "fetch_current_matches", lambda self: live)
    monkeypatch.setattr(EspnSource, "fetch_team_last_next",
                        lambda self, oid: bookends.get(oid, []))

    agg = Aggregator(settings)
    result = agg.fetch(followed_teams=["england"], last_next=True)
    ids = [m.match_id for m in result.matches]
    assert ids[0] == "live1"            # live result preserved, first
    assert set(ids) == {"live1", "res-m", "fix-m", "res-w"}  # both squads' bookends


def test_aggregator_last_next_off_by_flag(settings, monkeypatch):
    from stumps.models import Format, Match, Team

    live = [Match("live1", Format.ODI, [Team("England", object_id="1")],
                  phase=Phase.LIVE)]
    monkeypatch.setattr(EspnSource, "fetch_current_matches", lambda self: live)
    monkeypatch.setattr(
        EspnSource, "fetch_team_last_next",
        lambda self, oid: (_ for _ in ()).throw(AssertionError("should not fetch")))

    agg = Aggregator(settings)
    result = agg.fetch(followed_teams=["england"], last_next=False)
    assert [m.match_id for m in result.matches] == ["live1"]

"""Rendering: win-prob is in-play only, and figures carry column headers."""

import json

from rich.console import Console

from stumps.config import Settings
from stumps.models import Format, Phase
from stumps.options import Preferences
from stumps.prioritise import classify, prioritise
from stumps.render.console import _match_panel, _synth_result, render_report
from stumps.render.json_out import render_json
from stumps.sources.aggregator import Aggregator
from stumps.sources.fixtures import sample_matches


def _render(match, settings, prefs=None) -> str:
    prefs = prefs or Preferences()
    console = Console(width=100, record=True)
    console.print(_match_panel(match, classify(match), settings, prefs))
    return console.export_text()


def _settings():
    return Settings(cricketdata_api_key=None)


def test_no_winprob_for_completed_match():
    completed = next(m for m in sample_matches() if m.phase is Phase.COMPLETE)
    out = _render(completed, _settings())
    assert "Win probability" not in out
    assert completed.result_text in out  # the result is still shown


def test_bare_result_headline_suppressed():
    # A finished game whose only status is the bare label "Result" shouldn't
    # echo it as a headline — the green ✓ RESULT badge already says so.
    import dataclasses
    completed = next(m for m in sample_matches() if m.phase is Phase.COMPLETE)
    bare = dataclasses.replace(completed, status_text="Result", result_text="")
    lines = [ln.strip() for ln in _render(bare, _settings()).splitlines()]
    assert "Result" not in lines  # no standalone "Result" headline line
    # ...but the result is synthesised from the chase rather than left blank.
    assert any("South Africa won by 4 wickets" in ln for ln in lines)


# -- synthesised result fallback --------------------------------------------


def _completed_lo(first, chase, fmt=Format.ODI, status="Result", result=""):
    """Build a finished limited-overs match. `first`/`chase` are
    (team, runs, wickets[, target]) tuples for the two innings."""
    from stumps.models import Innings, Match, Team
    ft, fr, fw = first
    ct, cr, cw, target = chase
    return Match(
        match_id="lo-complete", format=fmt, phase=Phase.COMPLETE,
        teams=[Team(ft, ft[:3].upper()), Team(ct, ct[:3].upper())],
        status_text=status, result_text=result, source="demo",
        innings=[
            Innings(ft, ct, 1, fr, fw, 50.0, all_out=fw >= 10, closed=True),
            Innings(ct, ft, 2, cr, cw, 49.0, target=target, closed=True),
        ],
    )


def test_synth_result_chase_won_by_wickets():
    m = _completed_lo(("India", 169, 8), ("England", 170, 4, 170))
    assert _synth_result(m) == "England won by 6 wickets"
    assert "England won by 6 wickets" in _render(m, _settings())


def test_synth_result_defended_won_by_runs():
    # India 171, England 166 all out chasing 172 -> India won by 5 runs.
    m = _completed_lo(("India", 171, 6), ("England", 166, 10, 172))
    assert _synth_result(m) == "India won by 5 runs"


def test_synth_result_tie():
    # Scores level: chase reaches target-1 and is over.
    m = _completed_lo(("India", 170, 7), ("England", 170, 9, 171))
    assert _synth_result(m) == "Match tied"


def test_synth_result_singular_units():
    assert _synth_result(
        _completed_lo(("India", 200, 8), ("England", 201, 9, 201))
    ) == "England won by 1 wicket"
    assert _synth_result(
        _completed_lo(("India", 200, 6), ("England", 199, 10, 201))
    ) == "India won by 1 run"


def test_synth_result_skips_dls():
    # A D/L result re-weights the target; visible totals give a wrong margin,
    # so we defer to the (here absent) feed text rather than guess.
    m = _completed_lo(("India", 250, 8), ("England", 180, 6, 240),
                      status="England won by 12 runs (DLS method)")
    # status isn't generic here, so the feed text wins anyway...
    assert _synth_result(m) is None
    # ...and even with a generic status, a DLS marker blocks synthesis.
    m2 = _completed_lo(("India", 250, 8), ("England", 180, 6, 240),
                       status="Result", result="Match decided by D/L")
    assert _synth_result(m2) is None


def test_synth_result_skips_multi_day():
    from stumps.models import Innings, Match, Team
    m = Match(
        match_id="td", format=Format.TEST, phase=Phase.COMPLETE,
        teams=[Team("Eng", "ENG"), Team("Aus", "AUS")], status_text="Result",
        innings=[
            Innings("Eng", "Aus", 1, 400, 10, 120.0, all_out=True, closed=True),
            Innings("Aus", "Eng", 2, 300, 10, 100.0, all_out=True, closed=True),
            Innings("Eng", "Aus", 3, 250, 8, 70.0, declared=True, closed=True),
            Innings("Aus", "Eng", 4, 200, 5, 60.0, target=351, closed=True),
        ],
    )
    assert _synth_result(m) is None  # draw vs win isn't derivable from scores


def test_winprob_shown_for_live_chase():
    live = next(m for m in sample_matches() if m.match_id == "demo-ind-nz-wc")
    out = _render(live, _settings())
    assert "Win probability" in out
    assert "India" in out and "%" in out


def test_winprob_shown_at_stumps():
    test_match = next(m for m in sample_matches() if m.phase is Phase.STUMPS)
    out = _render(test_match, _settings())
    assert "Win probability" in out  # stumps is an active/paused state


def test_batting_and_bowling_have_column_headers():
    live = next(m for m in sample_matches() if m.match_id == "demo-ind-nz-wc")
    out = _render(live, _settings())
    # Section labels double as the name-column header...
    assert "Batting" in out and "Bowling" in out
    # ...and the figure columns are legended.
    for col in ("SR", "Econ", "4s/6s"):
        assert col in out


def test_headline_synthesises_chase_target():
    live = next(m for m in sample_matches() if m.match_id == "demo-ind-nz-wc")
    out = _render(live, _settings())
    # "India require 71 runs from 12.0 overs"
    assert "require" in out and "overs" in out


def test_headline_lead_trail_for_test():
    test_match = next(m for m in sample_matches() if m.format is Format.TEST)
    out = _render(test_match, _settings())
    assert "trail by" in out or "lead by" in out


def test_headline_target_in_fourth_innings():
    from stumps.models import Innings, Match, Team
    # Surrey 421 & 259, Hampshire 333 & 44/2 -> Hampshire need 348 to win, so
    # with 44 on the board they require 304 more.
    match = Match(
        match_id="fc-final-innings",
        format=Format.FIRST_CLASS,
        series_name="County Championship Division One",
        teams=[Team("Surrey", "SUR"), Team("Hampshire", "HAM")],
        phase=Phase.LIVE,
        venue="The Oval, London",
        status_text="Hampshire trail by 303",
        source="demo",
        innings=[
            Innings(batting_team="Surrey", bowling_team="Hampshire",
                    number=1, runs=421, wickets=10, overs=110.0, all_out=True,
                    closed=True),
            Innings(batting_team="Hampshire", bowling_team="Surrey",
                    number=2, runs=333, wickets=10, overs=95.0, all_out=True,
                    closed=True),
            Innings(batting_team="Surrey", bowling_team="Hampshire",
                    number=3, runs=259, wickets=10, overs=70.0, all_out=True,
                    closed=True),
            Innings(batting_team="Hampshire", bowling_team="Surrey",
                    number=4, runs=44, wickets=2, overs=17.2),
        ],
    )
    out = _render(match, _settings())
    assert "Hampshire require 304 runs to win with 8 wickets remaining" in out
    assert "trail by" not in out


def test_recent_balls_section_rendered():
    live = next(m for m in sample_matches() if m.match_id == "demo-ind-nz-wc")
    out = _render(live, _settings())
    assert "Recent" in out
    assert "FOUR" in out and "OUT" in out  # boundary + wicket commentary shown


def test_dls_only_in_play():
    completed = next(m for m in sample_matches() if m.phase is Phase.COMPLETE)
    assert "DLS" not in _render(completed, _settings())
    live = next(m for m in sample_matches() if m.match_id == "demo-ind-nz-wc")
    assert "DLS" in _render(live, _settings())


# -- display toggles --------------------------------------------------------


def test_display_toggles_hide_sections():
    live = next(m for m in sample_matches() if m.match_id == "demo-ind-nz-wc")
    prefs = Preferences(show_winprob=False, show_dls=False, show_commentary=False,
                        show_figures=False)
    out = _render(live, _settings(), prefs)
    for absent in ("Win probability", "DLS", "Recent", "Batting", "Bowling"):
        assert absent not in out
    # scores headline still present
    assert "India" in out


def _ranked_demo():
    settings = _settings()
    result = Aggregator(settings, demo_only=True).fetch()
    return result, prioritise(result.matches), settings


def test_compact_mode_is_one_line_per_match():
    result, ranked, settings = _ranked_demo()
    console = Console(width=120, record=True)
    render_report(console, result, ranked, settings, Preferences(compact=True))
    out = console.export_text()
    assert "Win probability" not in out and "Batting" not in out
    body = [ln for ln in out.splitlines() if ln.strip() and "stumps" not in ln
            and "source" not in ln]
    # roughly one line per shown match (not multi-line panels)
    assert len(body) <= len(ranked) + 1


# -- JSON output ------------------------------------------------------------


def test_json_output_schema():
    result, ranked, settings = _ranked_demo()
    payload = json.loads(render_json(result, ranked, settings, Preferences()))
    assert payload["source"] == "demo"
    assert isinstance(payload["matches"], list) and payload["matches"]
    m = next(x for x in payload["matches"] if x["id"] == "demo-ind-nz-wc")
    assert m["format"] == "ODI" and m["tier"] == "premier"
    assert m["teams"] == ["New Zealand", "India"]
    assert m["win_probability"]["method"] in {"model", "heuristic"}
    assert m["dls"]["par"] > 0
    assert m["innings"][1]["batters"][0]["name"] == "V Kohli"
    assert m["recent_balls"][0]["over"] == "38.0"

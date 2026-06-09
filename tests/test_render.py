"""Rendering: win-prob is in-play only, and figures carry column headers."""

import json

from rich.console import Console

from stumps.config import Settings
from stumps.models import Format, Phase
from stumps.options import Preferences
from stumps.prioritise import classify, prioritise
from stumps.render.console import _match_panel, render_report
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
    assert "need" in out and "from" in out  # "India need 71 runs from 72 balls"


def test_headline_lead_trail_for_test():
    test_match = next(m for m in sample_matches() if m.format is Format.TEST)
    out = _render(test_match, _settings())
    assert "trail by" in out or "lead by" in out


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

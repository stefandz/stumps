"""Rendering: win-prob is in-play only, and figures carry column headers."""

from rich.console import Console

from stumps.config import Settings
from stumps.models import Format, Phase
from stumps.prioritise import classify
from stumps.render.console import _match_panel
from stumps.sources.fixtures import sample_matches


def _render(match, settings) -> str:
    console = Console(width=100, record=True)
    console.print(_match_panel(match, classify(match), settings))
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

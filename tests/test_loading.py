"""The playful loading animation (no network, no real terminal)."""

import re
import time

from rich.console import Console

from stumps.render.loading import _ASIDES, _CricketLoader, loading


def _plain(console: Console, renderable) -> str:
    with console.capture() as cap:
        console.print(renderable)
    return re.sub(r"\x1b\[[0-9;]*m", "", cap.get()).strip()


def test_loader_frame_has_ball_wave_and_aside():
    console = Console(force_terminal=True, width=80)
    frame = _plain(console, _CricketLoader(time.monotonic()))
    assert frame.startswith("🏏")
    assert any(block in frame for block in "▁▂▃▄▅▆▇█")  # the dancing wave
    assert frame.rstrip("…").endswith(_ASIDES[0])        # the first aside


def test_loader_animates_over_time():
    console = Console(force_terminal=True, width=80)
    base = time.monotonic()
    early = _plain(console, _CricketLoader(base))           # t ~ 0
    later = _plain(console, _CricketLoader(base - 4.0))     # t ~ 4s
    assert early != later                                   # wave moved + aside rotated


def test_loading_disabled_is_silent_noop():
    console = Console(force_terminal=True, width=80)
    with console.capture() as cap:
        with loading(console, enabled=False):
            pass
    assert cap.get() == ""


def test_loading_skips_when_not_a_terminal():
    # Piped/redirected output (is_terminal False) must stay clean.
    console = Console(force_terminal=False, width=80)
    with console.capture() as cap:
        with loading(console):
            pass
    assert cap.get() == ""

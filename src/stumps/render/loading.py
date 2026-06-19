"""A playful, on-brand loading animation for the network-bound parts of a run.

Fetching the scoreboard and reading per-match scorecards (now with ball-by-ball
commentary) takes a beat, and a blank terminal reads as "stumps is hung". So we
show a little dancing over-by-over wave — the same block glyphs the scorecards
use — alongside a rotating cricket aside, then clear it the moment the work is
done. Only on an interactive terminal; piped/JSON output stays clean.
"""

from __future__ import annotations

import contextlib
import math
import time

from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.text import Text

#: The sparkline block ramp, reused from the over-by-over manhattan.
_SPARK = "▁▂▃▄▅▆▇█"

#: Witty cricket asides, rotated every couple of seconds while we wait.
_ASIDES = (
    "scuffing up the ball",
    "marking out a run-up",
    "consulting the third umpire",
    "checking the Duckworth–Lewis tables",
    "waiting for the sightscreen to settle",
    "setting a tempting field",
    "giving the pitch a roll",
    "appealing… not out",
    "taking tea and a slice of cake",
    "reviewing for a faint edge",
    "straightening the bails",
    "having a word with the skipper",
    "checking the light meter",
    "tossing the coin",
)

_WAVE_WIDTH = 16        # blocks in the dancing wave
_ASIDE_EVERY = 1.8      # seconds each aside lingers


class _CricketLoader:
    """A renderable whose output is a pure function of the wall clock, so a Live
    display re-rendering it every tick produces a smooth animation without us
    having to push updates."""

    def __init__(self, start: float) -> None:
        self._start = start

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        t = time.monotonic() - self._start
        # A travelling sine wave of block glyphs — a scoreboard doing a Mexican
        # wave. Each column is a little further along the wave than the last.
        wave = Text()
        for i in range(_WAVE_WIDTH):
            level = (math.sin(t * 6.5 - i * 0.6) * 0.5 + 0.5) * (len(_SPARK) - 1)
            wave.append(_SPARK[round(level)], style="cyan")
        aside = _ASIDES[int(t / _ASIDE_EVERY) % len(_ASIDES)]
        line = Text("🏏  ", style="bold green")
        line.append_text(wave)
        line.append(f"  {aside}…", style="dim italic")
        yield line


@contextlib.contextmanager
def loading(console: Console, enabled: bool = True):
    """Show the loading animation around a block of network work. A no-op when
    disabled or when output isn't an interactive terminal (piped / JSON), where
    the animation would just be noise in the captured output."""
    if not enabled or not console.is_terminal:
        yield
        return
    loader = _CricketLoader(time.monotonic())
    with Live(loader, console=console, refresh_per_second=15, transient=True):
        yield

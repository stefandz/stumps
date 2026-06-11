"""Desktop notifications for the `--refresh` watch loop.

Opt-in via `--notify`. While refreshing, we alert on the things a fan doesn't
want to miss for the teams they follow: a **wicket** or a **result**. State is
diffed between refreshes, with the first sighting of a match treated as a
baseline (so we don't fire for everything that was already happening when you
started watching).

Dependency-free: uses `notify-send` if present (Linux desktops), otherwise falls
back to a terminal bell + a stderr line.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass

from stumps.models import Match, Phase
from stumps.prioritise import Classification


@dataclass(frozen=True)
class Notification:
    title: str
    body: str


def _match_state(match: Match) -> tuple[int, Phase]:
    # Total wickets fallen is monotonic within a match, so a rise = a new wicket.
    return sum(i.wickets for i in match.innings), match.phase


def _score_line(match: Match) -> str:
    inns = match.current_innings
    return f"{inns.batting_team} {inns.score}" if inns else match.title


def detect_events(
    prev_state: dict[str, tuple[int, Phase]],
    ranked: list[tuple[Match, Classification]],
) -> tuple[list[Notification], dict[str, tuple[int, Phase]]]:
    """Compare the current followed matches against the previous state; return
    the notifications to fire plus the new state. A match seen for the first time
    only establishes a baseline (no notification)."""
    new_state: dict[str, tuple[int, Phase]] = {}
    events: list[Notification] = []
    for match, cls in ranked:
        if not cls.is_followed:
            continue
        wkts, phase = _match_state(match)
        new_state[match.match_id] = (wkts, phase)
        if match.match_id not in prev_state:
            continue  # baseline — don't alert on first sight
        prev_wkts, prev_phase = prev_state[match.match_id]
        if phase is Phase.COMPLETE and prev_phase is not Phase.COMPLETE:
            events.append(Notification(
                "🏏 Result", match.result_text or _score_line(match)))
        elif wkts > prev_wkts:
            events.append(Notification("🏏 Wicket!", _score_line(match)))
    return events, new_state


def send(note: Notification) -> None:
    """Best-effort desktop notification, degrading to a terminal bell."""
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-a", "stumps", note.title, note.body],
                check=False, timeout=5,
            )
            return
        except Exception:
            pass
    sys.stderr.write(f"\a{note.title}  {note.body}\n")
    sys.stderr.flush()

#!/usr/bin/env python
"""Capture raw ESPN payloads for a live match into tests/fixtures/.

Use during a real Test to snapshot the feed at key moments (day 1, mid-match,
the fourth innings) so we can build offline regression tests from real data —
the suite is otherwise network-free.

    python scripts/capture_test_fixtures.py "England v India" day1
    python scripts/capture_test_fixtures.py "England v India" day5-chase

It finds the (first) match whose name contains TEXT (case-insensitive; "v"/"vs"
both work), then writes <label>.json under tests/fixtures/espn/ containing the
scoreboard event, the per-event summary, and the first commentary page. It also
prints a quick digest (phase, day, innings, overs-remaining estimate) so you can
confirm you grabbed the moment you wanted.

Throwaway/dev tool — not imported by the app or the test suite.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from stumps.config import load_settings
from stumps.sources.espn import (
    EspnSource, _PLAYBYPLAY, _SCOREBOARD, _SUMMARY, _dig,
)
from stumps.winprob.multiday import overs_remaining_estimate

_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "espn"


def _norm(text: str) -> str:
    return text.lower().replace(" vs. ", " v ").replace(" vs ", " v ")


def _find_event(src: EspnSource, query: str):
    """Return (league_id, event) for the first scoreboard event matching query."""
    q = _norm(query)
    data = src._get(_SCOREBOARD)
    for sport in data.get("sports") or []:
        for league in sport.get("leagues") or []:
            for event in league.get("events") or []:
                if q in _norm(event.get("name") or ""):
                    return str(league.get("id") or ""), event
    return None, None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    query, label = argv
    safe_label = re.sub(r"[^A-Za-z0-9_-]", "_", label)

    src = EspnSource(load_settings())
    league_id, event = _find_event(src, query)
    if event is None:
        print(f"No match found for {query!r} in the current scoreboard.")
        return 1
    event_id = str(event.get("id") or "")

    summary, commentary = {}, {}
    try:
        summary = src._get(_SUMMARY.format(league=league_id, event=event_id))
    except Exception as exc:  # noqa: BLE001 - best-effort capture
        print(f"  (summary fetch failed: {exc})")
    try:
        commentary = src._get(_PLAYBYPLAY.format(event=event_id, page=1))
    except Exception as exc:  # noqa: BLE001
        print(f"  (commentary fetch failed: {exc})")

    # Digest, so you can confirm you grabbed the right moment.
    match = src._event_to_match(event, league_id, "")
    src.enrich(match)

    # Stamp the as-of-capture timing. overs-remaining and the current day are
    # derived from the *real clock*, so a fixture replayed days later would
    # otherwise recompute a different (wrong) moment. Persist them so offline
    # replay can pin the snapshot instead of re-deriving it.
    captured_state = {
        "day_number": match.day_number,
        "total_days": match.total_days,
        "overs_remaining": round(overs_remaining_estimate(match), 1),
        "local_time": match.local_time,
        "close_time": match.close_time,
    }

    _FIXTURES.mkdir(parents=True, exist_ok=True)
    out = _FIXTURES / f"{safe_label}.json"
    out.write_text(json.dumps(
        {"query": query, "league_id": league_id, "event_id": event_id,
         "captured_state": captured_state,
         "scoreboard_event": event, "summary": summary, "commentary_page1": commentary},
        indent=1), encoding="utf-8")

    print(f"captured {out.relative_to(_FIXTURES.parent.parent.parent)}")
    print(f"  {match.title}  [{match.phase.value}]  {match.format.value}")
    print(f"  day {match.day_number}/{match.total_days}  "
          f"local {match.local_time or '?'}  close {match.close_time or '?'}  "
          f"~{overs_remaining_estimate(match):.0f} overs left")
    scores = " | ".join(f"{i.batting_team} {i.score}" for i in match.innings)
    print(f"  innings: {scores or '(none yet)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

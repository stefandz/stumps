#!/usr/bin/env python
"""Replay a captured ESPN fixture through the win-prob estimator, offline.

Companion to capture_test_fixtures.py: reconstructs the Match from the saved
scoreboard event + summary (no network) and prints the heuristic and trained-
model estimates, so a real match can be used to validate the model against a
reference (e.g. WinViz) at the moment it was captured.

    python scripts/eval_winprob_fixture.py tests/fixtures/espn/<label>.json

IMPORTANT — overs-remaining and the current match-day are derived from the *real
clock*, so a fixture replayed days after capture would otherwise be scored at the
wrong moment. When the fixture carries a `captured_state` block (newer captures),
we PIN day/total/overs to it so the replay is faithful whenever it's run. Older
fixtures without it are scored as-of-today, with a warning.

Throwaway/dev tool — not imported by the app or the test suite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import stumps.winprob.multiday as multiday
from stumps.config import load_settings
from stumps.sources.espn import EspnSource
from stumps.winprob.estimator import estimate


def _load_match(fx: dict, src: EspnSource):
    summary = fx["summary"]

    def offline_get(url: str) -> dict:
        if url.startswith("https://site.api.espn.com") and "summary?event=" in url:
            return summary
        raise RuntimeError(f"offline: refusing network call to {url}")

    src._get = offline_get  # type: ignore[assignment]
    match = src._event_to_match(fx["scoreboard_event"], fx["league_id"], "")
    src.enrich(match)
    return match


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    fx = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    settings = load_settings()
    src = EspnSource(settings)
    match = _load_match(fx, src)

    # Pin the as-of-capture moment so overs-remaining doesn't drift with today's
    # date (extract_multiday_state reads multiday.overs_remaining_estimate).
    state = fx.get("captured_state")
    if state:
        match.day_number = state.get("day_number") or match.day_number
        match.total_days = state.get("total_days") or match.total_days
        pinned = state.get("overs_remaining")
        if pinned is not None:
            multiday.overs_remaining_estimate = lambda _m, _v=float(pinned): _v
    else:
        print("  (!) no captured_state — scoring as-of-today; overs/day may have "
              "drifted since capture")

    print(f"{match.title}  [{match.phase.value}]  {match.format.value}")
    print(f"  day {match.day_number}/{match.total_days}  "
          f"~{multiday.overs_remaining_estimate(match):.0f} overs left")
    for i in match.innings:
        print(f"  {i.batting_team} {i.score}")
    print()
    for use_model in (False, True):
        est = estimate(match, settings, use_multiday_model=use_model)
        tag = "MODEL (--test-model)" if use_model else "HEURISTIC (default)"
        print(f"=== {tag} ===")
        if est is None:
            print("  (no estimate)\n")
            continue
        for team, p in est.probabilities.items():
            print(f"  {team}: {p * 100:.1f}%")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

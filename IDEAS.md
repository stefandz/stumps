# Ideas

Candidate next features for `stumps`, recorded after the core product
(overs-aware win probability, recent results, finished-match rendering) felt
complete. None built yet — ordered by how well they fit the existing
architecture.

## 1. Notifications during `--refresh` — DONE

A desktop notification (or terminal bell) on a **wicket** or a **result** for
your followed teams. Opt-in via `--notify` with `--refresh`. Implemented in
`notify.py` (`detect_events` diffs followed matches between refreshes,
baseline-on-first-sight; `send` uses `notify-send`, else a bell).

## 2. Tournament standings / points tables — DONE

Answer "where's my team in the table?".

- Per-match **points awarded** show on finished league/tournament games
  (`Match.points`, from the summary `notes` type `points`).
- The full **standings table** is opt-in via `--standings`: `espn._apply_standings`
  parses the summary `standings` block (`children[].standings.entries[]`, ranked,
  points from `matchPoints`) into `Match.standings`, and `render` appends one
  table per distinct competition shown (`_standings_panel`). Generic across
  county championship and the first-class / limited-overs leagues worldwide.
- **Inline league positions** (default-on): each league match shows where its two
  teams sit — `League  Surrey 2nd (89 pts) · Hampshire 9th (53 pts)`
  (`_league_line`). Hidden with `--no-table`.

## 3. Single-match detail / drill-down — DONE

`stumps --match TEXT` (substring of team/series — friendlier than an opaque id)
shows the full scorecard: every innings, every batter with how-out, every
bowler, plus headline/points/DLS/win-prob/recent balls. `render_match_detail`
in `render/console.py`; `_batting_table`/`_bowling_table` gained a `full=True`
mode. Works with `--json` too (emits just that match).

Possible later: fall of wickets / partnerships (not parsed yet).

## 4. Status-line / one-liner mode — DONE

`stumps --oneline` prints one plain-text line (no panels/ANSI/enrich) for the
top match — preferring one in play, falling back to the top recent result —
e.g. `🏏 NZ 280/8  IND 210/4 — India require 71 runs from 12.0 overs`. For tmux /
polybar / a menu bar. `oneline()` in `render/console.py`.

## 5. Offline last-good cache — DONE

When all live sources fail, the aggregator serves the **last successful fetch**
(pickled to `~/.cache/stumps/last_good.pkl`) instead of dropping to demo data,
with the header noting "cached (as of …)". Capped at 12h old, after which demo
is clearer. `Aggregator._save_snapshot`/`_load_snapshot`;
`FetchResult.stale_as_of`.

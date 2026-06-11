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

## 2. Tournament standings / points tables

Answer "where's my team in the table?".

- **Done:** per-match **points awarded** are now shown on finished
  league/tournament games (`Match.points`, from the summary `notes` type
  `points`) across county championship and the first-class / limited-overs
  leagues worldwide.
- **Still open:** the full **standings / points table** — the ESPN per-event
  `summary` payload also carries a `standings` block. A `--standings` view (or a
  table appended to a tournament match) would show the whole league position.
  Medium effort (probe the `standings` shape first).

## 3. Single-match detail / drill-down

`stumps --match <id>` showing the full scorecard — every batter/bowler, fall of
wickets, partnerships, more commentary.

- The `summary` endpoint already returns `rosters` / `leaders` / `matchcards`.
- The natural "tap in for more" from the ranked list. Biggest scope, most
  "wow". Medium–high effort.

## 4. Status-line / one-liner mode

`stumps --oneline` → one ultra-compact line for tmux / polybar / a menu bar,
e.g. `🏏 ENG 250/4 (45) v AUS — req 71 off 72`.

- Complements `--json`. Low effort; becomes a daily-driver.

## 5. Offline last-good cache

When live sources fail, show the **last successful fetch** ("as of HH:MM")
instead of dropping to demo data.

- A resilience nicety. Low–medium effort.

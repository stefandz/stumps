# Ideas

Candidate next features for `stumps`, recorded after the core product
(overs-aware win probability, recent results, finished-match rendering) felt
complete. None built yet — ordered by how well they fit the existing
architecture.

## 1. Notifications during `--refresh`

Turn the refresh loop into an ambient companion: a desktop notification (or
terminal bell) on a **wicket** or a **result** for your followed teams.

- Mostly a dedupe-and-notify layer over the existing `--refresh` loop.
- `Ball.is_wicket` is already captured in `recent_balls`, and result detection
  is solid, so the inputs exist.
- Opt-in via `--notify`. Modest effort.

## 2. Tournament standings / points tables

Answer "where's my team in the table?".

- The ESPN per-event `summary` payload already carries a `standings` block, and
  county-championship points are in the `notes` (type `points`, e.g.
  "Surrey 7, Hampshire 5").
- A `--standings` view, or a footer on tournament/championship matches.
- Data is already in hand. Medium effort (probe the `standings`/`points` shapes
  first, as with the multi-day-timing and recent-results work).

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

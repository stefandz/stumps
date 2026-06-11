# Ideas

Feature backlog for `stumps`. The original five-idea backlog is built; this now
tracks what's done and what's still open.

## Done

- **Recent results** (`--results N` / `--no-results`) — finished games from the
  last N days for the teams you care about, tagged "Today"/"Yesterday", pulled
  in even after they drop off the live feed.
- **Points awarded** — finished league/tournament games show the points earned
  (`Match.points`, from the summary `notes` type `points`).
- **Standings** — `--standings` appends the full league table per competition
  (`espn._apply_standings` → `Match.standings`, `_standings_panel`); each league
  match also shows its two teams' positions inline by default (`_league_line`,
  hidden with `--no-table`).
- **Notifications** (`--notify` with `--refresh`) — desktop alert / bell on a
  wicket or result for followed teams (`notify.detect_events`/`send`).
- **Single-match detail** (`--match TEXT`) — full scorecard: every innings, every
  batter with how-out, every bowler (`render_match_detail`; `full=True` tables).
- **Status-line mode** (`--oneline`) — one plain line for tmux/polybar/menu bars.
- **Offline last-good cache** — serve the last successful fetch (≤12h) when live
  sources fail, stamped "cached (as of …)" (`Aggregator._save/_load_snapshot`).
- **Keep notable internationals in history** — a full-member international shown
  live lingers briefly after it finishes (symmetric catch-all in `_passes_tier`,
  bounded by `--results`); `--core-results` keeps history to your core teams.

## Open

### 7. Upcoming fixtures view — DONE

Forward-looking mirror of recent results. `fetch_upcoming(days)` queries the
header at future `&dates=`, and core teams' scheduled games show with a "Starts
<local time>" line (`_local_start`), soonest-first. Default 3 days
(`upcoming_days`, `--upcoming N`; `--no-upcoming` skips the fetch entirely).
Tier-scoped for free (the catch-all is live/finished only, so non-core upcoming
games don't leak in).

### 8. Net run rate + qualification in `--standings` — DONE

`--standings` tables now adapt to format: multi-day tables show a draws (D)
column, limited-overs tables show **NRR** (signed) instead, and a **Q** marker
flags qualified teams. `StandingsRow` gained `nrr`/`qualified`; parsed in
`espn._apply_standings`, rendered conditionally in `_standings_panel`.

### 9. Richer `--match` detail (leaders / toss / player of the match)

The per-event `summary` we already fetch carries unused blocks — `leaders` (top
run-scorers / wicket-takers), the toss (`notes`), `gameInfo`, player-of-the-match.
Surfacing these in the drill-down makes `--match` the full picture. Low–medium.

### 10. Fall of wickets / partnerships

The biggest depth win for the scorecard, but contingent on a payload probe to
confirm the data is present in `summary` before committing. Stretch goal.

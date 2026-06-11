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

### 7. Upcoming fixtures view

A forward-looking mirror of recent results: "when's England next on?". The ESPN
header accepts future `&dates=YYYYMMDD`, and each event carries a start time we
can render in local tz. A `--upcoming N` window (or a fixtures section) listing
followed/domestic/premier teams' next games with date + local start time.
Reuses the dated-fetch infra. Medium effort.

### 8. Net run rate + qualification in `--standings`

Limited-overs league tables turn on NRR, which we already parse but don't show
(entries carry `netrr`, a `qualified` flag, and `for`/`against`). Add an NRR
column for white-ball tables and a `Q` qualification marker. Low effort;
completes the standings feature for limited-overs leagues.

### 9. Richer `--match` detail (leaders / toss / player of the match)

The per-event `summary` we already fetch carries unused blocks — `leaders` (top
run-scorers / wicket-takers), the toss (`notes`), `gameInfo`, player-of-the-match.
Surfacing these in the drill-down makes `--match` the full picture. Low–medium.

### 10. Fall of wickets / partnerships

The biggest depth win for the scorecard, but contingent on a payload probe to
confirm the data is present in `summary` before committing. Stretch goal.

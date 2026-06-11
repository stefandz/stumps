# Ideas

Feature backlog for `stumps`. Everything proposed has shipped — this is now a
record of what was built (and a couple of things deliberately left out).

## Shipped

- **Recent results** (`--results N` / `--no-results`) — finished games from the
  last N days for the teams you care about, tagged "Today"/"Yesterday", pulled
  in even after they drop off the live feed.
- **Keep notable internationals in history** — a full-member international shown
  live lingers briefly after it finishes (symmetric catch-all in `_passes_tier`,
  bounded by `--results`); `--core-results` keeps history to your core teams.
- **Upcoming fixtures** (`--upcoming N`, default 3 days) — your teams' scheduled
  games with a local "Starts …" time, soonest-first (`fetch_upcoming`).
- **Points awarded** — finished league/tournament games show the points earned
  (`Match.points`, from the summary `notes`).
- **Standings** (`--standings`) — the full league table per competition, columns
  adapting to format (draws for multi-day; NRR + a Q marker for limited-overs).
  Each league match also shows its two teams' positions inline by default
  (`_league_line`, hidden with `--no-table`).
- **Single-match detail** (`--match TEXT`) — the full scorecard: every innings,
  every batter in batting order with how-out, every bowler, plus partnerships
  (latest innings), the toss and the umpires.
- **Notifications** (`--notify` with `--refresh`) — desktop alert / bell on a
  wicket or result for followed teams.
- **Status-line mode** (`--oneline`) — one plain line for tmux / polybar / a menu
  bar (prefers a match in play, else the top recent result).
- **Offline last-good cache** — serve the last successful fetch (≤12h) when live
  sources fail, stamped "cached (as of …)".

## Deliberately not done

- **Full "c Fielder b Bowler" dismissals** — the summary scorecard only carries
  the dismissal *mode* ("caught", "bowled", …), which is what `--match` shows.
  The full text would mean scraping every page of ball-by-ball commentary
  (~25 pages for an ODI, far more for a Test) per match — too heavy.
- **Player of the match** — not present in the summary payload.
- **Top-performer "leaders" block** — available, but redundant with the full
  scorecard the `--match` view already shows.

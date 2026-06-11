# Ideas

Feature backlog for `stumps`. The core product is built and feels fully
featured; this tracks what shipped, what's deliberately out, and a few candidate
next steps (mostly health/consolidation rather than new features).

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
- **Single-match detail** (`--match TEXT`, separator-tolerant "v"/"vs") — the
  full scorecard: every innings, every batter in batting order with how-out, the
  fall of wickets, every bowler, partnerships (a back-to-back bar of each
  batter's runs on a shared centre line, falling back to a plain list when the
  per-batter split is absent), the toss and the umpires.
- **Notifications** (`--notify` with `--refresh`) — desktop alert / bell on a
  wicket or result for followed teams.
- **Status-line mode** (`--oneline`) — one plain line for tmux / polybar / a menu
  bar (prefers a match in play, else the top recent result).
- **Offline last-good cache** — serve the last successful fetch (≤12h) when live
  sources fail, stamped "cached (as of …)".

## Candidate next steps

Mostly consolidation rather than new features — in rough priority order.

### Health / consolidation

- ~~**`cricketdata.org` fallback has fallen behind.**~~ DECIDED: kept as a
  deliberately-degraded backstop (documented in `sources/cricketdata.py`). It
  gives live/recent scores, status/result, start times and full scorecards (with
  full `dismissal-text` — richer than ESPN's how-out mode); the ESPN-only extras
  (points/standings/partnerships/FoW/timing/recent-lookback/upcoming) default
  safely and the renderers guard on them, so it degrades gracefully. Parity
  isn't worth it for a source only hit when ESPN is down.
- ~~**Two match-panel renderers duplicated.**~~ DONE: extracted `_labelled`,
  `_headline_line`, `_wrap_panel`.
- ~~**Hybrid augmentation.**~~ DONE: with a cricketdata.org key, `--match`
  upgrades ESPN's dismissal mode to cricketdata's full "c X b Y" text
  (`Aggregator.augment`), silent + aggressively cached. (Probed cricketdata for
  other richer fields — matchWinner/toss are redundant with ESPN, and there's no
  FoW/partnerships/player-of-match/umpires in its payload, so dismissals are the
  only worthwhile augmentation.)
- ~~**Config defaults for the newer toggles.**~~ DONE: `notify`, `standings` and
  `core_results` now read from `config.toml` (the store_true flags can still
  turn them on per-run).

### Small features

- **`--match` + `--refresh` as a "watch this game" mode.** The refresh loop
  already honours `--match`; confirm it works and document it as a feature.
- **Tab-completion for `--match`** (team names), alongside the existing
  `--team`/`--region`/`--domestic` completion.
- **Run-rate worm / over-by-over sparkline** for a chase — the data shape
  supports it, and it'd pair with the win-probability bar.

### Diminishing returns

- County ball-by-ball (not in the free feed), a better-trained Test model (needs
  data work), notifications beyond wicket/result.

## Deliberately not done

- **Full "c Fielder b Bowler" dismissals** — the summary scorecard only carries
  the dismissal *mode* ("caught", "bowled", …), which is what `--match` shows.
  The full text would mean scraping every page of ball-by-ball commentary
  (~25 pages for an ODI, far more for a Test) per match — too heavy.
- **Player of the match** — not present in the summary payload.
- **Top-performer "leaders" block** — available, but redundant with the full
  scorecard the `--match` view already shows.

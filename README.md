# 🏏 stumps

A command-line companion for cricket lovers. Run it and see the cricket you care
about, prioritised the way an England fan thinks about it:

1. **England first** — men's or women's, any format, always.
2. Then **top-tier Test matches** (two ICC full members) and **premier ICC
   tournaments** (World Cup, T20 World Cup, Champions Trophy, WTC final).
3. Then **English domestic** cricket — County Championship, One-Day Cup, Vitality
   Blast, The Hundred, women's regional competitions.
4. Everything else last (and only if it's a live international).

For each match you get the live score, current **batting & bowling figures**, a
**DLS par score** in limited-overs games (a "are they ahead or behind?"
indicator), end-of-day / stumps summaries for multi-day games, and a
**win-probability estimate**.

```
$ stumps
🏏 stumps   Mon 08 Jun 2026, 23:45
source: cricinfo

╭─  ● LIVE   New Zealand v India ───────────────────────────────────╮
│ India need 71 runs from 72 balls                                  │
│ New Zealand 280/8 (50.0 ov)   India 210/4 (38.0 ov)               │
│ Batting                                                           │
│ V Kohli *  92  88  7/1  105                                       │
│ ...                                                               │
│ DLS  21 ahead of DLS par (189)  · target 281  (Standard Edition)  │
│ Win probability                                                   │
│ India        █████████████████████░░░   86%                       │
│ New Zealand  ███░░░░░░░░░░░░░░░░░░░░░   14%                        │
│ Cricsheet-trained model · Estimate only — not CricViz WinViz      │
╰─ ODI · ICC Cricket World Cup 2027 · Eden Gardens, Kolkata ────────╯
```

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[winprob,dev]'      # winprob = numpy + scikit-learn for the model
```

## Usage

```bash
stumps                  # the prioritised report
stumps --all            # include every match, not just ones of interest
stumps --demo           # built-in sample data (offline; great for a quick look)
stumps --refresh 30     # live-refresh every 30s until Ctrl-C
stumps --limit 5        # cap how many matches are shown
stumps train            # train the win-probability model from Cricsheet
```

## Data sources

Live data uses, with automatic fallback:

1. **ESPNcricinfo** (`hs-consumer-api`) — no key required, richest data. It sits
   behind a CDN that can occasionally serve a challenge instead of JSON; if that
   happens we fall back to…
2. **cricketdata.org** (cricapi.com) — needs a free API key. Get one at
   <https://cricketdata.org/signup.aspx> and set it:
   ```bash
   export CRICKETDATA_API_KEY=your-key-here
   ```
3. **Demo data** — if both live sources are unavailable, `stumps` shows built-in
   sample matches, clearly labelled, so you always see *something*.

Responses are cached (default 30s, `STUMPS_CACHE_TTL` to change) to respect rate
limits.

## Win probability

This is a **home-grown estimate, not CricViz WinViz** (which is proprietary and
has no public API) and not ESPNcricinfo's Forecaster (not exposed as data).

- **Limited-overs chases** use a gradient-boosted model trained on ~8,000
  completed ODIs and T20s of Cricsheet ball-by-ball data (run `stumps train` to
  build/update it; it lands in `~/.cache/stumps/winprob_model.pkl`). Without a
  trained model — or without scikit-learn — it falls back to a transparent
  run-rate/wickets heuristic.
- **First innings** use a rough projected-score-vs-par heuristic.
- **Tests** use a crude lead/time-based lean (win/win/draw). Test win-probability
  is genuinely hard; treat these as a *lean*, not a number.

## DLS par scores

Computed with the **Standard Edition** resource table (the only edition published
openly — the Professional Edition that broadcasters use is proprietary). Expect
to land within ~1–2 runs of the official par for normal totals, diverging a
little more for very high first-innings scores (300+). Always labelled
"indicative".

## Tests

```bash
pytest
```

## Caveats

The live APIs are **unofficial and unsanctioned** — field shapes can change
without notice, in which case the normalisers in `src/stumps/sources/` may need a
tweak (every field access is defensive, so a shape change degrades to partial
data rather than crashing). This tool is for personal use; don't redistribute the
data commercially.

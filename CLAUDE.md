# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`stumps` is a CLI that shows a cricket fan the matches they care about, ranked.
The product spec drives the whole design, so know it: **England (men's/women's,
any format) first; then top-tier Tests + premier ICC tournaments; then English
domestic; then everything else.** Live score, batting/bowling figures, DLS par
score (limited overs), end-of-day summaries, and a win-probability estimate.

## Commands

```bash
# Setup (scikit-learn + numpy are needed only for training/the model)
python -m venv .venv && source .venv/bin/activate
pip install -e '.[winprob,dev]'

# Run
stumps                     # or: python -m stumps
stumps --demo              # offline sample data — use this when iterating
stumps train               # download Cricsheet + train the win-prob model

# Tests
pytest                     # all
pytest tests/test_dls.py   # one file
pytest tests/test_winprob.py -k heuristic   # one pattern
```

There is no network in the test suite — sources are tested via fixtures and
monkeypatched failures, so `pytest` is fast and offline. Prefer `stumps --demo`
over live runs while developing UI/logic.

## Architecture (the big picture)

Data flows in one direction through a **normalised domain model** so nothing
downstream cares where data came from:

```
sources/* ── Match objects ──> prioritise ──> render
  (raw API)   (models.py)        (policy)      (winprob + dls computed here)
```

- **`models.py`** is the contract. `Match`/`Innings`/`Batter`/`Bowler`/`Team`
  plus the `Format` and `Phase` enums. Every source maps its raw payload into
  these; `Format`/`Phase` carry the semantic helpers (`is_limited_overs`,
  `is_multi_day`, `overs_per_innings`, `is_active_today`) that the rest of the
  app branches on. Change this and you touch everything — do it deliberately.

- **`sources/`** — one module per data source, all implementing
  `DataSource.fetch_current_matches() -> list[Match]` and an optional
  `enrich(match)` (fetch detailed figures for matches we'll actually show).
  `aggregator.py` tries ESPN → cricketdata.org → demo data, in order, returning a
  `FetchResult`. `fixtures.py` is the offline `DemoSource` and the single source
  of sample data for `--demo` and tests.
  - **`espn.py` is the primary source.** It uses ESPN's open API via `curl_cffi`
    with Chrome TLS impersonation — this is *load-bearing*: ESPNcricinfo's CDN
    403s plain Python TLS, and the old `hs-consumer-api` 403s even with
    impersonation, so don't try to "simplify" back to httpx/that endpoint. The
    list comes from the scoreboard header; figures + structured innings
    (`linescores` with target) come from the per-event `summary` endpoint, used
    only to `enrich()` active matches.
  - **The live JSON shapes are reverse-engineered and unstable.** Every field
    access is defensive (`_dig`, `.get`) and degrades to partial data. When a
    live run returns empty/odd data, the fix is almost always in a normaliser
    here — not upstream. Format comes from `class.internationalClassId`
    (+ `generalClassCard`); phase from `fullStatus.type.state`.

- **`prioritise.py`** is the *policy*. `classify(match) -> Classification`
  assigns a `Tier` (ENGLAND/PREMIER/ENGLISH_DOMESTIC/OTHER); `prioritise()`
  filters + sorts (tier, then live-before-stumps-before-finished, then format).
  Classification depends on the allow-lists in `config.py` because **no feed has
  a clean flag** for "top-tier Test" / "World Cup" / "English county" — those are
  matched by team-name and series-name against the lists. Tuning what shows up =
  editing `config.py` lists, not the logic.

- **`dls/`** — Standard Edition par scores. `table.py` loads the verified
  resource grid from `data/dls_standard_resources.csv` (rows = overs remaining,
  cols = wickets lost) and interpolates; `par.py` implements ECB clause 5.6
  (`par_score`, `revised_target`). The CSV values are load-bearing and were
  transcribed + anchor-checked; don't "tidy" them. Only the Standard Edition is
  public; the Professional Edition broadcasters use is proprietary, so par scores
  are deliberately labelled "indicative".

- **`winprob/`** — the home-grown win estimate (**not** WinViz; it's proprietary
  with no API). `state.py` defines `FEATURE_ORDER` — the *single source of truth*
  for the feature vector, shared by training and inference so they can't drift.
  `cricsheet.py` downloads + parses Cricsheet ball-by-ball into chase rows;
  `train.py` fits a `HistGradientBoostingClassifier` and pickles
  `{model, order, ...}` to `~/.cache/stumps/winprob_model.pkl`. `estimator.py`
  loads that model if present (else a transparent heuristic) for limited-overs
  chases, a projection heuristic for first innings, and a crude lead/time lean
  for Tests. The trained model only covers **limited-overs second-innings
  chases** — that's the clean, well-defined case.

- **`render/console.py`** — all rich output. It *computes* the DLS par and win
  estimate per match (calling `dls`/`winprob`) rather than expecting them on the
  model. `cli.py` parses args and orchestrates fetch → prioritise → enrich(top N
  only, to respect rate limits) → render.

## Conventions / gotchas

- **Overs use cricket decimal notation** (`7.3` = 7 overs 3 balls, not 7.5).
  Convert with `winprob.state.overs_to_balls`; never treat `overs % 1` as a
  fraction of an over.
- **Win-probability labelling is a hard requirement** — output must never imply
  it's WinViz or an official figure. Keep the "estimate, not WinViz" note.
- The win-prob model artifact is pickled, so unpickling needs scikit-learn
  installed; `estimator._load_model` swallows the ImportError and returns None
  (→ heuristic). Keep that graceful path.
- Caches and the model live under `~/.cache/stumps/` (XDG-aware via
  `config.Settings`); they're disposable.

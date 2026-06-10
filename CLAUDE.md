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
    (+ `generalClassCard`); phase from `fullStatus.type.state`. For a finished
    game the result text comes from `fullStatus.type.detail` ("X won by N runs",
    "Match drawn/tied"), *not* `summary` — which is often the bare label
    "Result" (the renderer drops that via `_GENERIC_STATUS`). When the feed
    leaves us nothing usable, `render.console._synth_result` reconstructs a
    result *only* for the unambiguous limited-overs chase (the second innings
    carries a `target`, so the margin is readable); it deliberately bails on
    multi-day games (no draw signal) and D/L-affected ones (wrong margin).

- **`options.py`** holds `Preferences` — the user-facing choices (followed
  teams, region, domestic scene, filters, display toggles, JSON) resolved from
  `~/.config/stumps/config.toml` overlaid with CLI flags (`Preferences.resolve`).
  Threaded through `prioritise()` and the renderers. `config.Settings` stays
  infra-only (keys, cache, http, `region`). `--domestic` values pass through
  `config.resolve_domestic_key` (handles aliases/spaces → scene key), so don't
  use argparse `choices` for it. `completion.py` provides argcomplete-backed tab
  completion for `--team`/`--region`/`--domestic` (optional; no-op if argcomplete
  is absent). All 12 full members have a `config.DOMESTIC_SCENES` entry.

- **`prioritise.py`** is the *policy*. `classify(match, prefs) -> Classification`
  assigns a `Tier` (FOLLOWED/PREMIER/HOME_DOMESTIC/OTHER); `prioritise(matches,
  prefs)` filters (formats, gender, phase, series, tier floor, limit) + sorts
  (tier, then live-before-stumps-before-finished, then format). Classification
  depends on the allow-lists in `config.py` because **no feed has a clean flag**
  for "top-tier Test" / "World Cup" / "county" — matched by team/series name.
  "Home domestic" is generalised via `config.DOMESTIC_SCENES` (england/india/
  australia). Tuning what shows up = editing `config.py` lists, not the logic.

- **`dls/`** — Standard Edition par scores. `table.py` loads the verified
  resource grid from `data/dls_standard_resources.csv` (rows = overs remaining,
  cols = wickets lost) and interpolates; `par.py` implements ECB clause 5.6
  (`par_score`, `revised_target`). The CSV values are load-bearing and were
  transcribed + anchor-checked; don't "tidy" them. Only the Standard Edition is
  public; the Professional Edition broadcasters use is proprietary, so par scores
  are deliberately labelled "indicative".

- **`winprob/`** — the home-grown win estimate (**not** WinViz; it's proprietary
  with no API — and its endpoints carry no probability field anyway). Two feature
  contracts, each a *single source of truth* shared by training and inference so
  they can't drift: `state.py:FEATURE_ORDER` for the limited-overs chase, and
  `multiday.py:FEATURE_ORDER_MD` for Tests/first-class. `cricsheet.py` downloads
  + parses Cricsheet ball-by-ball into chase rows *and* multi-day rows;
  `train.py` fits a `HistGradientBoostingClassifier` for each — the chase model
  (binary, `~/.cache/stumps/winprob_model.pkl`) and the multi-day model (3-class
  win/lose/draw, `winprob_multiday_model.pkl`, via `stumps train --multiday`).
  `estimator.py` routes:
  - **limited-overs chase** → trained chase model if present, else a transparent
    run-rate heuristic; **first innings** → a projected-score heuristic.
  - **multi-day** → the **overs-aware heuristic by default** (`_test_estimate`):
    for a fourth innings it's a proper win/lose/**draw** 3-way (the side batting
    last can draw by surviving, so a big deficit is *not* a near-loss — this is
    what made the old aggregate-lead lean say "96% to the team that's actually
    going to draw"); innings 1–3 keep a lead-and-time lean. The opt-in trained
    multi-day model is used only with `--test-model` (`Preferences.use_multiday_model`),
    falling back to the heuristic if the model is absent.
  - **The dominant input for multi-day is overs remaining**, which no feed gives
    directly. `multiday.overs_remaining_estimate` reconstructs it from the
    scheduled close + present local time (parsed by `espn._apply_multiday_timing`
    from the summary `notes`: `hoursofplay` close, `matchdays` total days,
    `closeofplay` count → current day) at ~3.75 min/over (96/day for first-class,
    90 for Tests), falling back to a mid-day prior. The B model trains on a
    days-based proxy for the same quantity. Both are labelled "rough"/an estimate.

- **`render/console.py`** — all rich output; honours `Preferences` toggles
  (`--compact`, `--no-figures/-winprob/-dls/-commentary`, `--plain`). It
  *computes* the DLS par and win estimate per match (calling `dls`/`winprob`)
  rather than expecting them on the model. It also *synthesises* the status
  headline (`_headline`): a limited-overs chase becomes "require N runs from
  X.Y overs" (balls in the final over); a multi-day match in its fourth innings
  becomes "require N runs to win with W wickets remaining"
  (`_final_innings_target`), otherwise the lead/trail line; everything else falls
  back to the source's own `status_text`. `--compact`
  is one clipped line per match, leading with that headline. **`render/json_out.py`** is the
  `--json` path (stable schema for scripts/widgets). `cli.py` parses args, builds
  `Preferences`, and orchestrates fetch → prioritise → enrich(active matches
  only, to respect rate limits) → render (console or JSON).

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

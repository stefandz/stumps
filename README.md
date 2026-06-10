# 🏏 stumps

A command-line companion for cricket lovers. Run it and see the cricket you care
about, prioritised around the team(s) you follow (England by default, any team
via `--team`):

1. **Your team first** — men's or women's, any format, always.
2. Then **top-tier Test matches** (two ICC full members) and **premier ICC
   tournaments** (World Cup, T20 World Cup, Champions Trophy, WTC final).
3. Then your **home domestic** cricket — England's counties by default; India or
   Australia via `--domestic`.
4. Everything else last (and only if it's a live international).

For each match you get the live score, a synthesised headline that frames the
chase — "India require 71 runs from 12.0 overs" in limited-overs games (dropping
to balls in the final over), "Hampshire require 304 runs to win with 8 wickets
remaining" in the fourth innings of a Test or first-class match, "Hampshire
trail by 221" earlier on, and the outcome ("England Women won by 5 runs", "Match
drawn", "Match tied") once a game is finished — current
**batting & bowling figures**, the **last few balls** of commentary (with wickets
and boundaries flagged), a **DLS par score** in limited-overs games (a "are they
ahead or behind?" indicator), end-of-day / stumps summaries for multi-day games,
and a **win-probability estimate**.

```
$ stumps
🏏 stumps   Mon 08 Jun 2026, 23:45
source: cricinfo

╭─  ● LIVE   New Zealand v India ───────────────────────────────────╮
│ India require 71 runs from 12.0 overs                             │
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
stumps                  # the prioritised report (England by default)
stumps --team india     # follow India instead (repeatable: --team eng --team aus)
stumps --region in --domestic india   # tune coverage + your home domestic scene
stumps --compact        # one line per match
stumps --json           # machine-readable output (status bars, widgets, scripts)
stumps --live-only --format t20       # filter what's shown
stumps --womens-only    # or --mens-only
stumps --all            # include every match, not just ones of interest
stumps --refresh 30     # live-refresh every 30s until Ctrl-C
stumps --demo           # built-in sample data (offline; great for a quick look)
stumps train            # train the win-probability model from Cricsheet
```

`stumps --help` lists every flag, grouped into *follow*, *filtering*, *display*,
and *output*.

### Make it yours

Set your defaults once so you don't repeat flags every run. The easiest way is
the interactive helper:

```bash
stumps config            # wizard: team(s), region, domestic, API key
stumps config --show     # print current config
stumps config --team India --region in --domestic india   # set non-interactively
```

It writes `~/.config/stumps/config.toml` (chmod 600, since it may hold your key):

```toml
team = ["India", "Mumbai Indians"]   # who to put first
region = "in"                         # ESPN coverage region
domestic = "india"                    # home domestic scene (any full member, or none)
cricketdata_api_key = "…"            # optional fallback-source key
```

CLI flags always override the config file.

`--domestic` understands every ICC full member — `england`, `india`,
`australia`, `pakistan`, `south-africa`, `new-zealand`, `sri-lanka`,
`bangladesh`, `west-indies`, `afghanistan`, `ireland`, `zimbabwe` — plus short
aliases (`sa`, `nz`, `windies`, …) and `none`.

### Shell tab-completion

`--team` and `--region` (and `--domestic`) support tab-completion. Enable it once:

```bash
eval "$(register-python-argcomplete stumps)"   # add to ~/.bashrc or ~/.zshrc
```

Then `stumps --team Aus<TAB>` → `Australia`, `stumps --region <TAB>` lists region
codes, etc.

### Teams & domestic scenes you can follow

`--team` does a case-insensitive substring match, so the simplest string wins:
`--team england` also catches *England Women*, *England Lions*, *England A*.

**International** — every ICC full member:

```
afghanistan  australia  bangladesh  england  india  ireland
new zealand  pakistan   south africa  sri lanka  west indies  zimbabwe
```

Associate nations work by name too (e.g. `scotland`, `netherlands`, `nepal`,
`namibia`, `usa`, `uae`, `oman`, `papua new guinea`).

**Domestic scenes** (`--domestic <key>`, aliases in brackets) — a few example
`--team` strings from each:

| Scene | Example team strings |
|---|---|
| `england` (`uk`) | Surrey · Lancashire · Oval Invincibles · Birmingham Phoenix |
| `india` (`ind`) | Mumbai Indians · Delhi Capitals · Punjab Kings · Gujarat Titans |
| `australia` (`aus`) | Sixers · Scorchers · Hurricanes · New South Wales |
| `pakistan` (`pak`) | Karachi Kings · Lahore Qalandars · Multan Sultans · Peshawar Zalmi |
| `south-africa` (`sa`) | MI Cape Town · Paarl Royals · Pretoria Capitals · Dolphins |
| `new-zealand` (`nz`) | Auckland · Canterbury · Otago · Wellington Firebirds |
| `sri-lanka` (`sl`) | Jaffna Kings · Galle · Colombo Strikers · Kandy Falcons |
| `bangladesh` (`ban`) | Comilla Victorians · Rangpur Riders · Fortune Barishal · Khulna Tigers |
| `west-indies` (`wi`, `windies`) | Trinbago Knight Riders · Guyana Amazon Warriors · Barbados Royals · Jamaica Tallawahs |
| `afghanistan` (`afg`) | Band-e-Amir · Mis Ainak · Amo Sharks · Boost Defenders |
| `ireland` (`ire`) | Leinster Lightning · Northern Knights · Munster Reds · North-West Warriors |
| `zimbabwe` (`zim`) | Mountaineers · Mid West Rhinos · Matabeleland Tuskers · Mashonaland Eagles |

The domestic scene also matches its competitions by name (IPL, Big Bash, PSL,
SA20, CPL, County Championship, …), so you'll see those even for teams not listed
above.

## Data sources

Live data uses, with automatic fallback:

1. **ESPN open cricket API** — no key required, richest data. ESPNcricinfo's CDN
   blocks standard Python TLS handshakes (403), so we use `curl_cffi` to
   impersonate Chrome's TLS fingerprint, which gets the free
   `site.api.espn.com` scoreboard + per-match summary endpoints through (the
   same approach as the [`cricdata`](https://github.com/arnavbonigala/cricdata)
   project). No login, no key.
2. **cricketdata.org** (cricapi.com) — fallback; needs a free API key. Get one at
   <https://cricketdata.org/signup.aspx> and either set `CRICKETDATA_API_KEY` or
   put it in `~/.config/stumps/config.toml`:
   ```toml
   cricketdata_api_key = "your-key-here"
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

# 🏏 stumps

A command-line companion for cricket lovers. Run it and see the cricket you care
about, prioritised around the team(s) you follow (England by default, any team
via `--team`):

1. **Your team first** — men's or women's, any format, always.
2. Then **top-tier Test matches** (two ICC full members) and **premier ICC
   tournaments** (World Cup, T20 World Cup, Champions Trophy, WTC final).
3. Then your **home domestic** cricket — England's counties by default; India or
   Australia via `--domestic`.
4. Everything else last — and by default only a **full-member international**
   (live, and lingering briefly after it finishes so a game you were watching
   doesn't vanish; `--core-results` keeps history to your teams only).
   Associate-vs-associate games need `--all` (or `--tier all`).

For each match you get the live score, a synthesised headline that frames the
chase — "India require 71 runs from 12.0 overs" in limited-overs games (dropping
to balls in the final over), "Hampshire require 304 runs to win with 8 wickets
remaining" in the fourth innings of a Test or first-class match, "Hampshire
trail by 221" earlier on, and the outcome ("England Women won by 5 runs", "Match
drawn", "Match tied") once a game is finished — current
**batting & bowling figures**, the **last few balls** of commentary (with wickets
and boundaries flagged), a **DLS par score** in limited-overs games (a "are they
ahead or behind?" indicator), end-of-day / stumps summaries for multi-day games,
the **points awarded** for finished league/tournament games and, inline, **where
the two teams sit** in their league (county championship and the first-class /
limited-overs leagues worldwide), and a **win-probability estimate**.

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
│ DLS  21 ahead of DLS par (189)  · target 281                      │
│ Win probability                                                   │
│ India        █████████████████████░░░   86%                       │
│ New Zealand  ███░░░░░░░░░░░░░░░░░░░░░   14%                        │
╰─ ODI · ICC Cricket World Cup 2027 · Eden Gardens, Kolkata ────────╯
```

**Recently-finished results** are shown by default for the matches you care
about (followed teams + your home domestic + premier games), tagged
"Today"/"Yesterday". By default that's the last day; `--results N` widens the
window (one cached fetch per day), `--no-results` turns it off. This pulls in
games even after they drop off the live feed — so you won't miss yesterday's
England result.

**Your followed teams' last result and next fixture always show**, however far
outside those windows they fall — for both the men's and women's senior side
(so following England surfaces England Men *and* England Women). Off-season or
mid-tour, you always see where they last finished and when they're next on.
`--no-last-next` turns it off.

**Men's / women's labelling.** Where a nation fields both, the match *title*
names the side by gender — "England Men v New Zealand Men", "England Women v
Sri Lanka Women" — while mentions *inside* the panel stay prosaic ("England
287/4"), since the title has set the context. It's display-only (the `--json`
output is unchanged). `--no-gender-labels` turns it off.

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
stumps --oneline        # one plain status line for the top match (tmux/polybar)
stumps --live-only --format t20       # filter what's shown
stumps --womens-only    # or --mens-only
stumps --match england  # drill into one match: the full scorecard (all innings)
stumps --match england --refresh 30   # watch a single match, redraw every 30s
stumps --standings      # append the league/points table for each competition
stumps --results 3      # also show the last 3 days of followed/domestic results
stumps --no-results     # only today's matches (don't look back)
stumps --core-results   # keep recent results to your core teams only
stumps --upcoming 7     # also show your teams' fixtures over the next 7 days
stumps --no-last-next   # don't force-show each followed team's last/next game
stumps --no-gender-labels   # don't label paired national sides by gender in titles
stumps --all            # include every match, not just ones of interest
stumps --refresh 30     # live-refresh every 30s until Ctrl-C
stumps --refresh 30 --notify   # + desktop alert on a wicket/result for your team
stumps --demo           # built-in sample data (offline; great for a quick look)
stumps --test-model     # use the trained multi-day model for Tests (opt-in)
stumps train            # train the limited-overs chase model from Cricsheet
stumps train --multiday # train the optional Test/first-class win/lose/draw model
```

`stumps --help` lists every flag, grouped into *follow*, *filtering*, *display*,
and *output*.

### Drill into a match — `--match`

`--match TEXT` picks a single match (a case-insensitive substring of the teams
or series — "v" and "vs" both work, e.g. `--match "england vs india"`) and shows
the **full scorecard**: every innings in batting order with how-out, the fall of
wickets, an over-by-over sparkline, full bowling figures, partnerships drawn as a
back-to-back bar of each batter's runs on a shared centre line, plus the toss and
the umpires. Captains are marked **`(c)`**; **`*`** is the not-out batter (and,
live, the one on strike). Pair it with `--refresh` to *watch* a single match.

```
$ stumps --match "england v india"
╭─  ✓ RESULT   England v India ──────────────────────────────────────────╮
│ England won by 14 runs                                                 │
│ Toss  India, elected to field first                                    │
│                                                                        │
│ 1st innings — England 182/6  (20.0 ov)                                 │
│ Batting             R     B    4s/6s     SR    how out                 │
│ P Salt             55    38      7/2    145    caught                  │
│ J Buttler (c)      43    29      4/2    148    bowled                  │
│ H Brook            31    22      2/1    141    lbw                     │
│ L Livingstone *    28    16      1/2    175    not out                 │
│ J Bairstow *       12    15      1/0     80    not out                 │
│ Fall  1-98 (Salt, 10.2) · 2-142 (Buttler, 14.3) · 3-182 (Brook, 19.5)  │
│ Over by over  ▃▅▂▄▇▃▆█▂▅▄▆▃▇▅▄▆█▄▆  RR 9.1                              │
│ Bowling             O    M     R    W    Econ                          │
│ J Bumrah          4.0    0    28    2     7.0                          │
│ K Yadav           4.0    0    33    2     8.2                          │
│ Partnerships                                                           │
│  1st   98 (10.2)    Salt 55 ████████████│█████████    Buttler 40       │
│  2nd   44 ( 4.1)  Buttler 3            █│███████      Brook 31         │
│  3rd   40 ( 5.3)    Brook 0             │█████        Livingstone 22   │
│ …                                                                      │
│ Umpires  M Erasmus · R Kettleborough                                   │
╰─ Today · T20I · India tour of England 2026 · Lord's, London ───────────╯
```

- **Partnerships bar:** left (cyan) is the first batter's runs, right (magenta)
  the second's, scaled so the centre line lines up down the innings — an
  at-a-glance read of who made the runs.
- **Over by over:** one block per over scaled to runs, wicket overs in red.
- **how-out** shows the dismissal *mode* by default ("caught"). With a
  cricketdata.org key it becomes the full text ("c X b Y") — see below.
- The **wicketkeeper isn't marked**: the feed reports a player's *role*, not who
  kept in a given match, so it can't be determined reliably.

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
cricketdata_api_key = "…"            # optional fallback / augmentation key
results_days = 2                      # days of past results to pull in (0 = off)
upcoming_days = 3                     # days of upcoming fixtures to pull in (0 = off)
last_next = true                      # always show followed teams' last result + next fixture
gender_labels = true                  # label paired national sides by gender in titles
notify = true                         # desktop alerts on a wicket/result (with --refresh)
standings = true                      # always append league tables
core_results = false                  # keep history to your core teams only
```

CLI flags override the config file (the boolean toggles can be turned *on* by
their flag; to turn one off, drop it from the config).

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
limits. You're only told the source has changed when it matters: a normal run
just shows the cricket; the header notes `source: cached (as of …)` when serving
the last-good offline snapshot, or `(demo data — live sources unavailable)` when
both live sources are down.

### Hybrid: fuller scorecards with a cricketdata.org key

ESPN is the primary source, but its scorecard only gives the dismissal *mode*
("caught", "bowled"). If you set a **cricketdata.org key**, `stumps --match`
upgrades each how-out to the **full text** — "c Beth Mooney b Lucy Hamilton",
"run out (Wareham)" — by looking the same match up on cricketdata and merging
its richer dismissals into ESPN's card.

It's **best-effort and silent**: with no key, on a quota breach, or when the
match can't be matched, you simply keep ESPN's mode — never an error (ESPN
working is all you need). Dismissals never change once a wicket falls, so the
data is cached hard (a finished match's card for a week), and the lookup only
happens for the one match you drill into — so it's gentle on the API.

**Free vs paid:** cricketdata's free tier allows ~100 requests/day, which is
ample for occasional `--match` use given the aggressive caching. If you drill
into many different matches a day (or want it as a routine fallback too), a
[paid cricketdata plan](https://cricketdata.org/) lifts the limit — but it's
entirely optional; everything else works without a key.

## Win probability

This is a **home-grown estimate, not CricViz WinViz** (which is proprietary and
has no public API) and not ESPNcricinfo's Forecaster (not exposed as data).

- **Limited-overs chases** use a gradient-boosted model trained on ~8,000
  completed ODIs and T20s of Cricsheet ball-by-ball data (run `stumps train` to
  build/update it; it lands in `~/.cache/stumps/winprob_model.pkl`). Without a
  trained model — or without scikit-learn — it falls back to a transparent
  run-rate/wickets heuristic.
- **First innings** use a rough projected-score-vs-par heuristic.
- **Tests / first-class** use an **overs-aware win/lose/draw** estimate. The key
  driver of a draw is how much time is left, so `stumps` reconstructs the overs
  remaining from the scheduled close and the current local time at the ground.
  In a fourth innings it models all three outcomes properly — the side batting
  last can secure a draw simply by surviving, so a big lead is *not* a near-win
  (e.g. "281 to win, 8 wickets, ~24 overs left" comes out ~96% Draw, not 96% to
  the side that's bowling).

  This heuristic is the **default**. There's also an **optional trained model**
  (3-class, from Cricsheet Test data): build it with `stumps train --multiday`,
  then enable it per-run with `stumps --test-model`. It falls back to the
  heuristic if no model is present. Either way, treat Test win-probability as a
  *lean* — it's genuinely hard, and always labelled an estimate.

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

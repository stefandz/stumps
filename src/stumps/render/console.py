"""Render prioritised matches to the terminal with rich.

Each match becomes a panel: the headline status, innings scores, current
batting & bowling figures (when live), the DLS par line for limited-overs
chases, and a win-probability bar — labelled as an estimate, not WinViz.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from stumps import bonus, dls
from stumps.dls.par import G50_ASSOCIATE_OR_WOMENS_ODI, G50_FULL_MEMBER
from stumps.models import Format, Innings, Match, Phase, Standings
from stumps.options import Preferences
from stumps.prioritise import Classification, Tier
from stumps.sources.aggregator import FetchResult
from stumps.winprob import estimate, extract_chase_state
from stumps.winprob.estimator import WinEstimate

_PHASE_STYLE = {
    Phase.LIVE: ("● LIVE", "bold white on red"),
    Phase.STUMPS: ("◐ STUMPS", "bold black on yellow"),
    Phase.BREAK: ("⏸ BREAK", "bold black on yellow"),
    Phase.COMPLETE: ("✓ RESULT", "bold white on green4"),
    Phase.UPCOMING: ("◌ UPCOMING", "dim"),
    Phase.ABANDONED: ("✗ ABANDONED", "dim"),
    Phase.UNKNOWN: ("?", "dim"),
}

_TIER_ACCENT = {
    Tier.FOLLOWED: "bright_cyan",
    Tier.PREMIER: "magenta",
    Tier.HOME_DOMESTIC: "green",
    Tier.OTHER: "white",
}

def _accent(match: Match, cls: Classification) -> str:
    # The frame encodes relevance (tier); phase is carried by the section header
    # and the badge.
    return _TIER_ACCENT.get(cls.tier, "white")


# -- gender labelling (display-only; the domain model / --json are untouched) --
#
# Blended scheme: the match *title* names paired national sides by gender —
# men's internationals gain " Men" (women's titles already read "… Women" from
# the feed), so a glance distinguishes the squads — while every mention *inside*
# the panel drops the qualifier ("England 287/4", not "England Women 287/4"),
# because the title has already set the context. All of this is render-time only.

def _gender_suffix(name: str) -> str:
    for suffix in (" Women", " Men"):
        if name.endswith(suffix):
            return suffix
    return ""


def _plain_name(name: str, labels: bool = True) -> str:
    """A team name with its gender qualifier dropped — for prosaic mentions
    inside a panel ("England Women" -> "England"). No-op when labels are off."""
    if not labels:
        return name
    suffix = _gender_suffix(name)
    return name[: -len(suffix)] if suffix else name


def _plain_text(text: str, match: Match, labels: bool = True) -> str:
    """Drop the gender qualifier from any of the match's team names where they
    appear in free text (a synthesised headline, the feed's status prose)."""
    if not labels or not text:
        return text
    # Longest names first, so "England Women" is handled before any "England".
    for name in sorted(match.team_names, key=len, reverse=True):
        base = _plain_name(name)
        if base != name:
            text = text.replace(name, base)
    return text


def _match_title(match: Match, labels: bool = True) -> str:
    """The panel title. Men's internationals are labelled "X Men v Y Men" (both
    sides are nations in that format); women's internationals already carry
    "… Women" from the feed, and domestic titles are left as-is."""
    if labels and match.format.is_international and not match.is_womens:
        return " v ".join(f"{n} Men" for n in match.team_names)
    return match.title


#: Display sections, in order. Each match falls in exactly one (by phase); within
#: a section they keep their relevance order. Headers give clear division.
_SECTIONS = (
    ("● Live", lambda p: p.is_active_today or p is Phase.UNKNOWN),
    ("✓ Results", lambda p: p in (Phase.COMPLETE, Phase.ABANDONED)),
    ("◌ Upcoming", lambda p: p is Phase.UPCOMING),
)

#: Bare state labels that the phase badge already conveys — never worth showing
#: as a headline (e.g. a finished game whose only status text is "Result"). The
#: break labels live here too because the BREAK badge now names the interval
#: itself (see `_break_badge_label`), so echoing the bare word adds nothing.
_GENERIC_STATUS = {
    "live", "stumps", "tea", "lunch", "drinks", "close", "close of play",
    "result", "stump", "final", "completed", "match ended", "end of match",
    "rain", "bad light", "innings break", "break", "scheduled", "upcoming",
}

#: Specific break labels, most-specific first, matched as whole words against
#: the status text so the otherwise-generic "⏸ BREAK" badge can name the actual
#: interval (tea / lunch / rain / ...). Keeps the colour + pause glyph the user
#: likes while preserving the information the generic label threw away.
_BREAK_LABELS = (
    ("innings break", "INNINGS"),
    ("bad light", "BAD LIGHT"),
    ("lunch", "LUNCH"),
    ("tea", "TEA"),
    ("drinks", "DRINKS"),
    ("rain", "RAIN"),
)


def _break_badge_label(match: Match) -> str:
    text = match.status_text.lower()
    for keyword, label in _BREAK_LABELS:
        if re.search(rf"\b{re.escape(keyword)}\b", text):
            return f"⏸ {label}"
    return "⏸ BREAK"


def _phase_badge(match: Match) -> Text:
    label, style = _PHASE_STYLE.get(match.phase, ("?", "dim"))
    if match.phase is Phase.BREAK:
        label = _break_badge_label(match)
    return Text(f" {label} ", style=style)


def _local_start(iso: str) -> str:
    """ISO UTC start time -> local 'Sat 13 Jun, 09:00' (or '' if unparseable).

    A tz-naive timestamp (e.g. cricketdata's GMT times carry no 'Z') is treated
    as UTC rather than local."""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().strftime("%a %d %b, %H:%M")


def _finished_label(match: Match) -> str:
    """A relative-day tag for a finished match ("Today"/"Yesterday"/"Sat 07 Jun"),
    so pulled-in past results read clearly. Empty for matches still in progress."""
    if match.phase is not Phase.COMPLETE:
        return ""
    if not match.finished_on:
        return "Today"  # in the live feed as finished -> it finished today
    try:
        d = date.fromisoformat(match.finished_on)
    except ValueError:
        return ""
    delta = (date.today() - d).days
    if delta <= 0:
        return "Today"
    if delta == 1:
        return "Yesterday"
    return d.strftime("%a %d %b")


def _subtitle(match: Match) -> Text:
    bits = []
    label = _finished_label(match)
    if label:
        bits.append(label)
    bits.append(match.format.value)
    if match.series_name:
        bits.append(match.series_name)
    if match.venue:
        bits.append(match.venue)
    if match.day_number and match.total_days:
        bits.append(f"Day {match.day_number}/{match.total_days}")
    return Text(" · ".join(bits), style="dim")


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _league_line(match: Match, labels: bool = True) -> Text | None:
    """Where the match's two teams currently sit in their league table — e.g.
    "Surrey 2nd (89 pts) · Hampshire 9th (53 pts)". None unless we have standings
    that include them (bilateral series and knockouts won't)."""
    table = match.standings
    if not table or not table.rows:
        return None
    parts = []
    for team in match.teams:
        row = next((r for r in table.rows
                    if r.team.lower() in team.name.lower()
                    or team.name.lower() in r.team.lower()), None)
        if row:
            parts.append(f"{_plain_name(team.name, labels)} {_ordinal(row.rank)} ({row.points} pts)")
    if not parts:
        return None
    txt = Text()
    txt.append("League  ", style="bold")
    txt.append(" · ".join(parts), style="dim")
    return txt


def _scores_line(match: Match, labels: bool = True) -> Text:
    txt = Text()
    for i, inns in enumerate(match.innings):
        if i:
            txt.append("   ")
        txt.append(f"{_plain_name(inns.batting_team, labels)} ", style="bold")
        txt.append(inns.score)
        if inns.overs and not inns.all_out:
            txt.append(f" ({inns.overs:.1f} ov)", style="dim")
    return txt


def _short_name(match: Match, team_name: str) -> str:
    for t in match.teams:
        if t.name.lower() in team_name.lower() or team_name.lower() in t.name.lower():
            return t.short_name
    return team_name.split()[0] if team_name else "?"


def oneline(match: Match, labels: bool = True) -> str:
    """A single plain-text status line for the top match — for tmux / polybar /
    a menu bar. No panels, colour or markup; uses team abbreviations."""
    if match.innings:
        scores = "  ".join(
            f"{_short_name(match, i.batting_team)} {i.score}" for i in match.innings)
    else:
        scores = " v ".join(t.short_name for t in match.teams)
    out = f"🏏 {scores}"
    headline = _headline(match, labels)
    if headline and headline.strip().lower() not in _GENERIC_STATUS:
        out += f" — {headline}"
    return out


def _figures_table(section: str) -> Table:
    """A figures table whose header row doubles as the column legend, with the
    section name ('Batting'/'Bowling') sitting over the player-name column."""
    t = Table(
        box=None,
        show_header=True,
        header_style="bold dim",
        padding=(0, 2),
        pad_edge=False,
    )
    t.add_column(section, style="white")
    return t


def _batting_table(inns, *, full: bool = False) -> Table | None:
    batters = inns.batters if full else (
        [b for b in inns.batters if b.not_out] or inns.batters[:2])
    if not batters:
        return None
    t = _figures_table("Batting")
    t.add_column("R", justify="right")  # runs
    t.add_column("B", justify="right")  # balls faced
    t.add_column("4s/6s", justify="right")
    t.add_column("SR", justify="right")  # strike rate
    if full:
        t.add_column("how out", style="dim")
    for b in batters:
        starred = b.on_strike or (full and b.not_out)
        label = b.name + (" (c)" if b.captain else "") + (" *" if starred else "")
        name = Text(label, style="bold" if b.on_strike else "")
        row = [name, str(b.runs), str(b.balls), f"{b.fours}/{b.sixes}",
               f"{b.strike_rate:.0f}"]
        if full:
            row.append(b.dismissal or ("not out" if b.not_out else ""))
        t.add_row(*row)
    return t


def _bowling_table(inns, *, full: bool = False) -> Table | None:
    bowlers = inns.bowlers if full else (
        [b for b in inns.bowlers if b.bowling_now] or inns.bowlers[:2])
    if not bowlers:
        return None
    t = _figures_table("Bowling")
    t.add_column("O", justify="right")  # overs
    t.add_column("M", justify="right")  # maidens
    t.add_column("R", justify="right")  # runs conceded
    t.add_column("W", justify="right")  # wickets
    t.add_column("Econ", justify="right")  # economy rate
    for b in bowlers:
        name = Text(b.name + (" →" if b.bowling_now else ""),
                    style="bold" if b.bowling_now else "")
        t.add_row(name, f"{b.overs:.1f}", str(b.maidens), str(b.runs),
                  str(b.wickets), f"{b.economy:.1f}")
    return t


def _lead_trail(match: Match) -> str:
    """Lead/trail line for a multi-day game, once both sides have batted."""
    names = match.team_names
    if len(names) < 2:
        return ""

    def total(team: str) -> int:
        return sum(i.runs for i in match.innings if team.lower() in i.batting_team.lower())

    def has_batted(team: str) -> bool:
        return any(team.lower() in i.batting_team.lower() for i in match.innings)

    current = match.current_innings
    if current is None:
        return ""
    batting = current.batting_team
    others = [n for n in names if n.lower() not in batting.lower()]
    if not others or not (has_batted(batting) and has_batted(others[0])):
        return ""
    net = total(batting) - total(others[0])
    if net > 0:
        return f"{batting} lead by {net}"
    if net < 0:
        return f"{batting} trail by {-net}"
    return "Scores level"


def _balls_to_overs(balls: int) -> str:
    """Balls remaining -> cricket decimal-over notation (8 -> '1.2')."""
    return f"{balls // 6}.{balls % 6}"


def _final_innings_target(match: Match) -> tuple[str, int, int] | None:
    """For a multi-day match in its final (4th) innings, the chasing team, the
    runs they still need to win, and their wickets remaining. None if it's not
    yet a fourth-innings chase or the target has already been overhauled."""
    if not match.format.is_multi_day or len(match.innings) < 4:
        return None
    current = match.current_innings
    if current is None:
        return None
    batting = current.batting_team

    def total(team: str) -> int:
        return sum(i.runs for i in match.innings if team.lower() in i.batting_team.lower())

    others = [n for n in match.team_names if n.lower() not in batting.lower()]
    if not others:
        return None
    to_win = total(others[0]) - total(batting) + 1
    if to_win <= 0:
        return None
    wickets_remaining = max(0, 10 - current.wickets)
    return batting, to_win, wickets_remaining


def _headline(match: Match, labels: bool = True) -> str:
    """Best status line: a synthesised chase/lead phrase for active matches,
    else the source's own status (result, schedule, rain note...). Team mentions
    are made prosaic (gender qualifier dropped) when `labels` is on — the panel
    title already carries the distinction."""
    return _plain_text(_headline_raw(match), match, labels)


def _headline_raw(match: Match) -> str:
    if match.phase.is_active_today:
        chase = extract_chase_state(match)
        if chase and chase.runs_needed > 0 and chase.balls_remaining > 0:
            runs = "run" if chase.runs_needed == 1 else "runs"
            # Overs are the natural unit, but in the final over every ball
            # counts — switch to balls there, as live coverage does.
            if chase.balls_remaining <= 6:
                ball_word = "ball" if chase.balls_remaining == 1 else "balls"
                span = f"{chase.balls_remaining} {ball_word}"
            else:
                span = f"{_balls_to_overs(chase.balls_remaining)} overs"
            return (
                f"{chase.chasing_team} require {chase.runs_needed} {runs} "
                f"from {span}"
            )
        if match.format.is_multi_day:
            target = _final_innings_target(match)
            if target:
                team, to_win, wkts = target
                runs = "run" if to_win == 1 else "runs"
                wkt_word = "wicket" if wkts == 1 else "wickets"
                return (
                    f"{team} require {to_win} {runs} to win "
                    f"with {wkts} {wkt_word} remaining"
                )
            line = _lead_trail(match)
            if line:
                return line
    elif match.phase is Phase.COMPLETE:
        # The feed's result text is authoritative; only synthesise one when it
        # gave us nothing usable (empty, or the bare label "Result").
        if match.status_text.strip().lower() in _GENERIC_STATUS or not match.status_text.strip():
            synth = _synth_result(match)
            if synth:
                return synth
    return match.status_text


def _multiday_margin(match: Match) -> str:
    """Reconstruct a finished multi-day result line *with margin* from the
    `winner` flag and the innings aggregates. The three forms are determined by
    who batted last and how many innings each side played:

    - winner batted last and passed the target -> "won by N wickets"
      (N = 10 - wickets lost in that innings);
    - winner bowled last; the side batting last fell short -> "won by N runs"
      (N = winner's aggregate - loser's aggregate);
    - winner batted once, loser twice -> "won by an innings and N runs".

    Falls back to a bare "{winner} won" whenever the innings data is too thin or
    inconsistent to compute a margin safely (collapsed scoreboard, < 3 innings,
    a non-positive winning aggregate, an implausible wicket count). The feed's
    own "won by ..." text is preferred over this whenever it's present — this is
    only reached when the feed gave us a bare label."""
    winner = match.winner
    bare = f"{winner} won"
    inns = match.innings
    if len(inns) < 3:  # a real multi-day result needs >= 3 innings
        return bare

    def belongs(innings: Innings, team: str) -> bool:
        return _names_match(innings.batting_team, team)

    others = [n for n in match.team_names if not _names_match(n, winner)]
    if len(others) != 1:
        return bare
    loser = others[0]

    win_inns = [i for i in inns if belongs(i, winner)]
    lose_inns = [i for i in inns if belongs(i, loser)]
    if not win_inns or not lose_inns:
        return bare
    win_total = sum(i.runs for i in win_inns)
    lose_total = sum(i.runs for i in lose_inns)
    if win_total <= lose_total:  # winner must out-aggregate the loser
        return bare

    last = max(inns, key=lambda i: i.number)
    if belongs(last, winner):  # winner chased and won
        wkts = 10 - last.wickets
        if not 1 <= wkts <= 10:
            return bare
        unit = "wicket" if wkts == 1 else "wickets"
        return f"{winner} won by {wkts} {unit}"

    margin = win_total - lose_total
    if len(win_inns) == 1 and len(lose_inns) >= 2:  # innings victory
        runs = "run" if margin == 1 else "runs"
        return f"{winner} won by an innings and {margin} {runs}"
    runs = "run" if margin == 1 else "runs"
    return f"{winner} won by {margin} {runs}"


def _names_match(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return bool(a) and (a in b or b in a)


def _synth_result(match: Match) -> str | None:
    """Best-effort result line for a finished match when the feed gave us no
    usable text (just "Result"/"Final").

    Multi-day games lean on the feed's authoritative `winner` flag (a finished
    game with no winner is a draw) and reconstruct the margin from the innings
    aggregates (`_multiday_margin`), falling back to a bare "X won" when the
    innings data is too thin to be sure. Limited-overs games are derived from
    the chase: with a target we give the full margin ("won by N runs/wickets");
    D/L-affected ones are skipped (the visible totals would mislead)."""
    if match.format.is_multi_day:
        if not match.innings:
            return None
        if match.winner:
            return _multiday_margin(match)
        return "Match drawn"
    if not match.format.is_limited_overs or len(match.innings) < 2:
        return None
    blob = f"{match.status_text} {match.result_text}".lower()
    if any(k in blob for k in ("dls", "d/l", "duckworth")):
        return None

    chase = next((i for i in match.innings if (i.target or 0) > 0), None)
    if chase is not None:
        target = chase.target
        if chase.runs >= target:  # chase completed
            wkts = max(0, 10 - chase.wickets)
            unit = "wicket" if wkts == 1 else "wickets"
            return f"{chase.batting_team} won by {wkts} {unit}"
        if chase.runs == target - 1:  # scores level, chase over
            return "Match tied"
        defending = next(
            (n for n in match.team_names if n.lower() != chase.batting_team.lower()),
            None,
        )
        if defending is None:
            return None
        margin = (target - 1) - chase.runs
        unit = "run" if margin == 1 else "runs"
        return f"{defending} won by {margin} {unit}"

    # No target on either innings — winner by totals, margin omitted.
    first, second = match.innings[0], match.innings[1]
    if first.runs == second.runs:
        return "Match tied"
    winner = first if first.runs > second.runs else second
    return f"{winner.batting_team} won"


def _recent_balls_block(match: Match, limit: int = 6) -> Group | None:
    if not match.recent_balls:
        return None
    rows: list = [Text("Recent", style="bold dim")]
    for b in match.recent_balls[:limit]:
        line = Text()
        line.append(f"{b.over:>5}  ", style="dim")
        if b.is_wicket:
            line.append("W ", style="bold white on red")
        elif b.is_boundary:
            line.append(f"{b.runs} ", style="bold green")
        line.append(b.description)
        rows.append(line)
    return Group(*rows)


def _g50_for(match: Match) -> float:
    if match.format in {Format.WODI, Format.LIST_A} and match.is_womens:
        return G50_ASSOCIATE_OR_WOMENS_ODI
    return G50_FULL_MEMBER


def _dls_line(match: Match) -> Text | None:
    state = extract_chase_state(match)
    if state is None or not match.first_innings:
        return None
    overs = match.format.overs_per_innings
    if not overs:
        return None
    result = dls.par_score(
        first_innings_runs=match.first_innings.runs,
        overs_per_innings=overs,
        team2_overs_used=state.balls_bowled / 6.0,
        team2_wickets_lost=state.wickets_lost,
        team2_score=state.runs,
        g50=_g50_for(match),
    )
    txt = Text()
    txt.append("DLS  ", style="bold yellow")
    txt.append(result.status_phrase)
    txt.append(f"  · target {result.target}", style="dim")
    return txt


def _winprob_bar(label: str, prob: float, accent: str, width: int = 24) -> Text:
    filled = int(round(prob * width))
    label = (label[:16] + "…") if len(label) > 17 else label
    bar = Text()
    bar.append(f"{label:<18}", style="white")
    bar.append("█" * filled, style=accent)
    bar.append("░" * (width - filled), style="dim")
    bar.append(f" {prob * 100:4.0f}%", style="bold")
    return bar


def _winprob_block(est: WinEstimate, accent: str, labels: bool = True) -> Group:
    rows: list = [Text("Win probability", style=f"bold {accent}")]
    ordered = sorted(est.probabilities.items(), key=lambda kv: kv[1], reverse=True)
    for label, prob in ordered:
        rows.append(_winprob_bar(_plain_name(label, labels), prob, accent))
    return Group(*rows)


def _compact_line(match: Match, cls: Classification, labels: bool = True) -> Text:
    """One-line-per-match summary for --compact."""
    accent = _accent(match, cls)
    label, style = _PHASE_STYLE.get(match.phase, ("?", "dim"))
    line = Text()
    line.append(f"{label:<11}", style=style)
    line.append("  ")
    line.append(_match_title(match, labels), style=f"bold {accent}")
    # Lead with the synthesised headline (the chase target / result) — it's the
    # most useful bit, and compact lines are clipped to one row, so the verbose
    # innings list trails where it can be truncated without losing the story.
    headline = _headline(match, labels)
    if headline and headline.strip().lower() not in _GENERIC_STATUS:
        line.append(f"  — {headline}", style="dim")
    scores = "  ".join(
        f"{i.batting_team.split()[0] if i.batting_team else '?'} {i.score}"
        for i in match.innings
    )
    if scores:
        line.append(f"  {scores}")
    return line


_SPARK = "▁▂▃▄▅▆▇█"


def _over_sparkline(inns) -> Text | None:
    """An over-by-over manhattan: one block per over scaled to runs, wicket overs
    in red, with the innings run rate. Empty unless we have per-over data."""
    overs = inns.over_scores
    if not overs:
        return None
    peak = max((o.runs for o in overs), default=0)
    line = Text()
    line.append("Over by over  ", style="bold")
    for o in overs:
        level = round(o.runs / peak * (len(_SPARK) - 1)) if peak else 0
        line.append(_SPARK[level], style="red" if o.wickets else "cyan")
    line.append(f"  RR {inns.run_rate:.1f}", style="dim")
    return line


def _fall_of_wickets_line(inns) -> Text | None:
    """Classic fall-of-wickets line: "Fall  1-27 (Burns, 9.2) · 2-53 …"."""
    if not inns.fall_of_wickets:
        return None
    line = Text()
    line.append("Fall  ", style="bold")
    for i, w in enumerate(inns.fall_of_wickets):
        if i:
            line.append(" · ", style="dim")
        line.append(f"{w.wicket}-{w.team_runs}")
        detail = [x for x in (w.batter.split()[-1] if w.batter else "", w.over) if x]
        if detail:
            line.append(f" ({', '.join(detail)})", style="dim")
    return line


#: Colours for the two batters in a partnership's diverging bar.
_PARTNERSHIP_LEFT = "cyan"
_PARTNERSHIP_RIGHT = "magenta"


def _partnerships_block(inns, half: int = 12) -> Group | None:
    """Partnerships as a back-to-back bar: batter 1's runs grow left of a shared
    centre line, batter 2's grow right, scaled to the biggest contribution so
    the centre line aligns across every row.

    Some matches give the partnership total but not the per-batter split — then
    we list the stands plainly (no misleading empty bars)."""
    if not inns.partnerships:
        return None

    if not any(p.runs1 or p.runs2 for p in inns.partnerships):
        rows: list = [Text("Partnerships", style="bold dim")]
        for p in inns.partnerships:
            line = Text()
            line.append(f"{p.wicket:>4}  ", style="bold")
            line.append(f"{p.runs:>3} ({p.overs:>4})  ", style="dim")
            line.append(" & ".join(x for x in (p.batter1, p.batter2) if x))
            rows.append(line)
        return Group(*rows)

    def label(name: str, runs: int) -> str:
        # Surname only, to keep the bar compact and avoid wrapping.
        return f"{name.split()[-1] if name else '?'} {runs}"

    peak = max((max(p.runs1, p.runs2) for p in inns.partnerships), default=0) or 1
    lw = max(len(label(p.batter1, p.runs1)) for p in inns.partnerships)
    rw = max(len(label(p.batter2, p.runs2)) for p in inns.partnerships)

    rows: list = [Text("Partnerships", style="bold dim")]
    for p in inns.partnerships:
        left = round(p.runs1 / peak * half)
        right = round(p.runs2 / peak * half)
        line = Text()
        line.append(f"{p.wicket:>4}  ", style="bold")
        line.append(f"{p.runs:>3} ({p.overs:>4})  ", style="dim")
        line.append(label(p.batter1, p.runs1).rjust(lw) + " ")
        line.append(" " * (half - left))
        line.append("█" * left, style=_PARTNERSHIP_LEFT)
        line.append("│", style="dim")
        line.append("█" * right, style=_PARTNERSHIP_RIGHT)
        line.append(" " * (half - right))
        line.append(" " + label(p.batter2, p.runs2).ljust(rw))
        rows.append(line)
    return Group(*rows)


def _fmt_points(value: float) -> str:
    """Whole points lose the ".0" (County/Plunket); halves stay (Shield 1.5)."""
    return f"{value:g}"


def _bonus_block(match: Match, labels: bool = True) -> Group | None:
    """First-innings batting/bowling bonus points earned so far, per team — the
    figure no feed gives live, so it's computed from the competition's rules and
    labelled as such. None when the competition has no scheme we know."""
    rows = bonus.match_bonus(match)
    if rows is None:
        return None
    rule = bonus.rule_for(match.series_name)
    approx = any(r.approx for r in rows)

    def cell(value: float, seen: bool, mark: bool) -> str:
        if not seen:
            return "–"  # that innings hasn't happened yet
        return f"{_fmt_points(value)}{'~' if mark and value else ''}"

    t = Table(box=None, show_header=True, header_style="bold dim",
              padding=(0, 2), pad_edge=False)
    t.add_column("Team")
    for col in ("Bat", "Bowl", "Total"):
        t.add_column(col, justify="right")
    for r in rows:
        t.add_row(
            _plain_name(r.team, labels),
            cell(r.batting, r.batting_seen, r.approx),
            cell(r.bowling, r.bowling_seen, r.approx),
            Text(_fmt_points(r.total), style="bold"),
        )

    window = rule.window if rule else "first innings"
    caption = f"Bonus points · {window} · computed, not official"
    head = Text(caption, style="bold dim")
    out: list = [head, t]
    if approx:
        out.append(Text("~ past the over limit — from current score, may overstate",
                        style="dim italic"))
    return Group(*out)


def _standings_panel(standings: Standings, accent: str, labels: bool = True) -> Panel:
    rows = standings.rows
    # Multi-day tables have draws; limited-overs tables have a net run rate (and
    # sometimes qualification flags). Show only the columns that apply.
    show_draw = any(r.drawn for r in rows)
    show_nrr = any(r.nrr is not None for r in rows)
    show_q = any(r.qualified for r in rows)

    t = Table(box=None, show_header=True, header_style="bold dim",
              padding=(0, 2), pad_edge=False)
    t.add_column("#", justify="right")
    t.add_column("Team")
    for col in ("P", "W", "L"):
        t.add_column(col, justify="right")
    if show_draw:
        t.add_column("D", justify="right")
    t.add_column("Pts", justify="right")
    if show_nrr:
        t.add_column("NRR", justify="right")

    for row in rows:
        team = Text(_plain_name(row.team, labels))
        if show_q and row.qualified:
            team.append("  Q", style="bold green")
        cells = [str(row.rank), team, str(row.played), str(row.won), str(row.lost)]
        if show_draw:
            cells.append(str(row.drawn))
        cells.append(Text(str(row.points), style="bold"))
        if show_nrr:
            cells.append(f"{row.nrr:+.3f}" if row.nrr is not None else "—")
        t.add_row(*cells)
    return Panel(t, title=Text(standings.name, style=f"bold {accent}"),
                 title_align="left", border_style=accent, padding=(0, 1))


def _labelled(label: str, value: str) -> Text:
    """A "Label  value" line — bold label, dim value (Points / Toss / Starts /
    Umpires)."""
    line = Text()
    line.append(f"{label}  ", style="bold")
    line.append(value, style="dim")
    return line


def _headline_line(match: Match, labels: bool = True) -> Text | None:
    """The synthesised/source headline, unless it's a bare state word the badge
    already conveys."""
    headline = _headline(match, labels)
    if headline and headline.strip().lower() not in _GENERIC_STATUS:
        return Text(headline, style="bold")
    return None


def _wrap_panel(match: Match, cls: Classification, body: list,
                labels: bool = True) -> Panel:
    """Frame a body in the standard match panel (badge + title, subtitle, tier
    border). An empty body becomes a muted placeholder rather than an empty
    frame (e.g. a match listed before any scorecard exists)."""
    accent = _accent(match, cls)
    if not body:
        note = match.status_text.strip()
        if not note or note.lower() in _GENERIC_STATUS:
            note = "No score yet" if match.phase.is_active_today else "Yet to start"
        body = [Text(note, style="dim italic")]
    title = Text()
    title.append(_phase_badge(match))
    title.append("  ")
    title.append(_match_title(match, labels), style=f"bold {accent}")
    return Panel(Group(*body), title=title, title_align="left",
                 subtitle=_subtitle(match), subtitle_align="left",
                 border_style=accent, padding=(0, 1))


def _match_panel(
    match: Match, cls: Classification, settings, prefs: Preferences
) -> Panel:
    accent = _accent(match, cls)
    labels = prefs.gender_labels
    body: list = []
    hl = _headline_line(match, labels)
    if hl is not None:
        body.append(hl)
    if match.phase is Phase.UPCOMING and match.starts_at:
        when = _local_start(match.starts_at)
        if when:
            body.append(_labelled("Starts", when))
    scores = _scores_line(match, labels)
    if scores.plain.strip():
        body.append(scores)
    if match.phase is Phase.COMPLETE and match.points:
        body.append(_labelled("Points", match.points))
    if prefs.show_table:
        league = _league_line(match, labels)
        if league is not None:
            body.append(league)

    # In-play indicators (figures, DLS par, win probability) only make sense
    # while a match is active. Each is individually toggleable.
    if match.phase.is_active_today:
        if prefs.show_figures:
            inns = match.current_innings
            if inns is not None:
                bat = _batting_table(inns)
                bowl = _bowling_table(inns)
                if bat is not None:
                    body.append(bat)
                if bowl is not None:
                    body.append(bowl)
                spark = _over_sparkline(inns)
                if spark is not None:
                    body.append(spark)
        if prefs.show_dls:
            dls_line = _dls_line(match)
            if dls_line is not None:
                body.append(dls_line)
        if prefs.show_commentary:
            recent = _recent_balls_block(match, prefs.balls)
            if recent is not None:
                body.append(recent)
        if prefs.show_winprob:
            est = estimate(match, settings,
                           use_multiday_model=prefs.use_multiday_model)
            if est is not None:
                body.append(_winprob_block(est, accent, labels))

    return _wrap_panel(match, cls, body, labels)


def render_match_detail(
    console: Console, match: Match, cls: Classification, settings,
    prefs: Preferences,
) -> None:
    """Full drill-down for a single match: the whole scorecard, innings by
    innings (every batter with how-out + every bowler), plus the usual headline,
    points, DLS, win probability and recent balls."""
    accent = _accent(match, cls)
    labels = prefs.gender_labels
    body: list = []

    hl = _headline_line(match, labels)
    if hl is not None:
        body.append(hl)
    if match.phase is Phase.COMPLETE and match.points:
        body.append(_labelled("Points", match.points))
    if prefs.show_table:
        league = _league_line(match, labels)
        if league is not None:
            body.append(league)
    if match.toss:
        body.append(_labelled("Toss", match.toss))
    bonus_block = _bonus_block(match, labels)
    if bonus_block is not None:
        body.append(Text(""))
        body.append(bonus_block)

    for inns in match.innings:
        body.append(Text(""))  # spacer between innings
        head = Text(f"{_ordinal(inns.number)} innings — "
                    f"{_plain_name(inns.batting_team, labels)} {inns.score}",
                    style=f"bold {accent}")
        if inns.overs and not inns.all_out:
            head.append(f"  ({inns.overs:.1f} ov)", style="dim")
        body.append(head)
        bat = _batting_table(inns, full=True)
        if bat is not None:
            body.append(bat)
        fow = _fall_of_wickets_line(inns)
        if fow is not None:
            body.append(fow)
        spark = _over_sparkline(inns)
        if spark is not None:
            body.append(spark)
        # Blank lines between the batting+fall block, the bowling card and the
        # partnerships so the innings doesn't read as one dense slab.
        bowl = _bowling_table(inns, full=True)
        if bowl is not None:
            body.append(Text(""))
            body.append(bowl)
        pship = _partnerships_block(inns)
        if pship is not None:
            body.append(Text(""))
            body.append(pship)

    if match.phase.is_active_today:
        if prefs.show_dls:
            dls_line = _dls_line(match)
            if dls_line is not None:
                body.append(Text(""))
                body.append(dls_line)
        if prefs.show_winprob:
            est = estimate(match, settings,
                           use_multiday_model=prefs.use_multiday_model)
            if est is not None:
                body.append(_winprob_block(est, accent, labels))
    if prefs.show_commentary:
        recent = _recent_balls_block(match, prefs.balls)
        if recent is not None:
            body.append(recent)

    if match.officials:
        body.append(_labelled("Umpires", " · ".join(match.officials)))

    console.print(_wrap_panel(match, cls, body, labels))


def render_report(
    console: Console,
    result: FetchResult,
    ranked: list[tuple[Match, Classification]],
    settings,
    prefs: Preferences | None = None,
    *,
    when: str = "",
) -> None:
    """Render the full prioritised report."""
    prefs = prefs or Preferences()
    header = Text()
    header.append("🏏 stumps", style="bold bright_cyan")
    if when:
        header.append(f"   {when}", style="dim")
    console.print(header)

    if result.stale_as_of:
        src_note = f"source: cached (as of {result.stale_as_of}) — live sources unavailable"
    else:
        src_note = f"source: {result.source.name}"
        if result.used_fallback:
            src_note += "  (demo data — live sources unavailable)"
    console.print(Text(src_note, style="dim"))
    for notice in result.notices:
        console.print(Text("· " + notice, style="dim yellow"))
    console.print()

    if not ranked:
        console.print(Text("No matches of interest right now.", style="dim"))
        return

    # Group into phase sections (live now / recently finished / coming up); within
    # each, `ranked` is already in relevance order (tier, then format, then time).
    for title, in_section in _SECTIONS:
        section = [(m, c) for m, c in ranked if in_section(m.phase)]
        if not section:
            continue
        console.rule(Text(title, style="bold"), align="left", style="dim")
        for match, cls in section:
            if prefs.compact:
                console.print(_compact_line(match, cls, prefs.gender_labels),
                              no_wrap=True, overflow="ellipsis")
            else:
                console.print(_match_panel(match, cls, settings, prefs))

    if prefs.show_standings:
        seen: set[str] = set()
        for match, cls in ranked:
            table = match.standings
            if table and table.rows and table.name not in seen:
                seen.add(table.name)
                console.print(_standings_panel(table, _accent(match, cls),
                                               prefs.gender_labels))

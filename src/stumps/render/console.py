"""Render prioritised matches to the terminal with rich.

Each match becomes a panel: the headline status, innings scores, current
batting & bowling figures (when live), the DLS par line for limited-overs
chases, and a win-probability bar — labelled as an estimate, not WinViz.
"""

from __future__ import annotations

import re
from datetime import date

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from stumps import dls
from stumps.dls.par import G50_ASSOCIATE_OR_WOMENS_ODI, G50_FULL_MEMBER
from stumps.models import Format, Match, Phase, Standings
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

#: A finished match gets a green frame (matching its ✓ RESULT badge) regardless
#: of tier — "this is settled" reads more usefully here than the tier colour.
_COMPLETE_ACCENT = "green4"


def _accent(match: Match, cls: Classification) -> str:
    if match.phase is Phase.COMPLETE:
        return _COMPLETE_ACCENT
    return _TIER_ACCENT.get(cls.tier, "white")

#: Bare state labels that the phase badge already conveys — never worth showing
#: as a headline (e.g. a finished game whose only status text is "Result"). The
#: break labels live here too because the BREAK badge now names the interval
#: itself (see `_break_badge_label`), so echoing the bare word adds nothing.
_GENERIC_STATUS = {
    "live", "stumps", "tea", "lunch", "drinks", "close", "close of play",
    "result", "stump", "final", "completed", "match ended", "end of match",
    "rain", "bad light", "innings break", "break",
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


def _league_line(match: Match) -> Text | None:
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
            parts.append(f"{team.name} {_ordinal(row.rank)} ({row.points} pts)")
    if not parts:
        return None
    txt = Text()
    txt.append("League  ", style="bold")
    txt.append(" · ".join(parts), style="dim")
    return txt


def _scores_line(match: Match) -> Text:
    txt = Text()
    for i, inns in enumerate(match.innings):
        if i:
            txt.append("   ")
        txt.append(f"{inns.batting_team} ", style="bold")
        txt.append(inns.score)
        if inns.overs and not inns.all_out:
            txt.append(f" ({inns.overs:.1f} ov)", style="dim")
    return txt


def _short_name(match: Match, team_name: str) -> str:
    for t in match.teams:
        if t.name.lower() in team_name.lower() or team_name.lower() in t.name.lower():
            return t.short_name
    return team_name.split()[0] if team_name else "?"


def oneline(match: Match) -> str:
    """A single plain-text status line for the top match — for tmux / polybar /
    a menu bar. No panels, colour or markup; uses team abbreviations."""
    if match.innings:
        scores = "  ".join(
            f"{_short_name(match, i.batting_team)} {i.score}" for i in match.innings)
    else:
        scores = " v ".join(t.short_name for t in match.teams)
    out = f"🏏 {scores}"
    headline = _headline(match)
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
        name = Text(b.name + (" *" if starred else ""),
                    style="bold" if b.on_strike else "")
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


def _headline(match: Match) -> str:
    """Best status line: a synthesised chase/lead phrase for active matches,
    else the source's own status (result, schedule, rain note...)."""
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


def _synth_result(match: Match) -> str | None:
    """Best-effort result line for a finished match when the feed gave us no
    usable text (just "Result"/"Final").

    Multi-day games lean on the feed's authoritative `winner` flag (a finished
    game with no winner is a draw); deriving the margin from scores is left off,
    since the full innings list carries it. Limited-overs games are derived from
    the chase: with a target we give the full margin ("won by N runs/wickets");
    D/L-affected ones are skipped (the visible totals would mislead)."""
    if match.format.is_multi_day:
        if not match.innings:
            return None
        if match.winner:
            return f"{match.winner} won"
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


def _winprob_block(est: WinEstimate, accent: str) -> Group:
    rows: list = [Text("Win probability", style=f"bold {accent}")]
    ordered = sorted(est.probabilities.items(), key=lambda kv: kv[1], reverse=True)
    for label, prob in ordered:
        rows.append(_winprob_bar(label, prob, accent))
    return Group(*rows)


def _compact_line(match: Match, cls: Classification) -> Text:
    """One-line-per-match summary for --compact."""
    accent = _accent(match, cls)
    label, style = _PHASE_STYLE.get(match.phase, ("?", "dim"))
    line = Text()
    line.append(f"{label:<11}", style=style)
    line.append("  ")
    line.append(match.title, style=f"bold {accent}")
    # Lead with the synthesised headline (the chase target / result) — it's the
    # most useful bit, and compact lines are clipped to one row, so the verbose
    # innings list trails where it can be truncated without losing the story.
    headline = _headline(match)
    if headline and headline.strip().lower() not in _GENERIC_STATUS:
        line.append(f"  — {headline}", style="dim")
    scores = "  ".join(
        f"{i.batting_team.split()[0] if i.batting_team else '?'} {i.score}"
        for i in match.innings
    )
    if scores:
        line.append(f"  {scores}")
    return line


def _standings_panel(standings: Standings, accent: str) -> Panel:
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
        team = Text(row.team)
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


def _match_panel(
    match: Match, cls: Classification, settings, prefs: Preferences
) -> Panel:
    accent = _accent(match, cls)
    body: list = []

    # Status headline — synthesised ("require 71 from 12.0 overs", "trail by 245") where we
    # can, else the source's. Skip bare state words (the badge already says it).
    headline = _headline(match)
    if headline and headline.strip().lower() not in _GENERIC_STATUS:
        body.append(Text(headline, style="bold"))

    scores = _scores_line(match)
    if scores.plain.strip():
        body.append(scores)

    # Points awarded, for a finished league/tournament game (county championship,
    # the various first-class/limited-overs leagues — anywhere the feed tracks a
    # table). Absent for bilateral series.
    if match.phase is Phase.COMPLETE and match.points:
        pts = Text()
        pts.append("Points  ", style="bold")
        pts.append(match.points, style="dim")
        body.append(pts)

    if prefs.show_table:
        league = _league_line(match)
        if league is not None:
            body.append(league)

    # In-play indicators (figures, DLS par, win probability) only make sense
    # while a match is active — live, at a break, or paused at stumps. For a
    # finished match the result is already on the status line, and showing a
    # win % for a settled game is just noise. Each is individually toggleable.
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
                body.append(_winprob_block(est, accent))

    # A match can be listed before any scorecard exists (just toss, or feed lag).
    # Show a muted placeholder rather than an empty frame.
    if not body:
        note = match.status_text.strip()
        if not note or note.lower() in _GENERIC_STATUS:
            note = "No score yet" if match.phase.is_active_today else "Yet to start"
        body.append(Text(note, style="dim italic"))

    title = Text()
    title.append(_phase_badge(match))
    title.append("  ")
    title.append(match.title, style=f"bold {accent}")

    return Panel(
        Group(*body),
        title=title,
        title_align="left",
        subtitle=_subtitle(match),
        subtitle_align="left",
        border_style=accent,
        padding=(0, 1),
    )


_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}


def render_match_detail(
    console: Console, match: Match, cls: Classification, settings,
    prefs: Preferences,
) -> None:
    """Full drill-down for a single match: the whole scorecard, innings by
    innings (every batter with how-out + every bowler), plus the usual headline,
    points, DLS, win probability and recent balls."""
    accent = _accent(match, cls)
    body: list = []

    headline = _headline(match)
    if headline and headline.strip().lower() not in _GENERIC_STATUS:
        body.append(Text(headline, style="bold"))
    if match.phase is Phase.COMPLETE and match.points:
        pts = Text()
        pts.append("Points  ", style="bold")
        pts.append(match.points, style="dim")
        body.append(pts)
    if prefs.show_table:
        league = _league_line(match)
        if league is not None:
            body.append(league)

    for inns in match.innings:
        body.append(Text(""))  # spacer between innings
        head = Text(f"{_ORDINALS.get(inns.number, str(inns.number))} innings — "
                    f"{inns.batting_team} {inns.score}", style=f"bold {accent}")
        if inns.overs and not inns.all_out:
            head.append(f"  ({inns.overs:.1f} ov)", style="dim")
        body.append(head)
        bat = _batting_table(inns, full=True)
        if bat is not None:
            body.append(bat)
        bowl = _bowling_table(inns, full=True)
        if bowl is not None:
            body.append(bowl)

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
                body.append(_winprob_block(est, accent))
    if prefs.show_commentary:
        recent = _recent_balls_block(match, prefs.balls)
        if recent is not None:
            body.append(recent)

    if not body:
        body.append(Text("No score yet", style="dim italic"))

    title = Text()
    title.append(_phase_badge(match))
    title.append("  ")
    title.append(match.title, style=f"bold {accent}")
    console.print(Panel(Group(*body), title=title, title_align="left",
                        subtitle=_subtitle(match), subtitle_align="left",
                        border_style=accent, padding=(0, 1)))


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

    for match, cls in ranked:
        if prefs.compact:
            console.print(_compact_line(match, cls), no_wrap=True,
                          overflow="ellipsis")
        else:
            console.print(_match_panel(match, cls, settings, prefs))

    if prefs.show_standings:
        seen: set[str] = set()
        for match, cls in ranked:
            table = match.standings
            if table and table.rows and table.name not in seen:
                seen.add(table.name)
                console.print(_standings_panel(table, _accent(match, cls)))

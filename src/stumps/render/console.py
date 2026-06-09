"""Render prioritised matches to the terminal with rich.

Each match becomes a panel: the headline status, innings scores, current
batting & bowling figures (when live), the DLS par line for limited-overs
chases, and a win-probability bar — labelled as an estimate, not WinViz.
"""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from stumps import dls
from stumps.dls.par import G50_ASSOCIATE_OR_WOMENS_ODI, G50_FULL_MEMBER
from stumps.models import Format, Match, Phase
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
    Tier.ENGLAND: "bright_cyan",
    Tier.PREMIER: "magenta",
    Tier.ENGLISH_DOMESTIC: "green",
    Tier.OTHER: "white",
}


def _phase_badge(match: Match) -> Text:
    label, style = _PHASE_STYLE.get(match.phase, ("?", "dim"))
    return Text(f" {label} ", style=style)


def _subtitle(match: Match) -> Text:
    bits = [match.format.value]
    if match.series_name:
        bits.append(match.series_name)
    if match.venue:
        bits.append(match.venue)
    if match.day_number and match.total_days:
        bits.append(f"Day {match.day_number}/{match.total_days}")
    return Text(" · ".join(bits), style="dim")


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


def _batting_table(inns) -> Table | None:
    active = [b for b in inns.batters if b.not_out] or inns.batters[:2]
    if not active:
        return None
    t = _figures_table("Batting")
    t.add_column("R", justify="right")  # runs
    t.add_column("B", justify="right")  # balls faced
    t.add_column("4s/6s", justify="right")
    t.add_column("SR", justify="right")  # strike rate
    for b in active:
        name = Text(b.name + (" *" if b.on_strike else ""),
                    style="bold" if b.on_strike else "")
        t.add_row(name, str(b.runs), str(b.balls), f"{b.fours}/{b.sixes}",
                  f"{b.strike_rate:.0f}")
    return t


def _bowling_table(inns) -> Table | None:
    bowlers = [b for b in inns.bowlers if b.bowling_now] or inns.bowlers[:2]
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
    txt.append("  (Standard Edition, indicative)", style="dim italic")
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
    method_tag = {
        "model": "Cricsheet-trained model",
        "heuristic": "rule-of-thumb (no model loaded)",
        "first-innings-heuristic": "first-innings projection",
        "test-heuristic": "rough Test lean",
        "settled": "match decided",
    }.get(est.method, est.method)
    for line in est.extra:
        rows.append(Text(line, style="dim"))
    rows.append(Text(f"{method_tag} · {est.note}", style="dim italic"))
    return Group(*rows)


def _match_panel(match: Match, cls: Classification, settings) -> Panel:
    accent = _TIER_ACCENT.get(cls.tier, "white")
    body: list = []

    # Status headline — skip bare state words (the phase badge already says it).
    if match.status_text and match.status_text.strip().lower() not in {
        "live", "stumps", "tea", "lunch", "drinks", "close", "close of play",
    }:
        body.append(Text(match.status_text, style="bold"))

    body.append(_scores_line(match))

    # In-play indicators (figures, DLS par, win probability) only make sense
    # while a match is active — live, at a break, or paused at stumps. For a
    # finished match the result is already on the status line, and showing a
    # win % for a settled game is just noise.
    if match.phase.is_active_today:
        inns = match.current_innings
        if inns is not None:
            bat = _batting_table(inns)
            bowl = _bowling_table(inns)
            if bat is not None:
                body.append(bat)
            if bowl is not None:
                body.append(bowl)

        dls_line = _dls_line(match)
        if dls_line is not None:
            body.append(dls_line)

        est = estimate(match, settings)
        if est is not None:
            body.append(_winprob_block(est, accent))

    if cls.reasons:
        body.append(Text("Why shown: " + "; ".join(cls.reasons), style="dim italic"))

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


def render_report(
    console: Console,
    result: FetchResult,
    ranked: list[tuple[Match, Classification]],
    settings,
    *,
    when: str = "",
) -> None:
    """Render the full prioritised report."""
    header = Text()
    header.append("🏏 stumps", style="bold bright_cyan")
    if when:
        header.append(f"   {when}", style="dim")
    console.print(header)

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
        console.print(_match_panel(match, cls, settings))

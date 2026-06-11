"""Command-line entry point for ``stumps``.

  stumps                      # prioritised cricket for your team(s)
  stumps --team india         # follow India instead of the England default
  stumps --region in --domestic india
  stumps --live-only --format t20    # filter what's shown
  stumps --compact            # one line per match
  stumps --json               # machine-readable output
  stumps --refresh 30         # live-refresh every 30 seconds
  stumps config               # interactive setup of your defaults
  stumps train                # train the win-probability model from Cricsheet

Defaults (team, region, domestic) can be set in ~/.config/stumps/config.toml.
"""
# PYTHON_ARGCOMPLETE_OK

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from rich.console import Console

from stumps import completion
from stumps.config import load_config_file, load_settings
from stumps.models import Phase
from stumps.options import Preferences
from stumps.prioritise import prioritise
from stumps.render import render_report
from stumps.render.json_out import render_json
from stumps.sources.aggregator import Aggregator
from stumps.sources.base import SourceError


def _show_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stumps",
        description="Prioritised cricket scores for the team(s) you follow.",
        epilog=(
            "--team matches any team name (case-insensitive substring), so "
            "'england' also catches England Women/Lions. --domestic supports "
            "every ICC full member (england, india, australia, pakistan, "
            "south-africa, new-zealand, sri-lanka, bangladesh, west-indies, "
            "afghanistan, ireland, zimbabwe) plus aliases (sa, nz, windies, …). "
            "The README's 'Teams & domestic scenes' section lists good strings "
            "for each, and --team/--region/--domestic support tab-completion "
            '(eval "$(register-python-argcomplete stumps)").'
        ),
    )

    a = p.add_argument_group("who/what to follow")
    team_action = a.add_argument("--team", "--follow", action="append", metavar="NAME",
                                 help="team to put first (repeatable). Default: England")
    a.add_argument("--no-team", action="store_true",
                   help="don't prioritise any team (rank by tournament/domestic only)")
    region_action = a.add_argument("--region", metavar="CODE",
                                   help="ESPN scoreboard region: gb, in, au, … (default gb)")
    domestic_action = a.add_argument("--domestic", metavar="COUNTRY",
                   help="home domestic scene: any full member (e.g. india, "
                        "south-africa), or none")
    completion.attach(team_action, region_action, domestic_action)

    b = p.add_argument_group("filtering")
    b.add_argument("--all", action="store_true",
                   help="show every match, not just ones of interest")
    b.add_argument("--tier", choices=["followed", "premier", "domestic", "all"],
                   help="lowest relevance tier to include (default domestic)")
    b.add_argument("--format", action="append", choices=["test", "odi", "t20", "hundred"],
                   help="restrict to format(s) (repeatable)")
    b.add_argument("--live-only", action="store_true",
                   help="only matches in play (live / break / stumps)")
    b.add_argument("--no-finished", action="store_true", help="hide finished matches")
    b.add_argument("--no-upcoming", action="store_true", help="hide upcoming matches")
    gender = b.add_mutually_exclusive_group()
    gender.add_argument("--mens-only", action="store_true")
    gender.add_argument("--womens-only", action="store_true")
    b.add_argument("--series", metavar="TEXT", help="only series whose name contains TEXT")
    b.add_argument("--include-warmups", action="store_true",
                   help="treat World Cup warm-ups/qualifiers as premier")
    b.add_argument("--limit", type=int, default=None,
                   help="cap the number of matches shown")
    b.add_argument("--results", type=int, metavar="DAYS", default=None,
                   help="include finished results from the last DAYS days for "
                        "followed/domestic/premier matches (default 1)")
    b.add_argument("--no-results", action="store_true",
                   help="don't pull in past days' results (only today's)")

    c = p.add_argument_group("display")
    c.add_argument("--compact", action="store_true", help="one line per match")
    c.add_argument("--no-figures", action="store_true", help="hide batting/bowling")
    c.add_argument("--no-winprob", action="store_true", help="hide win probability")
    c.add_argument("--no-dls", action="store_true", help="hide DLS par")
    c.add_argument("--no-commentary", action="store_true", help="hide recent balls")
    c.add_argument("--standings", action="store_true",
                   help="append the league/points table for each competition shown")
    c.add_argument("--balls", type=int, default=None, metavar="N",
                   help="how many recent balls to show (default 6)")
    c.add_argument("--test-model", action="store_true",
                   help="use the trained multi-day model for Test/first-class "
                        "win probability (needs `stumps train --multiday`); "
                        "the heuristic is the default")
    c.add_argument("--plain", action="store_true", help="disable colour")

    o = p.add_argument_group("output / data")
    o.add_argument("--json", action="store_true", help="machine-readable JSON output")
    o.add_argument("--demo", action="store_true",
                   help="use built-in sample data (offline)")
    o.add_argument("--no-enrich", action="store_true",
                   help="skip fetching detailed batting/bowling figures")
    o.add_argument("--refresh", type=int, metavar="SECONDS", default=None,
                   help="redraw every SECONDS until interrupted")
    o.add_argument("--notify", action="store_true",
                   help="desktop notification on a wicket/result for your "
                        "followed teams (with --refresh)")
    o.add_argument("--width", type=int, default=None, help="force console width")
    return p


def _train_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stumps train",
        description="Train a win-probability model from Cricsheet. Defaults to "
                    "the limited-overs chase model; --multiday trains the "
                    "Test/first-class win/lose/draw model.",
    )
    p.add_argument("--multiday", action="store_true",
                   help="train the multi-day (Test/first-class) model instead of "
                        "the limited-overs chase model")
    p.add_argument("--formats", nargs="+", default=None,
                   choices=["odi", "t20i", "test"],
                   help="Cricsheet bundles to train on "
                        "(default: odi t20i, or test with --multiday)")
    p.add_argument("--max-matches", type=int, default=None,
                   help="limit matches parsed (for a quick run)")
    p.add_argument("--sample-every", type=int, default=None,
                   help="keep one training row per N balls "
                        "(default 6 for chase, 12 for --multiday)")
    p.add_argument("--force-download", action="store_true",
                   help="re-download Cricsheet bundles even if cached")
    return p


def _run_show(args: argparse.Namespace) -> int:
    prefs = Preferences.resolve(args, load_config_file())
    settings = load_settings()
    settings.region = prefs.region
    console = Console(width=args.width, no_color=args.plain)
    agg = Aggregator(settings, demo_only=args.demo)
    notify_state: dict = {}

    def run_once() -> None:
        result = agg.fetch(lookback_days=prefs.results_days)
        ranked = prioritise(result.matches, prefs)
        if not args.no_enrich:
            # Fetch detailed scorecards for matches we'll show figures for (live /
            # break / stumps) and for finished ones — multi-day games need it to
            # recover the full innings list (the scoreboard collapses "421 &
            # 259/5d" to one innings per side), and any finished league game needs
            # it for the points awarded (only in the per-event summary). Upcoming
            # games don't need it.
            agg.enrich(result, [
                m for m, _ in ranked
                if m.phase.is_active_today or m.phase is Phase.COMPLETE
            ])
        if prefs.notify:
            from stumps import notify
            events, new_state = notify.detect_events(notify_state, ranked)
            notify_state.clear()
            notify_state.update(new_state)
            for event in events:
                notify.send(event)
        when = datetime.now().strftime("%a %d %b %Y, %H:%M")
        if prefs.json_output:
            print(render_json(result, ranked, settings, prefs, when=when))
        else:
            render_report(console, result, ranked, settings, prefs, when=when)

    if args.refresh:
        try:
            while True:
                if not prefs.json_output:
                    console.clear()
                run_once()
                if not prefs.json_output:
                    console.print(
                        f"\n[dim]refreshing every {args.refresh}s — Ctrl-C to quit[/dim]"
                    )
                time.sleep(args.refresh)
        except KeyboardInterrupt:
            console.print("\n[dim]bye 👋[/dim]")
            return 0
    else:
        run_once()
    return 0


def _config_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stumps config",
        description="Set up ~/.config/stumps/config.toml (interactive by default).",
    )
    p.add_argument("--show", action="store_true", help="print current config and exit")
    p.add_argument("--team", "--follow", action="append", metavar="NAME",
                   help="set followed team(s) non-interactively (repeatable)")
    p.add_argument("--region", metavar="CODE", help="set region non-interactively")
    p.add_argument("--domestic", metavar="COUNTRY", help="set domestic scene")
    p.add_argument("--cricketdata-api-key", metavar="KEY",
                   help="set the cricketdata.org fallback key")
    return p


def _run_config(args: argparse.Namespace) -> int:
    from stumps import config as cfg

    console = Console()
    existing = cfg.load_config_file()
    path = cfg.config_file_path()

    if args.show:
        if existing:
            console.print(f"[dim]{path}[/dim]\n")
            console.print(cfg.dump_toml(existing).rstrip())
        else:
            console.print(f"[dim]No config yet at {path}[/dim]")
        return 0

    data = dict(existing)
    non_interactive = any([args.team, args.region, args.domestic,
                           args.cricketdata_api_key])

    if non_interactive:
        if args.team:
            data["team"] = args.team if len(args.team) > 1 else args.team[0]
        if args.region:
            data["region"] = args.region
        if args.domestic:
            data["domestic"] = cfg.resolve_domestic_key(args.domestic) or "none"
        if args.cricketdata_api_key:
            data["cricketdata_api_key"] = args.cricketdata_api_key
    else:
        data = _config_wizard(console, existing)

    saved = cfg.save_config_file(data)
    console.print(f"[green]✓[/green] saved [bold]{saved}[/bold]")
    return 0


def _config_wizard(console: Console, existing: dict) -> dict:
    from rich.prompt import Prompt

    from stumps.completion import REGIONS, domestic_keys
    from stumps.config import resolve_domestic_key

    console.print("[bold bright_cyan]🏏 stumps config[/bold bright_cyan]  "
                  "[dim](press Enter to keep the shown default)[/dim]\n")

    cur_team = existing.get("team")
    team_default = ", ".join(cur_team) if isinstance(cur_team, list) else (cur_team or "England")
    teams = [t.strip() for t in Prompt.ask(
        "Team(s) to follow [dim](comma-separated)[/dim]", default=team_default).split(",")
        if t.strip()]

    console.print(f"[dim]regions: {', '.join(REGIONS)}[/dim]")
    region = Prompt.ask("Region code", default=existing.get("region", "gb")).strip().lower()

    console.print(f"[dim]domestic: {', '.join(domestic_keys())}[/dim]")
    domestic_in = Prompt.ask(
        "Home domestic scene", default=str(existing.get("domestic", "england")))
    domestic = resolve_domestic_key(domestic_in) or "none"

    has_key = bool(existing.get("cricketdata_api_key"))
    key_prompt = "cricketdata.org API key " + (
        "[dim](Enter to keep existing)[/dim]" if has_key else "[dim](optional, Enter to skip)[/dim]")
    key_in = Prompt.ask(key_prompt, default="", show_default=False).strip()

    data = dict(existing)
    data["team"] = teams if len(teams) > 1 else (teams[0] if teams else "England")
    data["region"] = region
    data["domestic"] = domestic
    if key_in:
        data["cricketdata_api_key"] = key_in
    return data


def _run_train(args: argparse.Namespace) -> int:
    from pathlib import Path

    from stumps.config import load_settings as _ls

    settings = _ls()
    console = Console()
    multiday = getattr(args, "multiday", False)
    formats = args.formats or (["test"] if multiday else ["odi", "t20i"])
    sample_every = args.sample_every if args.sample_every is not None else (
        12 if multiday else 6)

    kind = "multi-day Test/first-class" if multiday else "limited-overs chase"
    console.print(f"[bold]Training {kind} win-probability model[/bold] from Cricsheet…")
    console.print("[dim]downloading bundles (cached after first run) and parsing[/dim]")

    if args.force_download:
        from stumps.winprob.cricsheet import BUNDLES, MD_BUNDLES
        bundles = MD_BUNDLES if multiday else BUNDLES
        for fmt in formats:
            if fmt in bundles:
                (settings.cache_dir / Path(bundles[fmt]).name).unlink(missing_ok=True)

    try:
        from stumps.winprob.train import train, train_multiday
    except ImportError:
        console.print(
            "[red]Training needs the 'winprob' extra:[/red] "
            "pip install 'stumps[winprob]'  (numpy, scikit-learn)"
        )
        return 1

    def progress(matches: int, rows: int) -> None:
        console.print(f"  [dim]…{matches:,} matches, {rows:,} rows[/dim]")

    train_fn = train_multiday if multiday else train
    try:
        report = train_fn(
            settings,
            formats=formats,
            max_matches=args.max_matches,
            sample_every=sample_every,
            progress=progress,
        )
    except ImportError:
        console.print(
            "[red]scikit-learn / numpy not available.[/red] "
            "Install with: pip install 'stumps[winprob]'"
        )
        return 1
    except (RuntimeError, OSError) as exc:
        console.print(f"[red]Training failed:[/red] {exc}")
        return 1

    console.print("[green]✓[/green] " + report.summary())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "train":
        return _run_train(_train_parser().parse_args(argv[1:]))

    if argv and argv[0] == "config":
        return _run_config(_config_parser().parse_args(argv[1:]))

    parser = _show_parser()
    completion.autocomplete(parser)  # tab-completion hook (no-op without argcomplete)
    args = parser.parse_args(argv)
    try:
        return _run_show(args)
    except SourceError as exc:
        Console().print(
            f"[red]Could not fetch cricket data:[/red] {exc}\n"
            "[dim]Try 'stumps --demo' for sample data.[/dim]"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

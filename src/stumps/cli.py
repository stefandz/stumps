"""Command-line entry point for ``stumps``.

  stumps                      # prioritised cricket for your team(s)
  stumps --team india         # follow India instead of the England default
  stumps --region in --domestic india
  stumps --live-only --format t20    # filter what's shown
  stumps --compact            # one line per match
  stumps --json               # machine-readable output
  stumps --refresh 30         # live-refresh every 30 seconds
  stumps train                # train the win-probability model from Cricsheet

Defaults (team, region, domestic) can be set in ~/.config/stumps/config.toml.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

from rich.console import Console

from stumps.config import load_config_file, load_settings
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
    )

    a = p.add_argument_group("who/what to follow")
    a.add_argument("--team", "--follow", action="append", metavar="NAME",
                   help="team to put first (repeatable). Default: England")
    a.add_argument("--no-team", action="store_true",
                   help="don't prioritise any team (rank by tournament/domestic only)")
    a.add_argument("--region", metavar="CODE",
                   help="ESPN scoreboard region: gb, in, au, … (default gb)")
    a.add_argument("--domestic", metavar="COUNTRY",
                   help="home domestic scene: england, india, australia, or none")

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

    c = p.add_argument_group("display")
    c.add_argument("--compact", action="store_true", help="one line per match")
    c.add_argument("--no-figures", action="store_true", help="hide batting/bowling")
    c.add_argument("--no-winprob", action="store_true", help="hide win probability")
    c.add_argument("--no-dls", action="store_true", help="hide DLS par")
    c.add_argument("--no-commentary", action="store_true", help="hide recent balls")
    c.add_argument("--balls", type=int, default=None, metavar="N",
                   help="how many recent balls to show (default 6)")
    c.add_argument("--plain", action="store_true", help="disable colour")

    o = p.add_argument_group("output / data")
    o.add_argument("--json", action="store_true", help="machine-readable JSON output")
    o.add_argument("--demo", action="store_true",
                   help="use built-in sample data (offline)")
    o.add_argument("--no-enrich", action="store_true",
                   help="skip fetching detailed batting/bowling figures")
    o.add_argument("--refresh", type=int, metavar="SECONDS", default=None,
                   help="redraw every SECONDS until interrupted")
    o.add_argument("--width", type=int, default=None, help="force console width")
    return p


def _train_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stumps train",
        description="Train the limited-overs win-probability model from Cricsheet.",
    )
    p.add_argument("--formats", nargs="+", default=["odi", "t20i"],
                   choices=["odi", "t20i"],
                   help="Cricsheet bundles to train on (default: odi t20i)")
    p.add_argument("--max-matches", type=int, default=None,
                   help="limit matches parsed (for a quick run)")
    p.add_argument("--sample-every", type=int, default=6,
                   help="keep one training row per N balls (default 6 = per over)")
    p.add_argument("--force-download", action="store_true",
                   help="re-download Cricsheet bundles even if cached")
    return p


def _run_show(args: argparse.Namespace) -> int:
    prefs = Preferences.resolve(args, load_config_file())
    settings = load_settings()
    settings.region = prefs.region
    console = Console(width=args.width, no_color=args.plain)
    agg = Aggregator(settings, demo_only=args.demo)

    def run_once() -> None:
        result = agg.fetch()
        ranked = prioritise(result.matches, prefs)
        if not args.no_enrich:
            # Only fetch detailed scorecards for matches we'll show figures for
            # (live / break / stumps). Finished and upcoming games don't need
            # them, which also conserves the cricketdata.org daily quota.
            agg.enrich(result, [m for m, _ in ranked if m.phase.is_active_today])
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


def _run_train(args: argparse.Namespace) -> int:
    from stumps.config import load_settings as _ls

    settings = _ls()
    console = Console()
    console.print("[bold]Training win-probability model[/bold] from Cricsheet…")
    console.print("[dim]downloading bundles (cached after first run) and parsing[/dim]")

    if args.force_download:
        for fmt in args.formats:
            from stumps.winprob.cricsheet import BUNDLES
            from pathlib import Path

            name = Path(BUNDLES[fmt]).name
            (settings.cache_dir / name).unlink(missing_ok=True)

    try:
        from stumps.winprob.train import train
    except ImportError:
        console.print(
            "[red]Training needs the 'winprob' extra:[/red] "
            "pip install 'stumps[winprob]'  (numpy, scikit-learn)"
        )
        return 1

    def progress(matches: int, rows: int) -> None:
        console.print(f"  [dim]…{matches:,} matches, {rows:,} rows[/dim]")

    try:
        report = train(
            settings,
            formats=args.formats,
            max_matches=args.max_matches,
            sample_every=args.sample_every,
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

    args = _show_parser().parse_args(argv)
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

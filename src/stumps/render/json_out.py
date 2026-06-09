"""Machine-readable JSON output (for scripts, status bars, widgets)."""

from __future__ import annotations

import json

from stumps import dls
from stumps.config import Settings
from stumps.models import Innings, Match
from stumps.options import Preferences
from stumps.prioritise import Classification
from stumps.render.console import _g50_for, _headline
from stumps.sources.aggregator import FetchResult
from stumps.winprob import estimate, extract_chase_state


def _innings_dict(inns: Innings) -> dict:
    return {
        "team": inns.batting_team,
        "runs": inns.runs,
        "wickets": inns.wickets,
        "overs": inns.overs,
        "target": inns.target,
        "declared": inns.declared,
        "all_out": inns.all_out,
        "batters": [
            {
                "name": b.name, "runs": b.runs, "balls": b.balls,
                "fours": b.fours, "sixes": b.sixes, "strike_rate": round(b.strike_rate, 1),
                "not_out": b.not_out, "on_strike": b.on_strike, "dismissal": b.dismissal,
            }
            for b in inns.batters
        ],
        "bowlers": [
            {
                "name": b.name, "overs": b.overs, "maidens": b.maidens,
                "runs": b.runs, "wickets": b.wickets, "economy": round(b.economy, 2),
                "bowling_now": b.bowling_now,
            }
            for b in inns.bowlers
        ],
    }


def _dls_dict(match: Match) -> dict | None:
    state = extract_chase_state(match)
    overs = match.format.overs_per_innings
    if state is None or not match.first_innings or not overs:
        return None
    r = dls.par_score(
        first_innings_runs=match.first_innings.runs,
        overs_per_innings=overs,
        team2_overs_used=state.balls_bowled / 6.0,
        team2_wickets_lost=state.wickets_lost,
        team2_score=state.runs,
        g50=_g50_for(match),
    )
    return {"par": r.par_score, "target": r.target, "runs_ahead": r.runs_ahead,
            "edition": "standard"}


def _match_dict(match: Match, cls: Classification, settings: Settings,
                prefs: Preferences) -> dict:
    active = match.phase.is_active_today
    d = {
        "id": match.match_id,
        "title": match.title,
        "teams": match.team_names,
        "format": match.format.name,
        "phase": match.phase.value,
        "tier": cls.tier.name.lower(),
        "series": match.series_name,
        "venue": match.venue,
        "status": _headline(match),
        "result": match.result_text or None,
        "innings": [_innings_dict(i) for i in match.innings],
    }
    if active and prefs.show_dls:
        d["dls"] = _dls_dict(match)
    if active and prefs.show_winprob:
        est = estimate(match, settings)
        d["win_probability"] = (
            None if est is None
            else {"method": est.method, "probabilities": est.probabilities}
        )
    if prefs.show_commentary and match.recent_balls:
        d["recent_balls"] = [
            {"over": b.over, "description": b.description, "runs": b.runs,
             "is_wicket": b.is_wicket, "is_boundary": b.is_boundary}
            for b in match.recent_balls[: prefs.balls]
        ]
    return d


def render_json(
    result: FetchResult,
    ranked: list[tuple[Match, Classification]],
    settings: Settings,
    prefs: Preferences,
    *,
    when: str = "",
) -> str:
    payload = {
        "generated": when,
        "source": result.source.name,
        "fallback": result.used_fallback,
        "matches": [_match_dict(m, c, settings, prefs) for m, c in ranked],
    }
    return json.dumps(payload, indent=2)

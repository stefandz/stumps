"""Download and parse Cricsheet ball-by-ball data into chase training rows.

Cricsheet (https://cricsheet.org, CC BY-SA 4.0) publishes per-match JSON in
format-bundled zips. We turn the second innings of completed limited-overs
matches into (feature-vector, did-the-chasing-team-win) rows for the model.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from stumps.config import USER_AGENT
from stumps.winprob.multiday import (
    MultiDayState,
    feature_vector_md,
    overs_per_day,
)
from stumps.winprob.state import ChaseState, feature_vector
from stumps.models import Format

#: Cricsheet format bundles we use for the (limited-overs) chase model.
BUNDLES: dict[str, str] = {
    "odi": "https://cricsheet.org/downloads/odis_json.zip",
    "t20i": "https://cricsheet.org/downloads/t20s_json.zip",
}

#: Cricsheet bundles for the multi-day (Test / first-class) model.
MD_BUNDLES: dict[str, str] = {
    "test": "https://cricsheet.org/downloads/tests_json.zip",
}

#: match_type values we treat as limited-overs chases.
_LIMITED_OVERS_TYPES = {"ODI", "ODM", "T20", "IT20"}
_T20_TYPES = {"T20", "IT20"}

#: match_type values we treat as multi-day games. "MDM" = other multi-day matches.
_MULTIDAY_TYPES = {"Test", "MDM"}
#: Scheduled days by multi-day match_type, for the overs-remaining proxy.
_SCHEDULED_DAYS = {"Test": 5, "MDM": 4}


@dataclass
class TrainingData:
    X: list[list[float]]
    y: list[int]
    n_matches: int
    n_rows: int
    sources: list[str]


def download_bundles(
    formats: list[str], cache_dir: Path, *, force: bool = False,
    bundles: dict[str, str] | None = None,
) -> list[Path]:
    """Download the requested Cricsheet zips into ``cache_dir`` (cached on disk)."""
    bundles = bundles or BUNDLES
    cache_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for fmt in formats:
        url = bundles.get(fmt)
        if not url:
            continue
        dest = cache_dir / Path(url).name
        if dest.exists() and not force and dest.stat().st_size > 0:
            paths.append(dest)
            continue
        with httpx.stream(
            "GET", url, headers={"User-Agent": USER_AGENT}, timeout=120.0,
            follow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
        paths.append(dest)
    return paths


def iter_matches(zip_path: Path):
    """Yield parsed match JSON objects from a Cricsheet bundle zip."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.endswith(".json") or name.lower().startswith("readme"):
                continue
            try:
                with zf.open(name) as fh:
                    yield json.loads(fh.read().decode("utf-8"))
            except (json.JSONDecodeError, KeyError):
                continue


def chase_rows_from_match(
    match: dict, *, sample_every: int = 6
) -> list[tuple[list[float], int]]:
    """Extract (feature_vector, label) rows from one match's second innings.

    ``sample_every`` keeps one row per N legal balls (default 6 = once an over)
    to keep the dataset manageable. Label = 1 if the chasing team won.
    """
    info = match.get("info", {})
    match_type = info.get("match_type", "")
    if match_type not in _LIMITED_OVERS_TYPES:
        return []

    outcome = info.get("outcome", {})
    winner = outcome.get("winner")
    if not winner:  # tie / no result / abandoned -> no clean label
        return []

    innings = match.get("innings", [])
    if len(innings) < 2:
        return []
    second = innings[1]
    chasing_team = second.get("team", "")
    target = (second.get("target") or {}).get("runs")
    overs_total = info.get("overs") or (second.get("target") or {}).get("overs")
    if not target or not overs_total:
        return []

    is_t20 = match_type in _T20_TYPES
    balls_total = int(overs_total) * 6
    label = 1 if winner == chasing_team else 0

    rows: list[tuple[list[float], int]] = []
    runs = 0
    wickets = 0
    legal_balls = 0
    for over in second.get("overs", []):
        for delivery in over.get("deliveries", []):
            runs += (delivery.get("runs", {}) or {}).get("total", 0)
            wickets += len(delivery.get("wickets", []) or [])
            extras = delivery.get("extras", {}) or {}
            is_legal = not ("wides" in extras or "noballs" in extras)
            if is_legal:
                legal_balls += 1

            if legal_balls == 0 or legal_balls % sample_every != 0:
                continue
            state = ChaseState(
                is_t20=is_t20,
                target=int(target),
                runs=runs,
                wickets_lost=min(wickets, 10),
                balls_bowled=legal_balls,
                balls_total=balls_total,
            )
            # Only keep genuinely in-play states.
            if (
                state.runs_needed > 0
                and state.balls_remaining > 0
                and state.wickets_in_hand > 0
            ):
                rows.append((feature_vector(state), label))
    return rows


def build_training_data(
    zip_paths: list[Path],
    *,
    sample_every: int = 6,
    max_matches: int | None = None,
    progress=None,
) -> TrainingData:
    """Parse all bundles into a single training set."""
    return _build(zip_paths, chase_rows_from_match, sample_every=sample_every,
                  max_matches=max_matches, progress=progress)


def multiday_rows_from_match(
    match: dict, *, sample_every: int = 12
) -> list[tuple[list[float], int]]:
    """Extract (feature_vector, label) rows from one multi-day match.

    Label is framed from the side batting at that state — 1 = that side wins the
    match, 0 = the opponent wins, 2 = the match is drawn — so the features stay
    symmetric. Time left is unknowable historically, so we proxy overs-remaining
    as (scheduled overs − overs bowled so far). Ties / no-results are skipped.
    """
    info = match.get("info", {})
    if info.get("match_type") not in _MULTIDAY_TYPES:
        return []
    teams = info.get("teams") or []
    if len(teams) != 2:
        return []

    outcome = info.get("outcome", {}) or {}
    winner = outcome.get("winner")
    result = (outcome.get("result") or "").lower()
    if not winner and result != "draw":
        return []  # tie / no result / abandoned -> ambiguous

    sched_overs = _SCHEDULED_DAYS.get(info.get("match_type"), 5) * \
        overs_per_day(Format.TEST)

    innings_list = match.get("innings", [])
    agg: dict[str, int] = {t: 0 for t in teams}
    overs_bowled_total = 0.0
    rows: list[tuple[list[float], int]] = []

    for idx, inns in enumerate(innings_list[:4]):
        batting = inns.get("team", "")
        bowling = next((t for t in teams if t != batting), "")
        if not batting or not bowling:
            continue
        is_fourth = idx == 3
        opp_total = agg.get(bowling, 0)        # fixed during this innings
        prior_bat = agg.get(batting, 0)
        runs = 0
        wickets = 0
        legal_balls = 0
        for over in inns.get("overs", []):
            for delivery in over.get("deliveries", []):
                runs += (delivery.get("runs", {}) or {}).get("total", 0)
                wickets += len(delivery.get("wickets", []) or [])
                extras = delivery.get("extras", {}) or {}
                if not ("wides" in extras or "noballs" in extras):
                    legal_balls += 1
                if legal_balls == 0 or legal_balls % sample_every != 0:
                    continue
                lead = (prior_bat + runs) - opp_total
                overs_remaining = max(
                    0.0, sched_overs - overs_bowled_total - legal_balls / 6.0)
                state = MultiDayState(
                    innings_number=idx + 1,
                    batting_team=batting,
                    bowling_team=bowling,
                    lead=lead,
                    wickets_in_hand=max(0, 10 - min(wickets, 10)),
                    overs_remaining=overs_remaining,
                    is_fourth_innings=is_fourth,
                    runs_to_win=max(0, -lead + 1) if is_fourth else 0,
                    day_number=0,
                    total_days=0,
                )
                if not result and winner:
                    label = 1 if winner == batting else 0
                else:
                    label = 2  # draw
                rows.append((feature_vector_md(state), label))
        # close the innings: bank runs and overs bowled
        agg[batting] = prior_bat + runs
        overs_bowled_total += legal_balls / 6.0
    return rows


def build_multiday_training_data(
    zip_paths: list[Path],
    *,
    sample_every: int = 12,
    max_matches: int | None = None,
    progress=None,
) -> TrainingData:
    """Parse multi-day bundles into a single (3-class) training set."""
    return _build(zip_paths, multiday_rows_from_match, sample_every=sample_every,
                  max_matches=max_matches, progress=progress)


def _build(zip_paths, row_fn, *, sample_every, max_matches, progress) -> TrainingData:
    X: list[list[float]] = []
    y: list[int] = []
    n_matches = 0
    for zip_path in zip_paths:
        for match in iter_matches(zip_path):
            if max_matches is not None and n_matches >= max_matches:
                break
            rows = row_fn(match, sample_every=sample_every)
            if rows:
                n_matches += 1
                for vec, label in rows:
                    X.append(vec)
                    y.append(label)
                if progress is not None and n_matches % 200 == 0:
                    progress(n_matches, len(X))
    return TrainingData(
        X=X, y=y, n_matches=n_matches, n_rows=len(X),
        sources=[p.name for p in zip_paths],
    )

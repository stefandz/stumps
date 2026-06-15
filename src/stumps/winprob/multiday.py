"""Multi-day (Test / first-class) match-state extraction and features.

Unlike a limited-overs chase, a multi-day game has three outcomes — either side
can win, or it can be drawn — and the dominant driver of a draw is **how much
time is left**, which no feed exposes directly. We reconstruct an estimate of
*overs remaining in the match* from the scheduled close of play and the current
local time (parsed from the feed in ``sources/espn.py``), falling back to a
day-fraction prior when those aren't known.

This module is the single source of truth for the multi-day feature vector —
shared by the heuristic (``estimator._test_estimate``) and, when trained, the
multi-day model (``train.train_multiday`` / ``estimator._multiday_model_*``), so
training and inference can't drift apart (mirrors ``state.FEATURE_ORDER``).
"""

from __future__ import annotations

from dataclasses import dataclass

from stumps.models import Format, Match, Phase

#: Scheduled overs per full day's play, by multi-day format. County
#: championship mandates 96; Tests target 90.
OVERS_PER_DAY: dict[Format, int] = {
    Format.TEST: 90,
    Format.WTEST: 90,
    Format.FIRST_CLASS: 96,
}

#: A standard day is ~6 hours of play (e.g. 11:00–18:00 less an hour of
#: intervals = 360 min), so minutes-per-over ≈ 360 / overs_per_day.
_PLAYING_MINUTES_PER_DAY = 360.0


def overs_per_day(fmt: Format) -> int:
    return OVERS_PER_DAY.get(fmt, 90)


def _minutes_per_over(fmt: Format) -> float:
    return _PLAYING_MINUTES_PER_DAY / overs_per_day(fmt)


def _parse_hhmm(text: str) -> int | None:
    """'17:30' or '17.30' -> minutes since midnight; None if unparseable."""
    if not text:
        return None
    cleaned = text.strip().replace(".", ":")
    parts = cleaned.split(":")
    if len(parts) < 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return hours * 60 + minutes


def overs_remaining_estimate(match: Match) -> float:
    """Best estimate of overs left in the whole match.

    Uses the scheduled close and current local time when present (the precise
    path, only meaningful while play is live); otherwise a day-fraction prior
    (assume mid-day on the current day). Always returns a non-negative number so
    callers have something to work with — accuracy is reflected in labelling,
    not by returning None.
    """
    opd = overs_per_day(match.format)
    day = match.day_number
    total = match.total_days
    full_days_left = 0
    if day and total and total >= day:
        full_days_left = max(0, total - day)

    now = _parse_hhmm(match.local_time)
    start = _parse_hhmm(match.start_time)
    close = _parse_hhmm(match.close_time)
    if match.phase is Phase.STUMPS:
        # "Stumps" labels both genuine end-of-day *and* the morning before a new
        # day's play begins (the feed carries the previous evening's label until
        # the first ball). Distinguish by the clock: at or before the scheduled
        # start, today's full allocation is still to come; otherwise the day's
        # play really is done.
        #
        # When the feed didn't give a start time, fall back to a prior: a day's
        # play spans ~7 wall-clock hours, so start ≈ close − 7h (which adapts to
        # day/night matches), else a plain 11:00. This keeps the morning case
        # right without a parsed start, and a genuine evening/early stumps still
        # reads as done (now is past the prior). We only make this call with a
        # known current time; without one we can't tell morning from evening.
        if start is None:
            start = (close - 7 * 60) if close is not None else 11 * 60
        if now is not None and now <= start:
            overs_today = float(opd)
        else:
            overs_today = 0.0
    elif now is not None and close is not None:
        overs_today = (max(0, close - now)) / _minutes_per_over(match.format)
        overs_today = min(overs_today, float(opd))
    else:
        # No usable clock — assume we're mid-day on the current day.
        overs_today = opd * 0.5

    return max(0.0, overs_today + full_days_left * opd)


@dataclass
class MultiDayState:
    """A multi-day position, framed from the *currently batting* side so the
    feature vector is symmetric (no dependence on team listing order)."""

    innings_number: int          # 1..4
    batting_team: str
    bowling_team: str
    lead: int                    # batting side aggregate − opponent aggregate
    wickets_in_hand: int         # in the current innings
    overs_remaining: float       # estimated, in the whole match
    is_fourth_innings: bool
    runs_to_win: int             # 4th innings: runs the batting side still needs (else 0)
    day_number: int
    total_days: int

    @property
    def balls_remaining(self) -> float:
        return self.overs_remaining * 6.0

    @property
    def required_run_rate(self) -> float:
        """4th-innings required rate (per over); 0 when not a fourth-innings chase."""
        if not self.is_fourth_innings or self.runs_to_win <= 0:
            return 0.0
        overs = self.overs_remaining
        return self.runs_to_win / overs if overs > 0 else 99.0


#: Multi-day feature names, fixed order — shared by training and inference.
FEATURE_ORDER_MD: tuple[str, ...] = (
    "innings_number",
    "lead",
    "wickets_in_hand",
    "overs_remaining",
    "is_fourth_innings",
    "runs_to_win",
    "required_run_rate",
)


def multiday_features(state: MultiDayState) -> dict[str, float]:
    return {
        "innings_number": float(state.innings_number),
        "lead": float(state.lead),
        "wickets_in_hand": float(state.wickets_in_hand),
        "overs_remaining": float(min(state.overs_remaining, 450.0)),
        "is_fourth_innings": 1.0 if state.is_fourth_innings else 0.0,
        "runs_to_win": float(max(0, state.runs_to_win)),
        "required_run_rate": float(min(state.required_run_rate, 36.0)),
    }


def feature_vector_md(state: MultiDayState) -> list[float]:
    feats = multiday_features(state)
    return [feats[name] for name in FEATURE_ORDER_MD]


def extract_multiday_state(match: Match) -> MultiDayState | None:
    """Pull a :class:`MultiDayState` from a multi-day match in progress."""
    if not match.format.is_multi_day or not match.innings:
        return None
    current = match.current_innings
    if current is None:
        return None
    batting = current.batting_team

    def total(team: str) -> int:
        return sum(i.runs for i in match.innings
                   if team.lower() in i.batting_team.lower())

    others = [n for n in match.team_names if n.lower() not in batting.lower()]
    if not others:
        return None
    bowling = others[0]

    lead = total(batting) - total(bowling)
    innings_number = current.number or len(match.innings)
    is_fourth = len(match.innings) >= 4
    # 4th-innings target = opponent aggregate + 1; what's still needed = −lead + 1.
    runs_to_win = max(0, -lead + 1) if is_fourth else 0

    return MultiDayState(
        innings_number=innings_number,
        batting_team=batting,
        bowling_team=bowling,
        lead=lead,
        wickets_in_hand=max(0, 10 - current.wickets),
        overs_remaining=overs_remaining_estimate(match),
        is_fourth_innings=is_fourth,
        runs_to_win=runs_to_win,
        day_number=match.day_number or 0,
        total_days=match.total_days or 0,
    )

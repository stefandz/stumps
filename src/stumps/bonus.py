"""First-class batting/bowling bonus points earned *so far* in a match.

No feed exposes these live — ESPN only emits a `points` note once a game is
finished, and the standings are season totals — so we compute them from each
competition's published bonus-point rules. Those rules are competition-specific
(England, Australia and New Zealand each differ), so a match only shows bonus
points when its series name matches a rule below; everything else returns None
and renders nothing. Like the DLS par score and the win estimate, this is a
computed approximation, clearly labelled as such.

Two structural caveats, both honestly surfaced rather than hidden:

* Bonus points accrue only in the **first innings of each side**, within a fixed
  over window (110 for England/NZ, 100 for Australia). While an innings is still
  inside that window the current score *is* the window figure; once it passes the
  window the locked value is the score *at* the cap, which the per-event summary
  doesn't carry unless the over-by-over block is populated. When it is, we sum to
  the cap (exact at any stage); when it isn't and the innings has gone long, we
  fall back to the current score and flag the figure approximate (`approx`) — it
  can only overstate, so it reads as an upper bound.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from stumps.models import Innings, Match


@dataclass(frozen=True)
class BonusRule:
    """One competition's first-innings bonus-point scheme.

    Batting is either a cumulative threshold table (England/NZ: reaching 250
    banks 1, 300 banks 2, …) or a linear rate (Australia: 0.01 per run above
    200). Bowling is always a cumulative threshold table on wickets taken."""

    window: str  # human label for the over window, e.g. "first 110 overs"
    over_cap: float
    batting_table: tuple[tuple[int, float], ...] = ()
    batting_per_run_above: tuple[int, float] | None = None  # (runs, points/run)
    batting_max: float | None = None  # cap for the linear scheme, if any
    bowling_table: tuple[tuple[int, float], ...] = ()

    def batting_points(self, runs: int) -> float:
        if self.batting_per_run_above is not None:
            base, rate = self.batting_per_run_above
            pts = max(0, runs - base) * rate
            return min(pts, self.batting_max) if self.batting_max is not None else pts
        return _table_points(self.batting_table, runs)

    def bowling_points(self, wickets: int) -> float:
        return _table_points(self.bowling_table, wickets)


def _table_points(table: tuple[tuple[int, float], ...], value: int) -> float:
    """Highest cumulative points whose threshold `value` has reached (the tables
    are monotonic, so the last threshold cleared is the running total)."""
    out = 0.0
    for threshold, points in table:
        if value >= threshold:
            out = points
    return out


# England — Rothesay County Championship: batting 250/300/350/400/450 → 1..5,
# bowling 3/6/9 wkts → 1..3, all within the first 110 overs of each first innings.
_COUNTY = BonusRule(
    "first 110 overs", 110.0,
    batting_table=((250, 1), (300, 2), (350, 3), (400, 4), (450, 5)),
    bowling_table=((3, 1), (6, 2), (9, 3)),
)

# Australia — Sheffield Shield: batting 0.01/run above 200 (uncapped in practice),
# bowling 5/7/9 wkts → 0.5/1.0/1.5, within the first 100 overs.
_SHIELD = BonusRule(
    "first 100 overs", 100.0,
    batting_per_run_above=(200, 0.01),
    bowling_table=((5, 0.5), (7, 1.0), (9, 1.5)),
)

# New Zealand — Plunket Shield: batting 200/250/300/350 → 1..4, bowling
# 3/5/7/9 wkts → 1..4, within the first 110 overs.
_PLUNKET = BonusRule(
    "first 110 overs", 110.0,
    batting_table=((200, 1), (250, 2), (300, 3), (350, 4)),
    bowling_table=((3, 1), (5, 2), (7, 3), (9, 4)),
)

#: series-name substrings (lowercased) → rule. Order doesn't matter; first hit wins.
_RULES: tuple[tuple[str, BonusRule], ...] = (
    ("county championship", _COUNTY),
    ("sheffield shield", _SHIELD),
    ("plunket shield", _PLUNKET),
)


def rule_for(series_name: str) -> BonusRule | None:
    s = (series_name or "").lower()
    for marker, rule in _RULES:
        if marker in s:
            return rule
    return None


@dataclass
class TeamBonus:
    """A team's bonus points so far. `batting_seen`/`bowling_seen` distinguish
    "0 points" from "that innings hasn't happened yet" (renders as a dash)."""

    team: str
    batting: float = 0.0
    bowling: float = 0.0
    batting_seen: bool = False
    bowling_seen: bool = False
    approx: bool = False  # any component taken past the over window without over data

    @property
    def total(self) -> float:
        return self.batting + self.bowling


def _window_figures(inns: Innings, over_cap: float) -> tuple[int, int, bool]:
    """Runs and wickets that count toward bonus points for one innings, plus an
    `approx` flag. Uses the over-by-over block to sum exactly to the cap when it's
    present; otherwise the live score (exact while inside the window, an
    over-stating upper bound once past it)."""
    overs = inns.over_scores
    if overs:
        window = overs[: int(over_cap)]
        return (sum(o.runs for o in window),
                sum(o.wickets for o in window), False)
    return inns.runs, inns.wickets, inns.overs > over_cap


def match_bonus(match: Match) -> list[TeamBonus] | None:
    """Per-team batting/bowling bonus points earned so far, or None when the
    competition has no bonus scheme we know (or it isn't a multi-day game).

    Only the first innings of each side counts; innings 3/4 never do. A team
    earns batting bonus from its own first innings and bowling bonus from the
    opponent's."""
    rule = rule_for(match.series_name)
    if rule is None or not match.format.is_multi_day:
        return None

    names = match.team_names
    if len(names) != 2:
        return None
    table = {n: TeamBonus(n) for n in names}

    for inns in match.innings:
        if inns.number not in (1, 2):  # second innings earn nothing
            continue
        bat = table.get(inns.batting_team)
        bowl_name = next((n for n in names if n != inns.batting_team), "")
        bowl = table.get(bowl_name)
        if bat is None or bowl is None:
            continue  # innings team didn't match either side's name — skip safely
        runs, wkts, approx = _window_figures(inns, rule.over_cap)
        bat.batting = rule.batting_points(runs)
        bat.batting_seen = True
        bat.approx |= approx
        bowl.bowling = rule.bowling_points(wkts)
        bowl.bowling_seen = True
        bowl.approx |= approx

    if not any(b.batting_seen or b.bowling_seen for b in table.values()):
        return None  # match hasn't started — nothing to show
    return [table[n] for n in names]

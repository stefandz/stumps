"""The DLS *Standard Edition* resource-percentage table and lookups.

Only the Standard Edition is published openly (the Professional Edition, which
broadcasters use, is proprietary). Values are transcribed from the ECB's
"Duckworth/Lewis/Stern Methodology" regulations (over-by-over table, page 15)
and cross-checked against the Wikipedia excerpt. They are therefore *indicative*
and will typically land within ~1-2 runs of the official Professional figure for
normal totals, diverging more for very high first-innings scores (300+).

Table shape: rows = whole overs remaining (50 down to 0); columns = wickets
lost (0..9). A cell is the percentage of a full 50-over innings' run-scoring
resources still available. We linearly interpolate between whole overs to
support part-over (ball-by-ball) positions, which is a close approximation of
the official ball-by-ball table.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from importlib import resources

# Source of the verified Standard Edition values shipped with the package.
_CSV_PACKAGE = "stumps.data"
_CSV_NAME = "dls_standard_resources.csv"


@lru_cache(maxsize=1)
def _load_table() -> dict[int, list[float]]:
    """Return {overs_remaining: [pct for wickets_lost 0..9]} from the CSV."""
    table: dict[int, list[float]] = {}
    with resources.files(_CSV_PACKAGE).joinpath(_CSV_NAME).open(
        "r", encoding="utf-8"
    ) as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            overs = int(row["overs_remaining"])
            table[overs] = [float(row[f"w{w}"]) for w in range(10)]
    if 50 not in table or 0 not in table:
        raise ValueError("DLS resource table is missing anchor rows (0 and 50).")
    return table


def _balls_to_overs(balls: int) -> float:
    """Convert a count of balls into cricket over notation (6 balls = 1 over)."""
    return balls // 6 + (balls % 6) / 10.0


def resource_pct(overs_remaining: float, wickets_lost: int) -> float:
    """Resource percentage remaining for *overs_remaining* overs and
    *wickets_lost* wickets down.

    ``overs_remaining`` may be fractional in *decimal* terms (e.g. 30.5 means
    30 overs and 3 balls is **not** how cricket notation works — pass decimal
    overs here, i.e. 30.5 = thirty-and-a-half overs). Use
    :func:`resource_pct_from_balls` if you have a ball count in cricket terms.

    Values are linearly interpolated between the whole-over rows of the table.
    """
    if wickets_lost >= 10:
        return 0.0
    if wickets_lost < 0:
        raise ValueError("wickets_lost must be >= 0")
    if overs_remaining <= 0:
        return 0.0

    table = _load_table()
    max_overs = max(table)
    if overs_remaining >= max_overs:
        return table[max_overs][wickets_lost]

    lower = int(overs_remaining)  # floor
    upper = lower + 1
    low_val = table[lower][wickets_lost]
    high_val = table[min(upper, max_overs)][wickets_lost]
    frac = overs_remaining - lower
    return low_val + (high_val - low_val) * frac


def resource_pct_from_balls(balls_remaining: int, wickets_lost: int) -> float:
    """Resource percentage given a *ball* count remaining (6 balls per over)."""
    return resource_pct(balls_remaining / 6.0, wickets_lost)

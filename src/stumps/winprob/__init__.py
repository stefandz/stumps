"""Home-grown win-probability estimation.

Important: this is an **estimate**, not CricViz's WinViz (which is proprietary
and not publicly available). The limited-overs chase estimate can use a model
trained on Cricsheet ball-by-ball data (run ``stumps train``); without a trained
model — or without scikit-learn installed — it falls back to a transparent
heuristic. Test-match numbers are always a rough heuristic lean.
"""

from stumps.winprob.estimator import WinEstimate, estimate
from stumps.winprob.state import ChaseState, extract_chase_state

__all__ = ["WinEstimate", "estimate", "ChaseState", "extract_chase_state"]

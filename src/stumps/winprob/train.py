"""Train the limited-overs chase win-probability model from Cricsheet data.

Run via ``stumps train``. Downloads the Cricsheet ODI + T20 bundles (cached),
turns second innings into (state -> won?) rows, fits a gradient-boosted
classifier, reports calibration/accuracy on a held-out split, and pickles the
artifact to the configured model path. Requires the ``winprob`` extra
(``numpy``, ``scikit-learn``).
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass

from stumps.config import Settings
from stumps.winprob.cricsheet import (
    MD_BUNDLES,
    build_multiday_training_data,
    build_training_data,
    download_bundles,
)
from stumps.winprob.multiday import FEATURE_ORDER_MD
from stumps.winprob.state import FEATURE_ORDER


@dataclass
class TrainReport:
    n_matches: int
    n_rows: int
    accuracy: float
    log_loss: float
    model_path: str
    sources: list[str]
    brier: float | None = None  # binary chase model only

    def summary(self) -> str:
        metrics = f"accuracy={self.accuracy:.3f}  log_loss={self.log_loss:.3f}"
        if self.brier is not None:
            metrics += f"  brier={self.brier:.3f}"
        return (
            f"Trained on {self.n_matches:,} matches / {self.n_rows:,} ball-states "
            f"from {', '.join(self.sources)}\n"
            f"  {metrics}\n"
            f"  saved -> {self.model_path}"
        )


def train(
    settings: Settings,
    *,
    formats: list[str] | None = None,
    max_matches: int | None = None,
    sample_every: int = 6,
    progress=None,
) -> TrainReport:
    # Imported lazily so the rest of the app doesn't need sklearn/numpy.
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
    from sklearn.model_selection import train_test_split

    formats = formats or ["odi", "t20i"]
    zip_paths = download_bundles(formats, settings.cache_dir)
    data = build_training_data(
        zip_paths, sample_every=sample_every, max_matches=max_matches,
        progress=progress,
    )
    if data.n_rows < 100:
        raise RuntimeError(
            f"Only {data.n_rows} training rows — not enough to train. "
            "Check the Cricsheet download succeeded."
        )

    X = np.asarray(data.X, dtype=float)
    y = np.asarray(data.y, dtype=int)
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.08, max_depth=4, l2_regularization=1.0,
        random_state=42,
    )
    model.fit(X_tr, y_tr)

    proba = model.predict_proba(X_te)
    pos_idx = list(model.classes_).index(1)
    p_pos = proba[:, pos_idx]
    report = TrainReport(
        n_matches=data.n_matches,
        n_rows=data.n_rows,
        accuracy=float(accuracy_score(y_te, model.predict(X_te))),
        log_loss=float(log_loss(y_te, proba, labels=list(model.classes_))),
        brier=float(brier_score_loss(y_te, p_pos)),
        model_path=str(settings.winprob_model_path),
        sources=data.sources,
    )

    artifact = {
        "model": model,
        "order": list(FEATURE_ORDER),
        "trained_on": data.sources,
        "n_matches": data.n_matches,
        "n_rows": data.n_rows,
        "metrics": {
            "accuracy": report.accuracy,
            "log_loss": report.log_loss,
            "brier": report.brier,
        },
    }
    settings.winprob_model_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.winprob_model_path.open("wb") as fh:
        pickle.dump(artifact, fh)

    return report


def train_multiday(
    settings: Settings,
    *,
    formats: list[str] | None = None,
    max_matches: int | None = None,
    sample_every: int = 12,
    progress=None,
) -> TrainReport:
    """Train the multi-day (Test/first-class) 3-class win/lose/draw model.

    Opt-in: the result is used only when ``--test-model`` is passed. Probabilities
    are framed from the side batting at each state (class 1 = that side wins,
    0 = opponent wins, 2 = draw)."""
    import numpy as np
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import accuracy_score, log_loss
    from sklearn.model_selection import train_test_split

    formats = formats or ["test"]
    zip_paths = download_bundles(formats, settings.cache_dir, bundles=MD_BUNDLES)
    data = build_multiday_training_data(
        zip_paths, sample_every=sample_every, max_matches=max_matches,
        progress=progress,
    )
    if data.n_rows < 100:
        raise RuntimeError(
            f"Only {data.n_rows} training rows — not enough to train. "
            "Check the Cricsheet download succeeded."
        )

    X = np.asarray(data.X, dtype=float)
    y = np.asarray(data.y, dtype=int)
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = HistGradientBoostingClassifier(
        max_iter=300, learning_rate=0.08, max_depth=4, l2_regularization=1.0,
        random_state=42,
    )
    model.fit(X_tr, y_tr)

    proba = model.predict_proba(X_te)
    report = TrainReport(
        n_matches=data.n_matches,
        n_rows=data.n_rows,
        accuracy=float(accuracy_score(y_te, model.predict(X_te))),
        log_loss=float(log_loss(y_te, proba, labels=list(model.classes_))),
        model_path=str(settings.winprob_multiday_model_path),
        sources=data.sources,
    )

    artifact = {
        "model": model,
        "order": list(FEATURE_ORDER_MD),
        "classes": list(int(c) for c in model.classes_),
        "multiday": True,
        "trained_on": data.sources,
        "n_matches": data.n_matches,
        "n_rows": data.n_rows,
        "metrics": {"accuracy": report.accuracy, "log_loss": report.log_loss},
    }
    settings.winprob_multiday_model_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.winprob_multiday_model_path.open("wb") as fh:
        pickle.dump(artifact, fh)

    return report

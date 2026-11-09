"""Probability calibration for the depression track: AI PHQ-8 total -> P(ground-truth PHQ-8 >= 10).

Fitted only on LongCounsel-8 development outputs. Research use only (dataset terms): the clinical store applies an
active mapping to `lc8-demo-` patients only (plan Q3). Other conditions stay uncalibrated (probability null).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .longcounsel import ROOT, canonical

CAL_DIR = Path(os.getenv("PROGRESS_CALIBRATION_DIR", str(ROOT / "data/research/progress-calibration")))
ACTIVE = "active.json"


@dataclass
class Mapping:
    id: str
    method: str                      # isotonic | platt
    x: list[float] = field(default_factory=list)
    y: list[float] = field(default_factory=list)
    coef: float = 0.0
    intercept: float = 0.0
    fitted_on: dict = field(default_factory=dict)

    def predict(self, score: float) -> float:
        if self.method == "platt":
            return float(1 / (1 + math.exp(-(self.coef * score + self.intercept))))
        return float(np.clip(np.interp(score, self.x, self.y), 0.0, 1.0))


def fit(scores, labels, method: str = "isotonic", fitted_on: dict | None = None) -> Mapping:
    scores, labels = np.asarray(scores, float), np.asarray(labels, int)
    if len(scores) == 0:
        raise ValueError("No data to calibrate")
    fitted_on = fitted_on or {}
    if len(set(labels.tolist())) < 2:
        p = float(labels.mean())
        return Mapping(id=canonical(["constant", p, fitted_on])[:16], method="isotonic", x=[0.0, 24.0], y=[p, p], fitted_on={**fitted_on, "note": "single class"})
    if method == "platt":
        from sklearn.linear_model import LogisticRegression
        model = LogisticRegression().fit(scores.reshape(-1, 1), labels)
        coef, intercept = float(model.coef_[0][0]), float(model.intercept_[0])
        return Mapping(id=canonical(["platt", coef, intercept, fitted_on])[:16], method="platt", coef=coef, intercept=intercept, fitted_on=fitted_on)
    from sklearn.isotonic import IsotonicRegression
    model = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(scores, labels)
    x, y = [float(v) for v in model.X_thresholds_], [float(v) for v in model.y_thresholds_]
    return Mapping(id=canonical(["isotonic", x, y, fitted_on])[:16], method="isotonic", x=x, y=y, fitted_on=fitted_on)


def cross_fitted(scores, labels, groups, k: int = 5, method: str = "isotonic") -> list[float]:
    """Out-of-fold probabilities with folds over trajectories (groups) so no trajectory calibrates itself."""
    from sklearn.model_selection import GroupKFold
    scores, labels, groups = np.asarray(scores, float), np.asarray(labels, int), np.asarray(groups)
    n_groups = len(set(groups.tolist()))
    out = np.zeros(len(scores))
    if n_groups < 2:
        return [float(labels.mean())] * len(scores)
    for train, test in GroupKFold(n_splits=min(k, n_groups)).split(scores, labels, groups):
        mapping = fit(scores[train], labels[train], method)
        out[test] = [mapping.predict(s) for s in scores[test]]
    return out.tolist()


def brier(probs, labels) -> float:
    p, y = np.asarray(probs, float), np.asarray(labels, float)
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def reliability(probs, labels, bins: int = 10) -> list[dict]:
    p, y = np.asarray(probs, float), np.asarray(labels, float)
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        mask = (p >= lo) & ((p < hi) if b < bins - 1 else (p <= hi))
        if mask.any():
            rows.append({"bin": [lo, hi], "n": int(mask.sum()), "mean_predicted": float(p[mask].mean()), "observed_rate": float(y[mask].mean())})
    return rows


def save_active(mapping: Mapping, directory: Path | None = None) -> Path:
    directory = Path(directory or CAL_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(mapping), "saved_at": datetime.now(timezone.utc).isoformat(),
               "use": "research view and lc8-demo- patients only (LongCounsel-8 terms)"}
    (directory / f"{mapping.id}.json").write_text(json.dumps(payload, indent=2))
    (directory / ACTIVE).write_text(json.dumps(payload, indent=2))
    return directory / ACTIVE


def load_active_mapping(directory: Path | None = None) -> Mapping | None:
    path = Path(directory or CAL_DIR) / ACTIVE
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return Mapping(**{k: data[k] for k in ("id", "method", "x", "y", "coef", "intercept", "fitted_on")})

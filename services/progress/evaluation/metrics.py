"""Depression-track metrics with trajectory-level bootstrap intervals. Pure functions over plain records.

A record is one (trajectory, session): {run_id, session_index, ai_items{1..8: int|None}, gt_items{1..8: float},
gt_total, label (prediction label or None), trajectory_type}. AI totals use insufficient -> 0 (reported as such);
item metrics use rated items only.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import median

import numpy as np

from services.progress.trends import trajectory

LARGE_CHANGE = 5
CASE_THRESHOLD = 10
ITEMS = [str(i) for i in range(1, 9)]
LABEL_RANK = {"insufficient_evidence": 0, "unlikely": 0, "possible": 1, "likely": 2}


def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def ai_total(rec: dict) -> int:
    return sum(v for v in rec["ai_items"].values() if v is not None)


def by_trajectory(records: list[dict]) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for r in records:
        out[r["run_id"]].append(r)
    return {k: sorted(v, key=lambda r: r["session_index"]) for k, v in sorted(out.items())}


def mean(values) -> float:
    values = list(values)
    return float(sum(values) / len(values)) if values else float("nan")


def current_mae(records) -> float:
    return mean(abs(ai_total(r) - r["gt_total"]) for r in records)


def item_metrics(records) -> dict:
    errors, exact, rated, total = [], [], 0, 0
    for r in records:
        for i in ITEMS:
            total += 1
            v = r["ai_items"].get(i)
            if v is None:
                continue
            rated += 1
            errors.append(abs(v - r["gt_items"][i]))
            exact.append(v == round_half_up(r["gt_items"][i]))
    return {"item_mae_rated": mean(errors), "item_exact_match_rated": mean(exact), "coverage": rated / total if total else float("nan"),
            "insufficient_rate": 1 - rated / total if total else float("nan")}


def transitions(records) -> list[tuple[float, float]]:
    """(Δ AI total, Δ GT total) for adjacent completed sessions of the same trajectory."""
    out = []
    for rows in by_trajectory(records).values():
        for a, b in zip(rows, rows[1:]):
            if b["session_index"] == a["session_index"] + 1:
                out.append((ai_total(b) - ai_total(a), b["gt_total"] - a["gt_total"]))
    return out


def change_mae(records) -> float:
    return mean(abs(p - g) for p, g in transitions(records))


def large_change_direction(records) -> tuple[float, int]:
    """Direction accuracy where |ΔGT| >= 5; a predicted tie (Δ AI = 0) counts as wrong."""
    rows = [(p, g) for p, g in transitions(records) if abs(g) >= LARGE_CHANGE]
    return mean((p != 0 and (p > 0) == (g > 0)) for p, g in rows), len(rows)


def stratified_mae(records) -> dict[str, dict]:
    groups = defaultdict(list)
    for r in records:
        groups[r["trajectory_type"]].append(r)
    return {k: {"mae": current_mae(v), "n_sessions": len(v)} for k, v in sorted(groups.items())}


def auc_scores(records) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve
    y = [int(r["gt_total"] >= CASE_THRESHOLD) for r in records]
    s = [ai_total(r) for r in records]
    if len(set(y)) < 2:
        return {"auroc": float("nan"), "auprc": float("nan"), "roc": [], "positives": sum(y), "n": len(y)}
    fpr, tpr, thr = roc_curve(y, s)
    return {"auroc": float(roc_auc_score(y, s)), "auprc": float(average_precision_score(y, s)),
            "roc": [{"fpr": float(a), "tpr": float(b), "threshold": float(c) if math.isfinite(c) else None} for a, b, c in zip(fpr, tpr, thr)],
            "positives": sum(y), "n": len(y)}


def threshold_metrics(records, minimum: str) -> dict:
    """Binary classification at 'label >= minimum' (possible or likely) vs GT PHQ-8 >= 10."""
    tp = fp = tn = fn = 0
    for r in records:
        if r.get("label") is None:
            continue
        pred = LABEL_RANK.get(r["label"], 0) >= LABEL_RANK[minimum]
        truth = r["gt_total"] >= CASE_THRESHOLD
        tp += pred and truth
        fp += pred and not truth
        tn += (not pred) and not truth
        fn += (not pred) and truth
    div = lambda a, b: a / b if b else float("nan")  # noqa: E731
    sens, spec, ppv, npv = div(tp, tp + fn), div(tn, tn + fp), div(tp, tp + fp), div(tn, tn + fn)
    f1 = div(2 * tp, 2 * tp + fp + fn)
    return {"threshold": minimum, "tp": tp, "fp": fp, "tn": tn, "fn": fn, "sensitivity": sens, "specificity": spec, "ppv": ppv, "npv": npv, "f1": f1}


def trajectory_agreement(records, T: float = 5) -> dict:
    rows = [(trajectory([ai_total(r) for r in v], T), v[0]["trajectory_type"]) for v in by_trajectory(records).values() if len(v) == 5]
    return {"agreement": mean(a == b for a, b in rows), "n_trajectories": len(rows),
            "pairs": [{"ai": a, "ground_truth": b} for a, b in rows]}


def baselines(train_self_reports: list[dict], records: list[dict]) -> dict:
    """Fitted on train self-report totals only: global median and per-session-index mean."""
    totals = [s["total"] for s in train_self_reports]
    if not totals:
        return {}
    by_index = defaultdict(list)
    for s in train_self_reports:
        by_index[s["session_index"]].append(s["total"])
    med = median(totals)
    idx_mean = {k: mean(v) for k, v in by_index.items()}
    rows = by_trajectory(records)
    change_idx = [abs((idx_mean[b["session_index"]] - idx_mean[a["session_index"]]) - (b["gt_total"] - a["gt_total"]))
                  for v in rows.values() for a, b in zip(v, v[1:]) if b["session_index"] == a["session_index"] + 1]
    return {"global_median": {"value": med, "current_mae": mean(abs(med - r["gt_total"]) for r in records),
                              "change_mae": mean(abs(0 - (b["gt_total"] - a["gt_total"])) for v in rows.values() for a, b in zip(v, v[1:]) if b["session_index"] == a["session_index"] + 1)},
            "session_index_mean": {"values": idx_mean, "current_mae": mean(abs(idx_mean[r["session_index"]] - r["gt_total"]) for r in records),
                                   "change_mae": mean(change_idx)}}


METRICS = {
    "current_mae": current_mae,
    "change_mae": change_mae,
    "item_mae_rated": lambda r: item_metrics(r)["item_mae_rated"],
    "item_exact_match_rated": lambda r: item_metrics(r)["item_exact_match_rated"],
    "coverage": lambda r: item_metrics(r)["coverage"],
    "large_change_direction_accuracy": lambda r: large_change_direction(r)[0],
    "auroc": lambda r: auc_scores(r)["auroc"],
    "auprc": lambda r: auc_scores(r)["auprc"],
}


def bootstrap(records, fn, resamples: int = 2000, seed: int = 42) -> list[float] | None:
    """95% percentile interval, resampling whole trajectories with replacement."""
    groups = list(by_trajectory(records).values())
    if not groups:
        return None
    rng = random.Random(seed)
    values = []
    for _ in range(resamples):
        sample = []
        for k in range(len(groups)):
            rows = groups[rng.randrange(len(groups))]
            sample += [{**r, "run_id": f"{r['run_id']}#{k}"} for r in rows]   # duplicates stay separate trajectories
        v = fn(sample)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            values.append(v)
    if not values:
        return None
    return [float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))]


def summarize(records, *, resamples: int = 2000, seed: int = 42) -> dict:
    out = {}
    for name, fn in METRICS.items():
        point = fn(records)
        out[name] = {"value": point, "ci95": bootstrap(records, fn, resamples, seed)}
    out["large_change_n"] = large_change_direction(records)[1]
    return out

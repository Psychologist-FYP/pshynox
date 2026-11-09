"""Metrics against hand-computed values (fictional records)."""
import math

import pytest

from services.progress.evaluation import calibration, metrics

GT = {"A": [12, 10, 8, 6, 4], "B": [5, 11, 6, 13, 7]}
AI = {"A": [10, 10, 10, 5, 4], "B": [5, 8, 8, 12, 12]}
LABELS = {"A": ["likely", "possible", "possible", "insufficient_evidence", "insufficient_evidence"],
          "B": ["insufficient_evidence", "likely", "possible", "likely", "possible"]}
TYPES = {"A": "decreasing", "B": "fluctuating"}


def items_for(total):
    base = [total // 8] * 8
    for i in range(total % 8):
        base[i] += 1
    return {str(i + 1): v for i, v in enumerate(base)}


def records():
    out = []
    for run in ("A", "B"):
        for i in range(5):
            out.append({"run_id": run, "session_index": i + 1, "ai_items": items_for(AI[run][i]),
                        "gt_items": {str(k): GT[run][i] / 8 for k in range(1, 9)}, "gt_total": GT[run][i],
                        "label": LABELS[run][i], "trajectory_type": TYPES[run]})
    return out


def test_current_and_change_mae():
    r = records()
    assert metrics.current_mae(r) == pytest.approx(16 / 10)       # A errors 2,0,2,1,0; B errors 0,3,2,1,5
    assert metrics.change_mae(r) == pytest.approx(25 / 8)         # A |diff| 2,2,3,1; B |diff| 3,5,3,6


def test_large_change_direction_counts_ties_as_wrong():
    acc, n = metrics.large_change_direction(records())
    assert n == 4 and acc == pytest.approx(0.5)                   # B: (+3,+6) ok, (0,-5) tie, (+4,+7) ok, (0,-6) tie


def test_stratified_mae_and_trajectory_agreement():
    strat = metrics.stratified_mae(records())
    assert strat["decreasing"]["mae"] == pytest.approx(1.0) and strat["fluctuating"]["mae"] == pytest.approx(2.2)
    agree = metrics.trajectory_agreement(records())
    assert agree["n_trajectories"] == 2 and agree["agreement"] == pytest.approx(0.5)   # A decreasing=decreasing; B increasing≠fluctuating


def test_auroc_and_threshold_metrics_match_hand_computation():
    a = metrics.auc_scores(records())
    assert a["positives"] == 4 and a["auroc"] == pytest.approx(18 / 24)                # pairwise count with ties = 0.5
    p = metrics.threshold_metrics(records(), "possible")
    assert (p["tp"], p["fp"], p["tn"], p["fn"]) == (4, 3, 3, 0)
    assert p["specificity"] == pytest.approx(0.5) and p["f1"] == pytest.approx(8 / 11)
    l = metrics.threshold_metrics(records(), "likely")
    assert (l["tp"], l["fp"], l["tn"], l["fn"]) == (3, 0, 6, 1) and l["npv"] == pytest.approx(6 / 7)


def test_item_metrics_and_coverage_with_insufficient_items():
    rec = {"run_id": "X", "session_index": 1, "ai_items": {"1": 2, "2": None, "3": 1, "4": None, "5": 0, "6": 0, "7": 0, "8": 0},
           "gt_items": {"1": 1.5, "2": 3, "3": 1.4, "4": 2, "5": 0.6, "6": 0, "7": 0, "8": 0}, "gt_total": 8.5, "label": None, "trajectory_type": "stable"}
    m = metrics.item_metrics([rec])
    assert m["coverage"] == pytest.approx(6 / 8) and m["insufficient_rate"] == pytest.approx(2 / 8)
    assert m["item_mae_rated"] == pytest.approx((0.5 + 0.4 + 0.6) / 6)
    assert m["item_exact_match_rated"] == pytest.approx(5 / 6)     # 1.5 rounds half-up to 2 (match); 0.6 -> 1 vs 0 (miss)
    assert metrics.ai_total(rec) == 3                              # insufficient items count as 0 in totals


def test_bootstrap_is_deterministic_and_baselines():
    r = records()
    one = metrics.bootstrap(r, metrics.current_mae, resamples=300, seed=42)
    two = metrics.bootstrap(r, metrics.current_mae, resamples=300, seed=42)
    assert one == two and one[0] <= metrics.current_mae(r) <= one[1]
    assert one == [pytest.approx(1.0), pytest.approx(2.2)]          # two trajectories: means of {A,A}, {A,B}, {B,B}
    train = [{"session_index": i + 1, "total": v} for i, v in enumerate([7, 6, 5, 4, 3])]
    b = metrics.baselines(train, r)
    assert b["global_median"]["value"] == 5 and b["global_median"]["current_mae"] == pytest.approx(34 / 10)
    assert b["session_index_mean"]["values"][1] == 7


def test_calibration_isotonic_cross_fitting_and_brier():
    scores = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18]
    labels = [0, 0, 0, 0, 1, 0, 1, 1, 1, 1]
    mapping = calibration.fit(scores, labels)
    preds = [mapping.predict(s) for s in scores]
    assert preds == sorted(preds) and 0 <= preds[0] <= preds[-1] <= 1
    oof = calibration.cross_fitted(scores, labels, [f"t{i // 2}" for i in range(10)], k=5)
    assert len(oof) == 10 and all(0 <= p <= 1 for p in oof)
    assert calibration.brier([0.5, 1.0], [1, 1]) == pytest.approx(0.125)
    rel = calibration.reliability([0.05, 0.15, 0.95], [0, 0, 1], bins=10)
    assert [r["n"] for r in rel] == [1, 1, 1]
    assert math.isclose(calibration.fit([1, 2], [1, 1]).predict(5), 1.0)

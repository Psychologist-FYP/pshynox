"""Build metric reports (with 95% trajectory-bootstrap intervals) from finished or partial benchmark runs.

    python -m services.progress.evaluation.report --run-id <id> [--run-id <id> ...] [--resamples 2000] [--citations 100]

Writes data/research/progress-runs/<id>/report/{report.json,report.md,citation_rating.csv}. Partial runs are marked
incomplete. Synthetic benchmark (LongCounsel-8): depression track only.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from . import calibration, metrics
from .benchmark import RUNS, load_env, run_records, session_snapshot
from .longcounsel import PREPARED, all_labels, load_transcript

LABEL = "Synthetic benchmark (LongCounsel-8): depression track only."


def clean(value):
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else round(value, 4)
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value


def citation_rows(run_id: str, records: list[dict], edition: str, prepared: Path, n: int, seed: int = 42) -> list[dict]:
    """Random cited findings for team/clinician rating. The rating column is left empty on purpose."""
    pool = []
    texts: dict[str, dict] = {}
    for r in records:
        transcript = texts.setdefault(r["run_id"], load_transcript(r["run_id"], edition, prepared))
        snap = session_snapshot(r["run_id"], transcript, r["session_index"])
        by_id = {s["id"]: s for s in snap["segments"]}
        for f in r["checkpoint"]["stage_b"].get("findings", []):
            for e in f.get("evidence", []):
                seg = by_id.get(e["segment_id"])
                if seg:
                    pool.append({"run_id": run_id, "trajectory": r["run_id"], "session_index": r["session_index"], "criterion": f["criterion_id"],
                                 "status": f["status"], "segment_id": e["segment_id"], "speaker_role": seg["speaker_role"],
                                 "cited_text": seg["text"], "basis": e.get("basis", ""),
                                 "supports_claim (yes/partly/no)": "", "rater": "", "notes": ""})
    random.Random(seed).shuffle(pool)
    return pool[:n]


def build(run_id: str, *, runs: Path = RUNS, prepared: Path = PREPARED, resamples: int = 2000, citations: int = 100) -> dict:
    config, records = run_records(run_id, runs, prepared)
    folder = Path(runs) / run_id
    status = json.loads((folder / "status.json").read_text()) if (folder / "status.json").exists() else {}
    expected = config["subset"]["n"] * 5
    complete = len(records) == expected
    full_traj = {k for k, v in metrics.by_trajectory(records).items() if len(v) == 5}
    complete_records = [r for r in records if r["run_id"] in full_traj]
    summary = metrics.summarize(records, resamples=resamples) if records else {}
    if records and not complete:
        # Change/trajectory metrics only on complete trajectories when the run is partial.
        for name in ("change_mae", "large_change_direction_accuracy"):
            fn = metrics.METRICS[name]
            summary[name] = {"value": fn(complete_records), "ci95": metrics.bootstrap(complete_records, fn, resamples)}
        summary["large_change_n"] = metrics.large_change_direction(complete_records)[1]
    train = [s for lab in all_labels(config["edition"], prepared, "train").values() for s in lab["self_report"]]
    scores = [metrics.ai_total(r) for r in records]
    truth = [int(r["gt_total"] >= metrics.CASE_THRESHOLD) for r in records]
    groups = [r["run_id"] for r in records]
    probs = calibration.cross_fitted(scores, truth, groups) if records and config["subset"]["split"] != "test" else []
    if config["subset"]["split"] == "test":
        mapping = calibration.load_active_mapping()
        probs = [mapping.predict(s) for s in scores] if mapping else []
    calib = {"method": "isotonic, 5-fold cross-fitted over trajectories" if config["subset"]["split"] != "test" else "frozen active mapping applied (no refit)",
             "brier": calibration.brier(probs, truth) if probs else None, "reliability": calibration.reliability(probs, truth) if probs else []}
    stage_b = [r["checkpoint"]["stage_b"] for r in records]
    walls = sorted(r["checkpoint"]["wall_seconds"] for r in records)
    runtime_metrics = json.loads((folder / "runtime-metrics.json").read_text()) if (folder / "runtime-metrics.json").exists() else {}
    report = {
        "label": LABEL, "run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(), "config": config, "status": status.get("state"),
        "complete": complete, "sessions_completed": len(records), "sessions_expected": expected, "trajectories_complete": len(full_traj),
        "metrics": summary, "item": metrics.item_metrics(records) if records else {},
        "stratified_mae": metrics.stratified_mae(records) if records else {},
        "depression": {**(metrics.auc_scores(records) if records else {}),
                       "at_possible": metrics.threshold_metrics(records, "possible"), "at_likely": metrics.threshold_metrics(records, "likely"),
                       "calibration": calib, "stage_b_failures": sum(1 for b in stage_b if b.get("status") == "failed"),
                       "label_counts": {lab: sum(1 for b in stage_b if b.get("prediction") == lab) for lab in ("likely", "possible", "unlikely", "insufficient_evidence", None)}},
        "trajectory": metrics.trajectory_agreement(complete_records) if complete_records else {},
        "baselines": metrics.baselines(train, records) if records else {},
        "runtime": {"session_wall_seconds_mean": sum(walls) / len(walls) if walls else None,
                    "session_wall_seconds_median": walls[len(walls) // 2] if walls else None,
                    "stage_a_cache_hits": sum(1 for r in records if r["checkpoint"]["stage_a"]["cache"] == "hit"),
                    "calls": sum(r["checkpoint"]["stage_a"]["calls"] + r["checkpoint"]["stage_b"]["calls"] for r in records),
                    "generated_tokens": sum(r["checkpoint"]["stage_a"]["generated_tokens"] + r["checkpoint"]["stage_b"]["generated_tokens"] for r in records),
                    "model": config["model"], **{k: runtime_metrics.get(k) for k in ("backend", "runtime_version", "runtime_peak_rss_kib", "vram")}},
        "trajectories": [{"run_id": k, "type": v[0]["trajectory_type"], "sessions": [
            {"session_index": r["session_index"], "ai_total": metrics.ai_total(r), "gt_total": r["gt_total"], "label": r["label"],
             "ai_items": r["ai_items"], "gt_items": r["gt_items"], "rated_items": r["checkpoint"]["stage_a"]["rated_items"],
             "findings": r["checkpoint"]["stage_b"].get("findings", []), "rationale": r["checkpoint"]["stage_b"].get("rationale", [])} for r in v]}
            for k, v in metrics.by_trajectory(records).items()],
        "notes": ["AI PHQ-8 totals count insufficient-evidence items as 0; item metrics use rated items only.",
                  "Trajectory types are derived from ground-truth totals (no type field in the release).",
                  "Ground truth is the release's latent PHQ-8 vector (floats); exact match compares with the half-up rounded value.",
                  "Bootstrap: 2,000 trajectory resamples, seed 42, percentile 95% intervals." if resamples == 2000 else f"Bootstrap: {resamples} resamples."],
    }
    report = clean(report)
    out = folder / "report"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1))
    (out / "report.md").write_text(markdown(report))
    if citations and records:
        rows = citation_rows(run_id, records, config["edition"], prepared, citations)
        if rows:
            with (out / "citation_rating.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    return report


def fmt(m: dict | None) -> str:
    if not m or m.get("value") is None:
        return "—"
    ci = m.get("ci95")
    return f"{m['value']:.3f}" + (f" [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else "")


def markdown(r: dict) -> str:
    m, d = r["metrics"], r["depression"]
    lines = [f"# Progress benchmark report — {r['run_id']}", "", f"**{r['label']}**", "",
             f"Arm `{r['config']['arm']}` (evidence {r['config']['evidence']}, history {r['config']['history']}), subset `{r['config']['subset']['path']}`.",
             f"Sessions {r['sessions_completed']}/{r['sessions_expected']}{'' if r['complete'] else ' — **INCOMPLETE RUN**'}.", "",
             "| Metric | Value [95% CI] |", "|---|---|"]
    for name in ("current_mae", "change_mae", "item_mae_rated", "item_exact_match_rated", "coverage", "large_change_direction_accuracy", "auroc", "auprc"):
        lines.append(f"| {name} | {fmt(m.get(name))} |")
    lines += ["", f"Large-change transitions: {m.get('large_change_n')}. Brier (calibrated): {d.get('calibration', {}).get('brier')}.", "",
              "| Threshold | Sens | Spec | PPV | NPV | F1 |", "|---|---|---|---|---|---|"]
    for key in ("at_possible", "at_likely"):
        t = d.get(key, {})
        lines.append(f"| {t.get('threshold')} | {t.get('sensitivity')} | {t.get('specificity')} | {t.get('ppv')} | {t.get('npv')} | {t.get('f1')} |")
    b = r.get("baselines", {})
    if b:
        lines += ["", "Baselines (train self-reports): global median current MAE "
                  f"{b['global_median']['current_mae']}, change MAE {b['global_median']['change_mae']}; session-index mean current MAE "
                  f"{b['session_index_mean']['current_mae']}, change MAE {b['session_index_mean']['change_mae']}."]
    lines += ["", "MAE by derived trajectory type: " + ", ".join(f"{k} {v['mae']} (n={v['n_sessions']})" for k, v in r.get("stratified_mae", {}).items()),
              f"Trajectory descriptor agreement: {r.get('trajectory', {}).get('agreement')} (n={r.get('trajectory', {}).get('n_trajectories')})", "",
              *[f"- {n}" for n in r["notes"]]]
    return "\n".join(lines) + "\n"


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--resamples", type=int, default=2000)
    parser.add_argument("--citations", type=int, default=100)
    args = parser.parse_args()
    rows = []
    for run_id in args.run_id:
        r = build(run_id, resamples=args.resamples, citations=args.citations)
        rows.append(r)
        print(f"{run_id}: {r['sessions_completed']}/{r['sessions_expected']} sessions{'' if r['complete'] else ' (INCOMPLETE)'} -> "
              f"{RUNS / run_id / 'report' / 'report.md'}")
    if len(rows) > 1:
        print("\n| run | arm | current MAE | change MAE | direction acc | AUROC |")
        print("|---|---|---|---|---|---|")
        for r in rows:
            m = r["metrics"]
            print(f"| {r['run_id']}{'*' if not r['complete'] else ''} | {r['config']['arm']} | {fmt(m.get('current_mae'))} | {fmt(m.get('change_mae'))} | "
                  f"{fmt(m.get('large_change_direction_accuracy'))} | {fmt(m.get('auroc'))} |")
        print("* incomplete run")


if __name__ == "__main__":
    main()

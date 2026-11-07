"""Stage A prompt pilot on a fixed set of val-rest-25 sessions (never val-arms-12, never test).

    python -m services.progress.evaluation.prompt_pilot select            # by ground-truth PHQ-8 band, before any run
    python -m services.progress.evaluation.prompt_pilot run --prompt v2 --label v2-attempt1

Stage A only, retrieval evidence. Fresh caches per run so timings are real. Reports per-session coverage, direct vs
indirect evidence, withheld items, citation validity and the pre-registered acceptance criteria
(docs/PROGRESS_PROMPT_V2_PILOT.md).
"""
from __future__ import annotations

import argparse
import json
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .benchmark import RUNS, RunLog, Stopper, load_env, make_runtime, reset_budget, run_stage_a, session_snapshot, write_json
from .longcounsel import PREPARED, PRIMARY_EDITION, ROOT, load_labels, load_transcript
from .subsets import load_subset

FOLDER = RUNS / "prompt-pilot"
REST = ROOT / "data/research/progress-subsets/val-rest-25.json"
ARMS = ROOT / "data/research/progress-subsets/val-arms-12.json"
BANDS = (("low", 0, 5), ("moderate", 5, 10), ("high", 10, 99))
V1_RETRIEVAL_SECONDS = 175.0
V1_WITHHELD = 0  # v1 pilot (run 3): 0 values withheld by validation across 4 Stage A extractions
ITEMS = [str(i) for i in range(1, 9)]


def band(total: float) -> str:
    return next(name for name, lo, hi in BANDS if lo <= total < hi)


def select(seed: int = 42, per_band: int = 2, prepared: Path = PREPARED) -> dict:
    rest, arms = load_subset(REST), load_subset(ARMS)
    if set(rest["run_ids"]) & set(arms["run_ids"]) or rest["split"] != "val":
        raise SystemExit("val-rest-25 must be validation-only and disjoint from val-arms-12")
    path = FOLDER / "selection.json"
    if path.exists():
        raise SystemExit(f"{path} exists; the pilot set is fixed once chosen")
    candidates = {name: [] for name, _, _ in BANDS}
    for run_id in sorted(rest["run_ids"]):
        for s in load_labels(run_id, rest["edition"], prepared)["ground_truth"]:
            candidates[band(s["total"])].append((run_id, s["session_index"], s["total"]))
    rng = random.Random(seed)
    used, chosen = set(), []
    for name, _, _ in BANDS:
        pool = candidates[name][:]
        rng.shuffle(pool)
        picked = [c for c in pool if c[0] not in used][:per_band]   # distinct trajectories across the pilot set
        if len(picked) < per_band:
            raise SystemExit(f"Not enough {name} sessions in val-rest-25")
        used |= {c[0] for c in picked}
        chosen += [{"band": name, "run_id": r, "session_index": i, "gt_phq8": t} for r, i, t in picked]
    selection = {"subset": "val-rest-25", "seed": seed, "rule": "band by ground-truth PHQ-8 total (low <5, moderate 5-<10, high >=10); "
                 "seed-42 shuffle within band; 2 per band; distinct trajectories", "sessions": chosen,
                 "created_at": datetime.now(timezone.utc).isoformat()}
    write_json(path, selection)
    return selection


def summarize_session(result: dict, labels: dict, index: int, snapshot: dict, seconds: float, calls: int) -> dict:
    gt = next(s for s in labels["ground_truth"] if s["session_index"] == index)
    items = result.get("phq_items", {})
    statuses = {k: (items.get(k) or {}).get("status", "missing") for k in ITEMS}
    scores = {k: (items.get(k) or {}).get("score") for k in ITEMS}
    session_ids = {s["id"] for s in snapshot["segments"]}
    cited = [sid for v in items.values() for sid in v.get("evidence_segment_ids", [])] + \
            [sid for v in result.get("domains", {}).values() for sid in v.get("evidence_segment_ids", [])]
    invalid = sum(1 for sid in cited if sid not in session_ids) + sum(1 for w in result.get("withheld", []) if "not supplied" in w["reason"])
    return {"run_id": labels["run_id"], "session_index": index, "gt_total": gt["total"],
            "ai_total": sum(v for v in scores.values() if v is not None),
            "rated": sum(s == "rated" for s in statuses.values()), "denied": sum(s == "explicit_denial" for s in statuses.values()),
            "insufficient": sum(s in {"insufficient_evidence", "missing"} for s in statuses.values()),
            "direct": sum((items.get(k) or {}).get("evidence_strength") == "direct" for k in ITEMS),
            "indirect": sum((items.get(k) or {}).get("evidence_strength") == "indirect" for k in ITEMS),
            "withheld": len(result.get("withheld", [])), "withheld_items": sum(1 for w in result.get("withheld", []) if w["kind"] == "item"),
            "withheld_reasons": [w["reason"] for w in result.get("withheld", [])], "invalid_citations": invalid,
            "seconds": round(seconds, 1), "calls": calls, "groups": {g: v.get("status") for g, v in result.get("groups", {}).items()},
            "items": [{"item": k, "gt": round(gt["items"][k], 2), "ai": scores[k], "status": statuses[k],
                       "strength": (items.get(k) or {}).get("evidence_strength"), "basis": (items.get(k) or {}).get("basis", "")} for k in ITEMS]}


def criteria(rows: list[dict]) -> list[dict]:
    by = {name: [r for r in rows if r["band"] == name] for name, _, _ in BANDS}
    high_items = sum(8 for _ in by["high"])
    high_cov = sum(r["rated"] + r["denied"] for r in by["high"]) / high_items if high_items else 0
    low_err = [abs(r["ai_total"] - r["gt_total"]) for r in by["low"]]
    means = {k: sum(r["ai_total"] for r in v) / len(v) for k, v in by.items() if v}
    mean_seconds = sum(r["seconds"] for r in rows) / len(rows)
    withheld = sum(r["withheld"] for r in rows)
    return [
        {"criterion": "Coverage: high sessions pooled, rated or denied >= 60% of items", "value": f"{high_cov:.0%}", "pass": high_cov >= 0.60},
        {"criterion": "No over-rating: each low session |AI - GT| <= 4", "value": ", ".join(f"{e:.1f}" for e in low_err), "pass": all(e <= 4 for e in low_err)},
        {"criterion": "Direction: mean AI total low < moderate < high", "value": " < ".join(f"{means[k]:.1f}" for k in ("low", "moderate", "high")),
         "pass": means["low"] < means["moderate"] < means["high"]},
        {"criterion": f"Validity: 0 invalid citations and withheld values <= v1 baseline ({V1_WITHHELD})",
         "value": f"invalid {sum(r['invalid_citations'] for r in rows)}, withheld {withheld}",
         "pass": sum(r["invalid_citations"] for r in rows) == 0 and withheld <= V1_WITHHELD},
        {"criterion": f"Cost: mean Stage A retrieval s/session <= 1.25 x {V1_RETRIEVAL_SECONDS:.0f} s", "value": f"{mean_seconds:.0f} s",
         "pass": mean_seconds <= 1.25 * V1_RETRIEVAL_SECONDS},
    ]


def run(prompt: str, label: str, prepared: Path = PREPARED) -> dict:
    import os
    os.environ["PROGRESS_EXTRACT_PROMPT"] = prompt
    selection = json.loads((FOLDER / "selection.json").read_text())
    out = FOLDER / label
    if (out / "results.json").exists():
        raise SystemExit(f"{out}/results.json exists; use a new label for a new attempt")
    log = RunLog(out / "run.log")
    stopper = Stopper(log)
    stopper.install()
    work = out / "work"
    runtime = make_runtime(work, stopper.cancel)
    stopper.runtime = runtime
    from services.note_generation.storage import memory_slot
    rows, archive = [], []
    try:
        with memory_slot(stopper.cancel, timeout=600):
            runtime.load("qwen")
            log(f"prompt pilot {label} prompt={prompt} backend={runtime.metrics.get('backend')}")
            for s in selection["sessions"]:
                transcript = load_transcript(s["run_id"], PRIMARY_EDITION, prepared)
                snapshot = session_snapshot(s["run_id"], transcript, s["session_index"])
                reset_budget(runtime, archive)
                started = time.monotonic()
                result, _ = run_stage_a(snapshot, "retrieval", runtime, work, None)
                seconds = time.monotonic() - started
                row = {"band": s["band"], **summarize_session(result, load_labels(s["run_id"], PRIMARY_EDITION, prepared), s["session_index"],
                                                             snapshot, seconds, len(runtime.metrics["calls"]))}
                rows.append(row)
                write_json(out / "sessions" / f"{s['run_id']}__s{s['session_index']}.json", {"summary": row, "result": result})
                log(f"{s['band']} {s['run_id']} s{s['session_index']} gt={row['gt_total']:.1f} ai={row['ai_total']} rated={row['rated']} "
                    f"denied={row['denied']} insufficient={row['insufficient']} direct={row['direct']} indirect={row['indirect']} "
                    f"withheld={row['withheld']} {seconds:.0f}s")
    finally:
        runtime.close()
    report = {"label": label, "prompt": prompt, "created_at": datetime.now(timezone.utc).isoformat(), "sessions": rows, "criteria": criteria(rows),
              "passed": all(c["pass"] for c in criteria(rows))}
    write_json(out / "results.json", report)
    return report


def markdown(report: dict) -> str:
    lines = [f"#### {report['label']} ({report['prompt']})", "",
             "| Band | Session | GT PHQ-8 | AI PHQ-8 | Rated | Denied | Insufficient | Direct | Indirect | Withheld | Time |", "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in report["sessions"]:
        lines.append(f"| {r['band']} | {r['run_id']} s{r['session_index']} | {r['gt_total']:.1f} | {r['ai_total']} | {r['rated']} | {r['denied']} | "
                     f"{r['insufficient']} | {r['direct']} | {r['indirect']} | {r['withheld']} | {r['seconds']:.0f} s |")
    lines += ["", "Item level (GT / AI, status):", "", "| Session | " + " | ".join(f"item {k}" for k in ITEMS) + " |", "|---|" + "---|" * 8]
    for r in report["sessions"]:
        cells = [f"{i['gt']:.1f} / {'–' if i['ai'] is None else i['ai']}{' D' if i['status'] == 'explicit_denial' else ''}" for i in r["items"]]
        lines.append(f"| {r['band']} {r['run_id']} s{r['session_index']} | " + " | ".join(cells) + " |")
    lines += ["", "| Criterion | Value | Result |", "|---|---|---|"] + [f"| {c['criterion']} | {c['value']} | {'PASS' if c['pass'] else 'FAIL'} |" for c in report["criteria"]]
    return "\n".join(lines) + "\n"


def main():
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("select")
    r = sub.add_parser("run")
    r.add_argument("--prompt", default="v2", choices=["v1", "v2", "v2-attempt1"])
    r.add_argument("--label", required=True)
    m = sub.add_parser("markdown")
    m.add_argument("--label", required=True)
    args = parser.parse_args()
    if args.command == "select":
        print(json.dumps(select(), indent=1))
    elif args.command == "run":
        report = run(args.prompt, args.label)
        print(markdown(report))
    else:
        print(markdown(json.loads((FOLDER / args.label / "results.json").read_text())))


if __name__ == "__main__":
    main()

"""Run the fast DSM pipeline with the real local model on research data (never the clinical DB) and time it.

    python -m services.progress.dsm.fast.run roleplay  --out data/research/progress-runs/dsm-fast-roleplay
    python -m services.progress.dsm.fast.run reference --out data/research/progress-runs/dsm-fast-reference
    python -m services.progress.dsm.fast.run long      --out data/research/progress-runs/dsm-fast-long --minutes 15,30,45

* ``roleplay``: the fictional two-session fixture; Stage A per session, then Stage B in code as of each session. Labels
  and risk rows are compared with the existing ``dsm-v1`` run when ``--v1`` points at it.
* ``reference``: the 10 ``val-rest-25`` sessions of plan §7.1, each as a one-session patient. Derived PHQ-8 vs
  LongCounsel-8 ground truth is reported for information (no ``dsm-v1`` comparison, user decision).
* ``long``: synthetic long transcripts (consecutive sessions of reference trajectories joined), for speed only.

Every model call is logged in ``<out>/calls.jsonl``; ``summary.json`` holds the per-session wall times. Server settings:
``--settings tuned`` (default) or any ``bench.setting`` name. ``--fresh`` ignores the checkpoint cache, so timings are
real; without it, finished calls are reused (resume).
"""
from __future__ import annotations

import argparse
import json
import shutil
import threading
import time
from pathlib import Path

from services.retrieval.engine import digest

from .runtime import TUNED, CallLog, FastRuntime

ROOT = Path(__file__).resolve().parents[4]
PREPARED = ROOT / "data/research/datasets/longcounsel-8/prepared/longcounsel-8-gpt-5.4-mini-low"
REST = ROOT / "data/research/progress-subsets/val-rest-25.json"
FORBIDDEN = [ROOT / "data/research/progress-subsets/test-final-37.json", ROOT / "data/research/progress-subsets/val-arms-12.json"]
REFERENCE = [("3475_pmt_014", 1), ("294_pmt_030", 5), ("3399_het_043", 1), ("1294_bt_003", 4), ("4207_cbt_079", 3), ("1537_cbt_020", 2),
             ("936_cbt_003", 5), ("1784_pdt_046", 4), ("4931_het_044", 2), ("4807_cbt_148", 2)]
DEV = [("746_cbt_099", 2), ("2596_integrative_008", 4), ("866_pdt_049", 3), ("385_cbt_135", 5), ("3642_pdt_040", 3), ("1402_het_004", 1)]


def _guard(run_ids):
    allowed = set(json.loads(REST.read_text())["run_ids"])
    forbidden = {r for p in FORBIDDEN if p.exists() for r in json.loads(p.read_text())["run_ids"]}
    bad = [r for r in run_ids if r not in allowed or r in forbidden]
    if bad:
        raise SystemExit(f"refusing run ids outside val-rest-25: {bad}")


def longcounsel_session(run_id: str, index: int) -> dict:
    _guard([run_id])
    data = json.loads((PREPARED / "transcripts" / f"{run_id}.json").read_text())
    s = next(x for x in data["sessions"] if x["session_index"] == index)
    sid = f"lc8-{run_id}-s{index}"
    segs = [{"id": f"{sid}-{seg['id']}", "session_id": sid, "speaker_role": seg["speaker_role"], "start_ms": seg["start_ms"], "end_ms": seg["end_ms"],
             "text": seg["text"], "revision": 1} for seg in s["segments"]]
    return {"session_id": sid, "index": 1, "segments": segs}


def synthetic_long(minutes: float) -> dict:
    """Consecutive LongCounsel-8 sessions (reference trajectories, in order) joined until ``minutes`` of speech."""
    segs, offset, sid = [], 0, f"synthetic-{int(minutes)}min"
    for run_id, _ in REFERENCE:
        _guard([run_id])
        data = json.loads((PREPARED / "transcripts" / f"{run_id}.json").read_text())
        for s in sorted(data["sessions"], key=lambda x: x["session_index"]):
            for seg in s["segments"]:
                segs.append({"id": f"{sid}-{len(segs)}", "session_id": sid, "speaker_role": seg["speaker_role"], "start_ms": offset + seg["start_ms"],
                             "end_ms": offset + seg["end_ms"], "text": seg["text"], "revision": 1})
            offset = segs[-1]["end_ms"] + 1000
            if offset >= minutes * 60_000:
                return {"session_id": sid, "index": 1, "segments": segs}
    return {"session_id": sid, "index": 1, "segments": segs}


def run_sessions(sessions: list[dict], out: Path, settings, *, fresh: bool, stage_b_each: bool = True, patients: str = "one") -> dict:
    """Stage A (fast) for every session, then Stage B in code. patients='one' (all sessions one patient) or 'each'."""
    from services.note_generation.storage import memory_slot

    from .extract import extract_session_fast
    from .stage_b import predict_rules
    out.mkdir(parents=True, exist_ok=True)
    if fresh and (out / "fast-cache").exists():
        shutil.rmtree(out / "fast-cache")
    run_id = time.strftime("fast-%Y%m%dT%H%M%S")
    log = CallLog(out / "calls.jsonl", run_id=run_id, pipeline="dsm-fast")
    cancel = threading.Event()
    rows = []
    started_all = time.monotonic()
    with memory_slot(cancel, timeout=600):
        runtime = FastRuntime(out / "work", cancel, settings=settings, calllog=log)
        runtime.max_requests = 10_000
        try:
            done: list[dict] = []
            for s in sessions:
                runtime.deadline = time.monotonic() + 3600
                snap = {"session_id": s["session_id"], "revision": digest(s["segments"]), "segments": s["segments"]}
                t0 = time.monotonic()
                calls_before = len([r for r in log.rows if r["kind"] == "call"])
                result = extract_session_fast(snap, runtime, out / "work", progress=lambda m: print(f"  {s['session_id']}: {m}", flush=True))
                ta = time.monotonic() - t0
                (out / f"stage-a-{s['session_id']}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
                s = {**s, "findings": [{"feature_key": k, "review_status": "pending", **v} for k, v in result["findings"].items()]}
                done = (done if patients == "one" else []) + [s]
                t1 = time.monotonic()
                with log.context(session_id=s["session_id"], stage="B"):
                    with log.phase("stage_b_rules"):
                        b = predict_rules(done, runtime=runtime, calllog=log)
                tb = time.monotonic() - t1
                (out / f"stage-b-{s['session_id']}.json").write_text(json.dumps({"results": b["results"], "notes": b["notes"], "model_calls": b["model_calls"]},
                                                                                indent=1, ensure_ascii=False, default=str))
                calls = [r for r in log.rows if r["kind"] == "call"][calls_before:]
                minutes = (s["segments"][-1]["end_ms"] - s["segments"][0]["start_ms"]) / 60000
                row = {"session_id": s["session_id"], "turns": len(s["segments"]), "audio_minutes": round(minutes, 1),
                       "stage_a_s": round(ta, 1), "stage_b_s": round(tb, 2), "total_s": round(ta + tb, 1),
                       "calls": len(calls), "windows": len(result["windows"]), "prompt_tokens": sum(c["prompt_tokens"] or 0 for c in calls),
                       "cache_tokens": sum(c["cache_tokens"] or 0 for c in calls), "prefill_tokens": sum(c["prefill_tokens"] or 0 for c in calls),
                       "prefill_s": round(sum(c["prefill_ms"] or 0 for c in calls) / 1000, 1), "generated_tokens": sum(c["generated_tokens"] or 0 for c in calls),
                       "generation_s": round(sum(c["generation_ms"] or 0 for c in calls) / 1000, 1), "stage_b_model_calls": b["model_calls"],
                       "cache_hits": result["calls"]["cache_hits"], "retries": result["calls"]["retries"], "splits": result["calls"]["splits"],
                       "groups_skipped": [g for g, v in result["triage"].items() if not v["call"]],
                       "labels": {k: r["label"] for k, r in b["results"].items()},
                       "risk_rows": [{"categories": r["categories"], "note_kind": r["note_kind"], "segment_ids": r["segment_ids"]} for r in result["risk_statements"]],
                       "findings": {k: v["status"] for k, v in result["findings"].items() if v["status"] != "insufficient_evidence"},
                       "phq8": {k: v["score"] for k, v in result["phq_items"].items()}, "withheld": len(result["withheld"])}
                rows.append(row)
                print(f"{s['session_id']}: Stage A {row['stage_a_s']} s ({row['calls']} calls, {row['windows']} windows, {row['generated_tokens']} generated), "
                      f"Stage B {row['stage_b_s']} s", flush=True)
        finally:
            runtime.close()
    load = [r for r in log.rows if r["kind"] == "phase" and r["phase"] == "model_load"]
    summary = {"run_id": run_id, "settings": settings.describe(), "wall_s": round(time.monotonic() - started_all, 1),
               "model_load_s": round(sum(r["seconds"] for r in load), 1), "sessions": rows, "reuse_warnings": log.warnings,
               "vram_peak_mib": (runtime.metrics.get("vram") or {}).get("peak_sampled_mib")}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    return summary


def compare_roleplay(summary: dict, v1: Path) -> dict:
    """Labels as of the last session and risk rows per session, against the existing dsm-v1 role-play run."""
    ref_b = json.loads((v1 / "stage-b.json").read_text())["results"]
    last = summary["sessions"][-1]["labels"]
    labels = {k: {"dsm-v1": ref_b[k]["label"], "dsm-fast": last.get(k), "same": ref_b[k]["label"] == last.get(k)} for k in ref_b}
    families = lambda cats: {c.split("_")[0] + ("_" + c.split("_")[1] if c.startswith("self") else "") for c in cats}  # noqa: E731
    risk = []
    for i, row in enumerate(summary["sessions"], 1):
        ref = json.loads((v1 / f"stage-a-s{i}.json").read_text())["risk_statements"]
        mine = [r for r in row["risk_rows"] if r["note_kind"] == "flag"]
        turn = lambda sid: int(sid.rsplit("-t", 1)[1])  # noqa: E731
        for r in [r for r in ref if r["note_kind"] == "flag"]:
            near = [m for m in mine if any(abs(turn(a) - turn(b)) <= 1 for a in r["segment_ids"] for b in m["segment_ids"])]
            fam = [m for m in near if families([c for c in m["categories"]]) & families([c for c in r["categories"] if c not in {"protective_factor", "denial_of_plan_or_intent"}])]
            risk.append({"session": i, "dsm-v1": r["categories"], "segments": r["segment_ids"], "found": bool(fam),
                         "dsm-fast": [m["categories"] for m in near]})
    return {"labels": labels, "labels_same": sum(v["same"] for v in labels.values()), "risk": risk,
            "risk_recall": sum(r["found"] for r in risk) / len(risk) if risk else None}


def phq_report(summary: dict) -> dict:
    rows = []
    for s in summary["sessions"]:
        run_id, idx = s["session_id"][4:].rsplit("-s", 1)
        gt = next(g for g in json.loads((PREPARED / "labels" / f"{run_id}.json").read_text())["ground_truth"] if g["session_index"] == int(idx))
        ai = s["phq8"]
        rated = {k: v for k, v in ai.items() if v is not None and k in gt["items"]}
        rows.append({"session": s["session_id"], "gt_total": gt["total"], "ai_total": sum(rated.values()), "items_rated": len(rated),
                     "item_abs_error": round(sum(abs(v - gt["items"][k]) for k, v in rated.items()) / len(rated), 2) if rated else None,
                     "mde": s["labels"].get("mde")})
    n = len(rows)
    return {"sessions": rows, "current_mae": round(sum(abs(r["ai_total"] - r["gt_total"]) for r in rows) / n, 2) if n else None,
            "coverage": round(sum(r["items_rated"] for r in rows) / (8 * n), 3) if n else None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["roleplay", "reference", "dev", "long"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--settings", default="tuned")
    parser.add_argument("--minutes", default="15,30,45")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--v1", type=Path, default=ROOT / "data/research/progress-runs/dsm-roleplay")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass
    from .bench import setting
    settings = TUNED if args.settings == "tuned" else setting(args.settings)
    if args.command == "roleplay":
        from services.progress.dsm.fixture_run import load_fixture
        summary = run_sessions(load_fixture(ROOT / "tests/fixtures/roleplay_dsm"), args.out, settings, fresh=args.fresh)
        if (args.v1 / "stage-b.json").exists():
            summary["comparison"] = compare_roleplay(summary, args.v1)
            print(json.dumps({k: summary["comparison"][k] for k in ("labels_same", "risk_recall")}))
    elif args.command in {"reference", "dev"}:
        chosen = REFERENCE if args.command == "reference" else DEV
        summary = run_sessions([longcounsel_session(r, i) for r, i in chosen], args.out, settings, fresh=args.fresh, patients="each")
        summary["phq8"] = phq_report(summary)
        print(json.dumps({k: summary["phq8"][k] for k in ("current_mae", "coverage")}))
    else:
        sessions = [synthetic_long(float(m)) for m in args.minutes.split(",")]
        summary = run_sessions(sessions, args.out, settings, fresh=args.fresh, patients="each")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    for s in summary["sessions"]:
        print(f"{s['session_id']:40s} {s['audio_minutes']:5.1f} min  A {s['stage_a_s']:6.1f} s  B {s['stage_b_s']:5.2f} s  calls {s['calls']:3d}  "
              f"prefill {s['prefill_tokens']:6d} ({s['prefill_s']} s)  gen {s['generated_tokens']:5d} ({s['generation_s']} s)")
    print(f"model load {summary['model_load_s']} s; wall {summary['wall_s']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

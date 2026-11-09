"""Run the DSM pipeline with the real local model on a folder of fictional session files (no clinical DB).

    python -m services.progress.dsm.fixture_run --fixture tests/fixtures/roleplay_dsm --out data/research/progress-runs/dsm-roleplay

Each ``s<n>.json`` holds ``{"turns": [{turn_id, speaker, start_ms, end_ms, text}]}``. Results are checkpointed by the
shared Recovery cache under ``<out>``, so rerunning the command resumes. Nothing is written to the clinical database.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
from pathlib import Path

from services.retrieval.engine import digest

from .extraction import extract_session_dsm
from .prediction import predict_dsm


def load_fixture(folder: Path) -> list[dict]:
    sessions = []
    for n, path in enumerate(sorted(folder.glob("s*.json"), key=lambda p: int(p.stem[1:])), start=1):
        data = json.loads(path.read_text())
        segs = [{"id": f"{folder.name}-s{n}-t{t['turn_id']}", "session_id": f"{folder.name}-s{n}", "speaker_role": t["speaker"],
                 "start_ms": t["start_ms"], "end_ms": t["end_ms"], "text": t["text"], "revision": 1} for t in data["turns"]]
        sessions.append({"session_id": f"{folder.name}-s{n}", "index": n, "segments": segs})
    return sessions


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--evidence", choices=["auto", "full", "retrieval"], default="auto")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass
    from services.note_generation.runtime import Runtime
    from services.note_generation.storage import memory_slot
    args.out.mkdir(parents=True, exist_ok=True)
    sessions = load_fixture(args.fixture)
    cancel = threading.Event()
    runtime = Runtime(args.out / "work", cancel)
    log = {"started": time.strftime("%Y-%m-%dT%H:%M:%S"), "stages": []}
    try:
        with memory_slot(cancel):
            for s in sessions:
                started = time.monotonic()
                runtime.deadline = time.monotonic() + float(os.getenv("NOTE_JOB_TIMEOUT", "1800"))
                snap = {"session_id": s["session_id"], "revision": digest(s["segments"]), "segments": s["segments"]}
                result = extract_session_dsm(snap, runtime, args.out / "work", evidence_mode=args.evidence, progress=lambda m: print(f"  s{s['index']}: {m}", flush=True))
                (args.out / f"stage-a-s{s['index']}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
                s["findings"] = [{"feature_key": k, "review_status": "pending", **v} for k, v in result["findings"].items()]
                log["stages"].append({"stage": f"A s{s['index']}", "seconds": round(time.monotonic() - started, 1), "calls": len(runtime.metrics.get("calls", []))})
                print(f"Stage A session {s['index']}: {log['stages'][-1]['seconds']} s", flush=True)
            started = time.monotonic()
            runtime.deadline = time.monotonic() + float(os.getenv("NOTE_JOB_TIMEOUT", "1800"))
            out = predict_dsm(sessions, runtime, args.out / "work", progress=lambda m: print(f"  B: {m}", flush=True))
            log["stages"].append({"stage": "B", "seconds": round(time.monotonic() - started, 1), "calls": len(runtime.metrics.get("calls", []))})
    finally:
        runtime.close()
    summary = {k: {"label": r["label"], "model_label": r["postprocess"].get("model_label"), "met": f"{r['criteria_met_count']}/{r['criteria_required']}",
                   "lines": [e["line"] for e in r["elements"]], "specifiers": [x["label"] for x in r["specifiers"]],
                   "missing_information": r["missing_information"]} for k, r in out["results"].items()}
    (args.out / "stage-b.json").write_text(json.dumps({"results": out["results"], "periods": [{k: v for k, v in p.items() if k != "proposals"} | {"features": list(p["proposals"])} for p in out["periods"]],
                                                       "proposals": out["proposals"], "notes": out["notes"]}, indent=1, ensure_ascii=False, default=str))
    log.update(finished=time.strftime("%Y-%m-%dT%H:%M:%S"), model=runtime.model.get("sha256") if getattr(runtime, "model", None) else None,
               metrics={k: runtime.metrics.get(k) for k in ("backend", "vram", "runtime_peak_rss_kib", "inference_totals")})
    (args.out / "summary.json").write_text(json.dumps({"labels": summary, "run": log}, indent=1, ensure_ascii=False, default=str))
    for k, v in summary.items():
        print(f"{k:12s} {v['label']:22s} (model said {v['model_label']}) {v['met']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

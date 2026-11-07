"""LongCounsel-8 depression-track benchmark for the progress pipeline (user-run; see docs/PROGRESS_BENCHMARK_RUNBOOK.md).

    python -m services.progress.evaluation.benchmark pilot
    python -m services.progress.evaluation.benchmark estimate
    python -m services.progress.evaluation.benchmark run --subset <manifest> --arm <arm> --run-id <id>
    python -m services.progress.evaluation.benchmark status --run-id <id>
    python -m services.progress.evaluation.benchmark freeze --run-id <id> --reason "<why>"
    python -m services.progress.evaluation.benchmark calibrate --run-id <id> [--run-id <id> ...]

Arms: retrieval-current, retrieval-prior, full-current, full-prior (Stage A evidence x Stage B history), frozen.
Checkpoints after every session; rerunning the same command resumes; Ctrl+C stops safely (second Ctrl+C forces).
A run-id is bound to one configuration. Stage A is cached per evidence setting and reused across history settings.
Stage B predicts depression only. Held-out test manifests run only through run_final.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import signal
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from services.retrieval.engine import digest

from .longcounsel import PREPARED, PRIMARY_EDITION, ROOT, canonical, load_transcript, prepared_manifest
from .subsets import load_subset

RUNS = Path(os.getenv("PROGRESS_RUNS_DIR", str(ROOT / "data/research/progress-runs")))
FROZEN_DIR = ROOT / "data/research/progress-frozen"
FROZEN = FROZEN_DIR / "frozen-progress-config.json"
ARMS = {"retrieval-current": ("retrieval", "current"), "retrieval-prior": ("retrieval", "prior"),
        "full-current": ("full", "current"), "full-prior": ("full", "prior")}
CONDITION = "depressive_episode"
STAGE_A_FILES = ["services/progress/prompts.py", "services/progress/extraction.py", "services/progress/validation.py",
                 "services/progress/evidence.py", "services/progress/registry/loader.py", "services/progress/registry/domains.json",
                 "services/progress/registry/instruments.json", "services/note_generation/runtime.py", "services/note_generation/recovery.py",
                 "services/retrieval/engine.py", "services/retrieval/query.py"]
STAGE_B_FILES = ["services/progress/prediction.py", "services/progress/postprocess.py", "services/progress/registry/conditions.json"]
RUNTIME_ENV = ["NOTE_CONTEXT", "NOTE_GPU_LAYERS", "NOTE_BATCH_SIZE", "NOTE_UBATCH_SIZE", "NOTE_SAFETY_MARGIN", "NOTE_OVERVIEW_OUTPUT",
               "NOTE_OVERVIEW_MAX_OUTPUT", "NOTE_MAX_SPLIT_DEPTH", "NOTE_MIN_SPAN_CHARS", "NOTE_QWEN_MODEL"]
SESSION_TIMEOUT = float(os.getenv("PROGRESS_SESSION_TIMEOUT", "1800"))


# ---------------------------------------------------------------- environment & identity

def load_env() -> None:
    """Plain CLI commands do not read .env; exported variables still take precedence."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass


def file_hashes(files: list[str], root: Path = ROOT) -> dict[str, str]:
    return {f: hashlib.sha256((root / f).read_bytes()).hexdigest() for f in files}


def runtime_settings() -> dict:
    return {k: os.getenv(k) for k in RUNTIME_ENV}


def model_identity() -> dict:
    from services.note_generation.runtime import manifest, model_path
    m = manifest(model_path("qwen"))
    return {"path": m["path"], "sha256": m["sha256"], "size_bytes": m["size_bytes"]}


def extract_prompt() -> str:
    from services.progress.extraction import default_prompt
    return default_prompt()


def stage_a_key(evidence: str, model: dict, edition: str) -> str:
    return canonical({"evidence": evidence, "code": file_hashes(STAGE_A_FILES), "model": model["sha256"], "runtime": runtime_settings(),
                      "edition": edition, "extract_prompt": extract_prompt()})[:24]


def run_config(arm: str, subset_path: Path, subset: dict, model: dict, frozen: dict | None = None) -> dict:
    evidence, history = (frozen["evidence"], frozen["history"]) if arm == "frozen" else ARMS[arm]
    return {"arm": arm, "evidence": evidence, "history": history, "condition": CONDITION, "edition": subset["edition"], "extract_prompt": extract_prompt(),
            "subset": {"path": str(Path(subset_path).resolve().relative_to(ROOT)) if Path(subset_path).resolve().is_relative_to(ROOT) else str(subset_path),
                       "sha256": hashlib.sha256(Path(subset_path).read_bytes()).hexdigest(), "split": subset["split"], "n": len(subset["run_ids"])},
            "code_stage_a": file_hashes(STAGE_A_FILES), "code_stage_b": file_hashes(STAGE_B_FILES),
            "model": model, "runtime": runtime_settings(), "stage_a_key": stage_a_key(evidence, model, subset["edition"]),
            **({"frozen_config_sha256": canonical(frozen)} if frozen else {})}


def config_diff(old: dict, new: dict, prefix="") -> list[str]:
    keys = sorted(set(old) | set(new))
    out = []
    for k in keys:
        a, b = old.get(k), new.get(k)
        if isinstance(a, dict) and isinstance(b, dict):
            out += config_diff(a, b, f"{prefix}{k}.")
        elif a != b:
            out.append(f"{prefix}{k}: {a!r} -> {b!r}")
    return out


# ---------------------------------------------------------------- files

def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1))
    tmp.replace(path)


class RunLog:
    """Progress lines only (ids, counts, timings, labels). Never transcript text."""

    def __init__(self, path: Path, echo: bool = True):
        self.path, self.echo = path, echo
        path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, message: str) -> None:
        line = f"{datetime.now().astimezone().isoformat(timespec='seconds')} {message}"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if self.echo:
            print(line, flush=True)


@contextmanager
def run_lock(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ".lock").open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"Run {folder.name} is already running in another process.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


# ---------------------------------------------------------------- pipeline pieces (shared by pilot, run, tests)

def session_snapshot(run_id: str, transcript: dict, index: int) -> dict:
    session = next(s for s in transcript["sessions"] if s["session_index"] == index)
    sid = f"lc8:{run_id}:s{index}"
    rows = [{"id": s["id"], "session_id": sid, "start_ms": s["start_ms"], "end_ms": s["end_ms"], "speaker_role": s["speaker_role"],
             "text": s["text"], "revision": 1} for s in session["segments"]]
    return {"session_id": sid, "revision": digest(rows), "segments": rows}


def phq8_total(stage_a: dict) -> tuple[int, int]:
    items = stage_a.get("phq_items", {})
    rated = [v["score"] for k, v in items.items() if k in {str(i) for i in range(1, 9)} and v.get("score") is not None]
    return sum(rated), len(rated)


def stage_b_inputs(run_id: str, transcript: dict, stage_a_results: list[dict]) -> list[dict]:
    out = []
    for index, result in enumerate(stage_a_results, 1):
        snap = session_snapshot(run_id, transcript, index)
        total, rated = phq8_total(result)
        out.append({"session_id": snap["session_id"], "index": index, "segments": snap["segments"],
                    "observations": [{"domain_key": k, "status_value": v["status_value"], "rating": v["rating"], "timeframe": v["timeframe"],
                                      "duration_text": v.get("duration_text", ""), "review_status": "pending",
                                      "evidence_segment_ids": v["evidence_segment_ids"]} for k, v in result.get("domains", {}).items()],
                    "scores": [{"instrument_key": "phq9", "metric": "phq8", "value": total, "provenance": "AI_GENERATED", "review_status": "pending"}] if rated else []})
    return out


def reset_budget(runtime, archive: list) -> None:
    """One Runtime serves many sessions: archive per-session call metrics and reset the per-job deadline."""
    archive.extend(runtime.metrics.get("calls", []))
    runtime.metrics["calls"] = []
    runtime.deadline = time.monotonic() + SESSION_TIMEOUT


def call_stats(calls: list[dict]) -> dict:
    return {"calls": len(calls), "formatted_input_tokens": sum(c.get("formatted_prompt_tokens", 0) for c in calls),
            "generated_tokens": sum(c.get("tokens_predicted") or 0 for c in calls),
            "request_seconds": round(sum(c.get("request_seconds", 0) for c in calls), 3)}


def run_stage_a(snapshot: dict, evidence: str, runtime, work: Path, cache_file: Path | None):
    from services.progress.extraction import extract_session
    if cache_file is not None and cache_file.exists():
        data = json.loads(cache_file.read_text())
        if data.get("revision") == snapshot["revision"]:
            return data, "hit"
    result = extract_session(snapshot, runtime, work, evidence_mode=evidence)
    runtime.check()  # never cache a result produced while a stop was requested
    if cache_file is not None:
        write_json(cache_file, result)
    return result, "miss"


def run_stage_b(inputs: list[dict], history: str, runtime, work: Path) -> dict:
    from services.note_generation.runtime import GenerationError
    from services.progress.prediction import predict_condition
    started = time.monotonic()
    try:
        r = predict_condition(inputs, CONDITION, runtime, work, history=history)
    except GenerationError as exc:
        if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
            raise
        return {"status": "failed", "error": str(exc)[:300], "prediction": None, "seconds": round(time.monotonic() - started, 3)}
    return {"status": "completed", "prediction": r["prediction"], "criteria_met_count": r["criteria_met_count"],
            "criteria_required": r["criteria_required"], "duration_status": r["duration_status"], "impairment_status": r["impairment_status"],
            "has_evidence": r["has_evidence"], "source_turns": r["source_turns"], "notes": r["postprocess"]["notes"][:20],
            "findings": [{"criterion_id": f["criterion_id"], "status": f["status"], "timeframe": f["timeframe"],
                          "evidence": [{"segment_id": e["segment_id"], "speaker_role": e["speaker_role"], "basis": e["basis"]} for e in f["evidence"]],
                          "counter_evidence": [{"segment_id": e["segment_id"], "basis": e["basis"]} for e in f["counter_evidence"]]}
                         for f in r["findings"]],
            "rationale": r["rationale"], "seconds": round(time.monotonic() - started, 3)}


def make_runtime(folder: Path, cancel: threading.Event):
    from services.note_generation.runtime import Runtime
    return Runtime(folder, cancel)


RUNTIME_FACTORY = make_runtime


class Stopper:
    """First Ctrl+C/SIGTERM: finish nothing new, abandon the in-flight session, close cleanly. Second: force."""

    def __init__(self, log):
        self.cancel = threading.Event()
        self.runtime = None
        self.log = log
        self.count = 0

    def __call__(self, signum, frame):
        self.count += 1
        if self.count == 1:
            self.cancel.set()
            # Abort the in-flight model call now instead of waiting for it (its session is discarded anyway).
            process = getattr(self.runtime, "process", None)
            if process is not None and process.poll() is None:
                process.terminate()
            self.log("stop requested (signal); abandoning the in-flight session, its checkpoint will not be written. Press Ctrl+C again to force.")
            return
        process = getattr(self.runtime, "process", None)
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        self.log("forced stop")
        os._exit(130)

    def install(self):
        signal.signal(signal.SIGINT, self)
        signal.signal(signal.SIGTERM, self)


# ---------------------------------------------------------------- run

def run_subset(subset_path: Path, arm: str, run_id: str, *, runs: Path = RUNS, prepared: Path = PREPARED, allow_test: bool = False,
               model: dict | None = None, install_signals: bool = True, echo: bool = True, stopper: Stopper | None = None) -> int:
    from services.note_generation.runtime import GenerationError
    from services.note_generation.storage import memory_slot
    if not run_id.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("run-id may contain letters, digits, '-' and '_' only")
    if arm not in ARMS and arm != "frozen":
        raise SystemExit(f"Unknown arm {arm}; choose one of {', '.join([*ARMS, 'frozen'])}")
    subset = load_subset(subset_path)
    if subset["split"] == "test" and not allow_test:
        raise SystemExit("Test-split manifests run only through `python -m services.progress.evaluation.run_final` (user only).")
    frozen = json.loads(FROZEN.read_text()) if arm == "frozen" else None
    if arm == "frozen" and frozen is None:
        raise SystemExit("No frozen configuration; run `benchmark freeze` first.")
    if arm == "frozen" and frozen.get("extract_prompt", "v1") != extract_prompt():
        raise SystemExit(f"Frozen configuration uses extraction prompt {frozen.get('extract_prompt', 'v1')}; set PROGRESS_EXTRACT_PROMPT accordingly.")
    model = model or model_identity()
    folder = Path(runs) / run_id
    config = run_config(arm, subset_path, subset, model, frozen)
    log = RunLog(folder / "run.log", echo)
    with run_lock(folder):
        if (folder / "config.json").exists():
            old = json.loads((folder / "config.json").read_text())
            diff = config_diff(old, config)
            if diff:
                raise SystemExit("Refusing to mix configurations within one run-id. Differences:\n  " + "\n  ".join(diff[:20]) +
                                 "\nUse a new --run-id for a new configuration.")
            log(f"resume run={run_id} arm={arm}")
        else:
            write_json(folder / "config.json", config)
            log(f"start run={run_id} arm={arm} evidence={config['evidence']} history={config['history']} subset={subset['name']} n={len(subset['run_ids'])}")
        todo = [(r, s) for r in subset["run_ids"] for s in range(1, 6)]
        done = {(r, s) for r, s in todo if (folder / "sessions" / f"{r}__s{s}.json").exists()}
        total = len(todo)
        status = {"state": "running", "run_id": run_id, "arm": arm, "completed": len(done), "total": total, "pid": os.getpid(),
                  "started_at": datetime.now(timezone.utc).isoformat()}
        write_json(folder / "status.json", status)
        if len(done) == total:
            log(f"already complete: {total}/{total} sessions")
            write_json(folder / "status.json", {**status, "state": "completed"})
            return 0
        stopper = stopper or Stopper(log)
        if install_signals:
            stopper.install()
        cache_root = Path(runs) / "_stage-a" / config["evidence"] / config["stage_a_key"]
        work = Path(runs) / "_work" / run_id
        archive: list = []
        durations: list[float] = []
        exit_code = 0
        runtime = None
        try:
            with memory_slot(stopper.cancel, timeout=600):
                runtime = RUNTIME_FACTORY(work, stopper.cancel)
                stopper.runtime = runtime
                load_started = time.monotonic()
                runtime.load("qwen")
                log(f"model loaded in {time.monotonic() - load_started:.1f}s backend={runtime.metrics.get('backend')}")
                for run_id_traj in subset["run_ids"]:
                    transcript = load_transcript(run_id_traj, subset["edition"], prepared)
                    stage_a_results = []
                    for index in range(1, 6):
                        if stopper.cancel.is_set():
                            raise GenerationError("Cancelled by user")
                        snapshot = session_snapshot(run_id_traj, transcript, index)
                        cache_file = cache_root / f"{run_id_traj}__s{index}.json"
                        checkpoint = folder / "sessions" / f"{run_id_traj}__s{index}.json"
                        if (run_id_traj, index) in done:
                            stage_a_results.append(json.loads(cache_file.read_text()) if cache_file.exists() else run_stage_a(snapshot, config["evidence"], runtime, work, cache_file)[0])
                            continue
                        started = time.monotonic()
                        reset_budget(runtime, archive)
                        a_started = time.monotonic()
                        result_a, cache_state = run_stage_a(snapshot, config["evidence"], runtime, work, cache_file)
                        a_seconds = time.monotonic() - a_started
                        a_calls = call_stats(runtime.metrics["calls"])
                        stage_a_results.append(result_a)
                        reset_budget(runtime, archive)
                        result_b = run_stage_b(stage_b_inputs(run_id_traj, transcript, stage_a_results), config["history"], runtime, work)
                        if stopper.cancel.is_set():
                            raise GenerationError("Cancelled by user")
                        b_calls = call_stats(runtime.metrics["calls"])
                        total_a, rated = phq8_total(result_a)
                        not_done = [g for g, v in result_a.get("groups", {}).items() if v.get("status") != "completed"]
                        if not_done:
                            log(f"WARNING {run_id_traj} s{index}: Stage A groups not extracted ({', '.join(f'{g}={result_a['groups'][g].get('status')}' for g in not_done)})")
                        write_json(checkpoint, {
                            "run_id": run_id_traj, "session_index": index, "arm": arm,
                            "stage_a": {"phq_items": {k: v["score"] for k, v in result_a.get("phq_items", {}).items()}, "phq8_total": total_a,
                                        "rated_items": rated, "groups": {g: v.get("status") for g, v in result_a.get("groups", {}).items()},
                                        "evidence_used": {g: v.get("evidence") for g, v in result_a.get("groups", {}).items()},
                                        "limitations": len(result_a.get("limitations", [])), "cache": cache_state,
                                        "seconds": round(a_seconds, 3), **a_calls},
                            "stage_b": {**result_b, **b_calls}, "wall_seconds": round(time.monotonic() - started, 3),
                            "completed_at": datetime.now(timezone.utc).isoformat()})
                        done.add((run_id_traj, index))
                        durations.append(time.monotonic() - started)
                        mean = sum(durations) / len(durations)
                        remaining = total - len(done)
                        log(f"{len(done)}/{total} {run_id_traj} s{index} {durations[-1]:.1f}s (stageA {cache_state} {a_seconds:.1f}s, "
                            f"stageB {result_b['seconds']:.1f}s, label={result_b.get('prediction')}, phq8={total_a} rated={rated}/8) "
                            f"mean={mean:.1f}s ETA {remaining * mean / 3600:.2f}h")
                        write_json(folder / "status.json", {**status, "completed": len(done), "updated_at": datetime.now(timezone.utc).isoformat(),
                                                             "mean_session_seconds": round(mean, 2), "eta_hours": round(remaining * mean / 3600, 3)})
        except GenerationError as exc:
            if stopper.cancel.is_set():
                log(f"stopped at {len(done)}/{total}; rerun the same command to resume")
                exit_code = 130
            else:
                log(f"failed: {exc}")
                exit_code = 1
        except RuntimeError as exc:  # memory slot busy or cancelled while waiting
            log(f"failed: {exc}")
            exit_code = 130 if stopper.cancel.is_set() else 1
        finally:
            if runtime is not None:
                runtime.close()
                archive.extend(runtime.metrics.get("calls", []))
                metrics = {k: runtime.metrics.get(k) for k in ("backend", "runtime_version", "runtime_peak_rss_kib", "vram", "effective_context_tokens", "loads")}
                write_json(folder / "runtime-metrics.json", {**metrics, "calls": call_stats(archive)})
        state = "completed" if len(done) == total else "stopped" if exit_code == 130 else "failed" if exit_code else "running"
        write_json(folder / "status.json", {**status, "state": state, "completed": len(done), "updated_at": datetime.now(timezone.utc).isoformat()})
        if len(done) == total:
            log(f"completed {total}/{total} sessions")
        return exit_code


def show_status(run_id: str, runs: Path = RUNS) -> None:
    folder = Path(runs) / run_id
    if not (folder / "status.json").exists():
        raise SystemExit(f"No run {run_id}")
    status = json.loads((folder / "status.json").read_text())
    done = len(list((folder / "sessions").glob("*.json"))) if (folder / "sessions").exists() else 0
    print(json.dumps({**status, "completed_on_disk": done}, indent=2))
    lines = (folder / "run.log").read_text().splitlines()[-5:] if (folder / "run.log").exists() else []
    print("\n".join(lines))


# ---------------------------------------------------------------- pilot & estimate

def pilot(runs: Path = RUNS, prepared: Path = PREPARED, edition: str = PRIMARY_EDITION, echo: bool = True) -> dict:
    """Two validation sessions: Stage A full + retrieval, Stage B current + prior. Fresh caches so timings are real."""
    import random
    from .longcounsel import all_labels
    val = sorted(all_labels(edition, prepared, "val"))
    rng = random.Random(42)
    rng.shuffle(val)
    run_id_traj = val[0]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = Path(runs) / "pilot"
    work = folder / "work" / stamp
    log = RunLog(folder / "run.log", echo)
    stopper = Stopper(log)
    stopper.install()
    transcript = load_transcript(run_id_traj, edition, prepared)
    rows = {"stage_a": [], "stage_b": []}
    archive: list = []
    runtime = RUNTIME_FACTORY(work, stopper.cancel)
    stopper.runtime = runtime
    from services.note_generation.storage import memory_slot
    try:
        with memory_slot(stopper.cancel, timeout=600):
            t = time.monotonic()
            runtime.load("qwen")
            load_seconds = time.monotonic() - t
            log(f"pilot trajectory={run_id_traj} model load {load_seconds:.1f}s backend={runtime.metrics.get('backend')}")
            results = {"full": [], "retrieval": []}
            for index in (1, 2):
                snapshot = session_snapshot(run_id_traj, transcript, index)
                for evidence in ("full", "retrieval"):
                    reset_budget(runtime, archive)
                    t = time.monotonic()
                    result, _ = run_stage_a(snapshot, evidence, runtime, work / evidence, None)
                    seconds = time.monotonic() - t
                    results[evidence].append(result)
                    stats = call_stats(runtime.metrics["calls"])
                    rows["stage_a"].append({"session": index, "evidence": evidence, "seconds": round(seconds, 2), **stats,
                                            "turns": len(snapshot["segments"]), "groups": {g: v.get("evidence") for g, v in result["groups"].items()}})
                    log(f"stageA s{index} {evidence}: {seconds:.1f}s calls={stats['calls']} generated={stats['generated_tokens']}")
                for history in ("current", "prior"):
                    if index == 1 and history == "prior":
                        continue
                    reset_budget(runtime, archive)
                    inputs = stage_b_inputs(run_id_traj, transcript, results["full"])
                    t = time.monotonic()
                    out = run_stage_b(inputs, history, runtime, work / f"b-{history}")
                    seconds = time.monotonic() - t
                    stats = call_stats(runtime.metrics["calls"])
                    rows["stage_b"].append({"session": index, "history": history, "seconds": round(seconds, 2), "status": out["status"],
                                            "label": out.get("prediction"), **stats})
                    log(f"stageB s{index} {history}: {seconds:.1f}s label={out.get('prediction')} calls={stats['calls']}")
    finally:
        runtime.close()
    timings = {"created_at": datetime.now(timezone.utc).isoformat(), "trajectory": run_id_traj, "sessions": [1, 2], "extract_prompt": extract_prompt(),
               "model_load_seconds": round(load_seconds, 2), "backend": runtime.metrics.get("backend"),
               "runtime_peak_rss_kib": runtime.metrics.get("runtime_peak_rss_kib"), "vram": runtime.metrics.get("vram"),
               "runtime": runtime_settings(), **rows}
    write_json(folder / "timings.json", timings)
    return timings


def estimate(timings: dict) -> list[dict]:
    def mean(rows):
        rows = list(rows)
        return sum(r["seconds"] for r in rows) / len(rows) if rows else 0.0
    a_full = mean(r for r in timings["stage_a"] if r["evidence"] == "full")
    a_ret = mean(r for r in timings["stage_a"] if r["evidence"] == "retrieval")
    b_cur = mean(r for r in timings["stage_b"] if r["history"] == "current")
    b_pri = mean(r for r in timings["stage_b"] if r["history"] == "prior") or b_cur
    load = timings["model_load_seconds"]
    h = lambda s: round(s / 3600, 2)  # noqa: E731
    steps = [
        {"step": "arm retrieval-current (val-arms-12, 60 sessions; computes retrieval Stage A)", "hours": h(load + 60 * (a_ret + b_cur))},
        {"step": "arm retrieval-prior (reuses retrieval Stage A)", "hours": h(load + 60 * b_pri)},
        {"step": "arm full-current (computes full-transcript Stage A)", "hours": h(load + 60 * (a_full + b_cur))},
        {"step": "arm full-prior (reuses full Stage A)", "hours": h(load + 60 * b_pri)},
        {"step": "optional calibration enlargement (val-rest-25, 125 sessions, frozen arm)", "hours_retrieval": h(load + 125 * (a_ret + b_pri)),
         "hours_full": h(load + 125 * (a_full + b_pri))},
        {"step": "final half-test run (test-final-37, 185 sessions, frozen arm)", "hours_retrieval": h(load + 185 * (a_ret + b_pri)),
         "hours_full": h(load + 185 * (a_full + b_pri))},
    ]
    return [{"per_session_seconds": {"stage_a_full": round(a_full, 1), "stage_a_retrieval": round(a_ret, 1), "stage_b_current": round(b_cur, 1),
                                     "stage_b_prior": round(b_pri, 1), "model_load": round(load, 1)},
             "caveat": "Measured on 2 sessions. Stage B with prior history grows with the number of sessions; later sessions can be slower."}] + steps


# ---------------------------------------------------------------- freeze & calibrate

def run_records(run_id: str, runs: Path = RUNS, prepared: Path = PREPARED) -> tuple[dict, list[dict]]:
    from .longcounsel import load_labels
    from .subsets import derived_type
    folder = Path(runs) / run_id
    config = json.loads((folder / "config.json").read_text())
    records = []
    for path in sorted((folder / "sessions").glob("*.json")):
        c = json.loads(path.read_text())
        labels = load_labels(c["run_id"], config["edition"], prepared)
        gt = next(s for s in labels["ground_truth"] if s["session_index"] == c["session_index"])
        records.append({"run_id": c["run_id"], "session_index": c["session_index"],
                        "ai_items": {str(i): c["stage_a"]["phq_items"].get(str(i)) for i in range(1, 9)},
                        "gt_items": gt["items"], "gt_total": gt["total"], "label": c["stage_b"].get("prediction"),
                        "trajectory_type": derived_type(labels), "checkpoint": c})
    return config, records


def fit_calibration(run_ids: list[str], runs: Path = RUNS, prepared: Path = PREPARED, method: str = "isotonic"):
    from . import calibration
    from .metrics import ai_total
    scores, labels, sources = [], [], {}
    for run_id in run_ids:
        config, records = run_records(run_id, runs, prepared)
        sources[run_id] = {"subset_sha256": config["subset"]["sha256"], "sessions": len(records), "split": config["subset"]["split"]}
        if config["subset"]["split"] == "test":
            raise SystemExit("Calibration is never fitted on test-split runs.")
        scores += [ai_total(r) for r in records]
        labels += [int(r["gt_total"] >= 10) for r in records]
    manifest = prepared_manifest(json.loads((Path(runs) / run_ids[0] / "config.json").read_text())["edition"], prepared)
    return calibration.fit(scores, labels, method, {"runs": sources, "dataset_labels_sha256": manifest["labels_sha256"],
                                                    "target": "P(ground-truth PHQ-8 >= 10) from AI PHQ-8 total"})


def freeze(run_id: str, reason: str, runs: Path = RUNS, prepared: Path = PREPARED, frozen_path: Path = FROZEN) -> dict:
    from . import calibration
    folder = Path(runs) / run_id
    status = json.loads((folder / "status.json").read_text())
    if status["state"] != "completed":
        raise SystemExit(f"Run {run_id} is {status['state']}; only a completed run can be frozen.")
    if Path(frozen_path).exists():
        raise SystemExit(f"{frozen_path} already exists; the configuration is frozen once. Do not tune after freezing.")
    config = json.loads((folder / "config.json").read_text())
    if config["subset"]["split"] != "val":
        raise SystemExit("Freeze a validation-split run only.")
    mapping = fit_calibration([run_id], runs, prepared)
    calibration.save_active(mapping, Path(frozen_path).parent / "calibration")
    calibration.save_active(mapping)
    frozen = {"evidence": config["evidence"], "history": config["history"], "extract_prompt": config.get("extract_prompt", "v1"),
              "chosen_run_id": run_id, "chosen_arm": config["arm"],
              "reason": reason, "code_stage_a": config["code_stage_a"], "code_stage_b": config["code_stage_b"], "model": config["model"],
              "runtime": config["runtime"], "condition": CONDITION, "edition": config["edition"], "calibration_id": mapping.id,
              "frozen_at": datetime.now(timezone.utc).isoformat()}
    write_json(Path(frozen_path), frozen)
    return frozen


def recalibrate(run_ids: list[str], runs: Path = RUNS, prepared: Path = PREPARED, frozen_path: Path = FROZEN) -> dict:
    """Refit calibration on several completed validation runs of the frozen config (e.g. chosen arm + val-rest-25)."""
    from . import calibration
    frozen = json.loads(Path(frozen_path).read_text())
    for run_id in run_ids:
        config = json.loads((Path(runs) / run_id / "config.json").read_text())
        if config["code_stage_a"] != frozen["code_stage_a"] or config["code_stage_b"] != frozen["code_stage_b"] or (config["evidence"], config["history"]) != (frozen["evidence"], frozen["history"]):
            raise SystemExit(f"Run {run_id} does not use the frozen configuration.")
    mapping = fit_calibration(run_ids, runs, prepared)
    calibration.save_active(mapping, Path(frozen_path).parent / "calibration")
    calibration.save_active(mapping)
    frozen["calibration_id"] = mapping.id
    frozen["calibration_runs"] = run_ids
    write_json(Path(frozen_path), frozen)
    return {"calibration_id": mapping.id, "runs": run_ids}


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pilot", help="2-session timing pilot (validation split)")
    e = sub.add_parser("estimate", help="hours per step from the pilot timings")
    e.add_argument("--pilot", type=Path, default=RUNS / "pilot" / "timings.json")
    r = sub.add_parser("run")
    r.add_argument("--subset", type=Path, required=True)
    r.add_argument("--arm", required=True, choices=[*ARMS, "frozen"])
    r.add_argument("--run-id", required=True)
    s = sub.add_parser("status")
    s.add_argument("--run-id", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--run-id", required=True)
    f.add_argument("--reason", required=True)
    c = sub.add_parser("calibrate")
    c.add_argument("--run-id", action="append", required=True)
    args = parser.parse_args(argv)
    if args.command == "pilot":
        from services.note_generation.runtime import GenerationError
        try:
            timings = pilot()
        except (GenerationError, RuntimeError) as exc:
            print(f"pilot stopped: {exc}")
            return 130
        print(json.dumps(estimate(timings), indent=2))
        return 0
    if args.command == "estimate":
        print(json.dumps(estimate(json.loads(args.pilot.read_text())), indent=2))
        return 0
    if args.command == "run":
        return run_subset(args.subset, args.arm, args.run_id)
    if args.command == "status":
        show_status(args.run_id)
        return 0
    if args.command == "freeze":
        print(json.dumps(freeze(args.run_id, args.reason), indent=2))
        return 0
    if args.command == "calibrate":
        print(json.dumps(recalibrate(args.run_id), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

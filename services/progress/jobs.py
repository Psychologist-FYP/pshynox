"""Background progress jobs (Stage A extraction, then Stage B prediction) on the shared local runtime.

Shares the note-generation in-process LOCK and the cross-process memory slot, so a note draft and a progress job
never load models at the same time. Job state lives under LOCAL_DATA_DIR/progress-jobs/<id>/ and the Recovery
checkpoint cache under LOCAL_DATA_DIR/progress-jobs/cache-v4/, so a retry resumes completed groups.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from services.note_generation.runtime import GenerationError, Runtime
from services.note_generation.storage import memory_slot, write
from services.retrieval.engine import SearchScope, digest

ACTIVE: dict[str, tuple[threading.Event, object]] = {}
RUNTIME_FACTORY = lambda folder, cancel: Runtime(folder, cancel)  # noqa: E731 - replaced in tests


def jobs_root() -> Path:
    return Path(os.getenv("LOCAL_DATA_DIR", "data")) / "progress-jobs"


def job_dir(job_id: str) -> Path:
    if not job_id.isalnum() or len(job_id) != 32:
        raise HTTPException(404, detail={"code": "PROGRESS_JOB_NOT_FOUND", "message": "Unknown progress job."})
    return jobs_root() / job_id


def load_job(job_id: str) -> dict:
    path = job_dir(job_id) / "job.json"
    if not path.exists():
        raise HTTPException(404, detail={"code": "PROGRESS_JOB_NOT_FOUND", "message": "Unknown progress job."})
    job = json.loads(path.read_text())
    if job["status"] == "running" and job_id not in ACTIVE:
        job.update(status="interrupted", error="The API restarted during this job. Retry to resume from saved checkpoints.")
    return job


def list_jobs(session_id: str | None = None, patient_id: str | None = None) -> list[dict]:
    out = []
    for path in sorted(jobs_root().glob("*/job.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True):
        job = json.loads(path.read_text())
        if (session_id and job.get("session_id") != session_id) or (patient_id and job.get("patient_id") != patient_id):
            continue
        out.append(load_job(job["id"]))
    return out


def _lock():
    from services.api.note_generation_routes import LOCK
    return LOCK


def guard_session(db, session_id: str):
    """Shared clinical eligibility rules (same as note drafting) plus research-split blocking."""
    from services.api.db import NoteDraftRecord, SessionRecord
    from services.api.main import ensure_no_active_transcription
    session = db.get(SessionRecord, session_id)
    if session is None:
        raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "Session was not found."})
    if session.patient_id is None:
        raise HTTPException(409, detail={"code": "SESSION_UNASSIGNED", "message": "Link this session to a patient first."})
    consent = json.loads(session.consent_json)
    if consent.get("independent_final_reserved"):
        raise HTTPException(403, detail={"code": "RESERVED_FOR_HUMAN_ANNOTATION", "message": "Reserved for independent human annotation."})
    if consent.get("dataset") == "longcounsel-8" and consent.get("source_split") == "test":
        raise HTTPException(403, detail={"code": "HELD_OUT_TEST_SPLIT", "message": "LongCounsel-8 test-split sessions are blocked from clinical AI runs."})
    ensure_no_active_transcription(db, session.id)
    return session


def clinical_snapshot(session_id: str) -> tuple[dict, SearchScope, dict]:
    from services.api.db import MeetingChunkRecord, NoteDraftRecord, PatientRecord, SegmentSourceRecord, TranscriptSegmentRecord
    from services.api.main import _session_segments, session_factory
    with session_factory() as db:
        session = guard_session(db, session_id)
        note = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session.id))
        if note and note.workflow_status != "DRAFT":
            raise HTTPException(409, detail={"code": "NOTE_TERMINAL", "message": "Decided sessions are read-only."})
        rows = [r.model_dump(mode="json") for r in _session_segments(db, session.id)]
        if not rows:
            raise HTTPException(422, detail={"code": "TRANSCRIPT_EMPTY", "message": "Transcript is empty."})
        sources = db.execute(select(SegmentSourceRecord.segment_id, SegmentSourceRecord.recording_id, MeetingChunkRecord.meeting_id)
                             .join(TranscriptSegmentRecord, TranscriptSegmentRecord.id == SegmentSourceRecord.segment_id)
                             .outerjoin(MeetingChunkRecord, MeetingChunkRecord.recording_id == SegmentSourceRecord.recording_id)
                             .where(TranscriptSegmentRecord.session_id == session.id)).all()
        scope = SearchScope(patient_id=session.patient_id, recording_ids={s: r for s, r, _ in sources},
                            timeline_ids={s: f"meeting:{m}" if m else f"recording:{r}" for s, r, m in sources})
        patient = db.get(PatientRecord, session.patient_id)
        intake = json.loads(patient.intake_json) if patient else {}
        context = {"patient_id": session.patient_id, "names": [n for n in (patient.name if patient else "", intake.get("preferred_name", "")) if n]}
    return {"session_id": session_id, "revision": digest(rows), "segments": rows}, scope, context


def _write(job: dict) -> None:
    write(job_dir(job["id"]) / "job.json", job)


def start_job(kind: str, *, session_id: str, request: dict) -> str:
    """kind: 'extraction' (Stage A then Stage B for that session), 'prediction' (Stage B as of session_id) or
    'reanalysis' (registry v2: DSM Stage A on several past sessions, then Stage B as of the last one)."""
    from services.api.main import session_factory
    from services.progress.dsm.registry import registry_kind
    request = {**request, "registry": request.get("registry") or registry_kind()}
    if kind == "reanalysis":
        with session_factory() as db:
            session = guard_session(db, session_id)
            context = {"patient_id": session.patient_id, "names": []}
        snapshot, scope = None, None
        request["registry"] = "dsm"
    elif kind == "extraction":
        if not request.get("transcript_reviewed"):
            raise HTTPException(422, detail={"code": "TRANSCRIPT_REVIEW_REQUIRED", "message": "Confirm that you reviewed the transcript first."})
        snapshot, scope, context = clinical_snapshot(session_id)
    else:
        with session_factory() as db:
            session = guard_session(db, session_id)
            context = {"patient_id": session.patient_id, "names": []}
        snapshot, scope = None, None
    lock = _lock()
    if not lock.acquire(False):
        raise HTTPException(409, detail={"code": "MODEL_BUSY", "message": "Another local model job is running. Try again when it finishes."})
    job_id = uuid.uuid4().hex
    folder = job_dir(job_id)
    job = {"id": job_id, "kind": kind, "status": "running", "stage": "queued", "session_id": session_id,
           "patient_id": context["patient_id"], "request": request, "revision": snapshot["revision"] if snapshot else None,
           "created_at": datetime.now(timezone.utc).isoformat(), "limitations": [], "error": None, "counts": {}}
    cancel = threading.Event()
    try:
        runtime = RUNTIME_FACTORY(folder, cancel)
    except (GenerationError, ValueError) as exc:
        lock.release()
        raise HTTPException(422, detail={"code": "RUNTIME_UNAVAILABLE", "message": str(exc)}) from exc
    _write(job)
    ACTIVE[job_id] = (cancel, runtime)

    def stage(name: str):
        job["stage"] = name
        _write(job)

    def worker():
        try:
            stage("waiting for model memory")
            with memory_slot(cancel):
                dsm = request["registry"] == "dsm"
                if kind == "reanalysis":
                    run_reanalysis(job, runtime, folder, stage)
                elif kind == "extraction":
                    (run_dsm_extraction if dsm else run_extraction)(job, runtime, folder, snapshot, scope, context, stage)
                if kind == "prediction" or (kind == "extraction" and request.get("predict", True)):
                    (run_dsm_prediction_stage if dsm else run_prediction_stage)(job, runtime, folder, session_id, stage, request.get("condition_keys"))
                if cancel.is_set():
                    raise GenerationError("Cancelled by user")
                job["status"] = "completed"
                stage("completed")
        except Exception as exc:  # noqa: BLE001 - recorded on the job, never swallowed silently
            detail = getattr(exc, "detail", None)
            message = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail or exc)
            job.update(status="cancelled" if cancel.is_set() else "failed", error=f"{type(exc).__name__}: {message}"[:1000])
        finally:
            try:
                runtime.close()
            finally:
                metrics = getattr(runtime, "metrics", {}) or {}
                job["metrics_summary"] = {"calls": len(metrics.get("calls", [])), "inference_totals": metrics.get("inference_totals", {}),
                                          "runtime_peak_rss_kib": metrics.get("runtime_peak_rss_kib"), "backend": metrics.get("backend"),
                                          "vram": metrics.get("vram")}
                _write(job)
                ACTIVE.pop(job_id, None)
                lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def run_extraction(job, runtime, folder, snapshot, scope, context, stage):
    from services.api.db import SessionRecord
    from services.api.main import session_factory
    from services.progress import store
    from services.progress.extraction import extract_session
    with session_factory() as db:
        session = db.get(SessionRecord, snapshot["session_id"])
        origin, plans = store.previous_plans(db, session)
    if hasattr(runtime, "deadline"):
        import time
        runtime.deadline = time.monotonic() + float(os.getenv("NOTE_JOB_TIMEOUT", "1800"))
    result = extract_session(snapshot, runtime, folder, evidence_mode=job["request"].get("evidence_mode", "auto"),
                             plans=plans, names=context["names"], progress=stage, scope=scope)
    latest, _, _ = clinical_snapshot(snapshot["session_id"])
    if latest["revision"] != snapshot["revision"]:
        raise GenerationError("Transcript changed during analysis; result withheld. Run the analysis again.")
    write(folder / "extraction.json", result)
    stage("saving observations")
    with session_factory() as db:
        session = db.get(SessionRecord, snapshot["session_id"])
        job["counts"] = store.persist_extraction(db, session, result, job_id=job["id"], origin_session_id=origin, plans=plans)
        db.commit()
    job["limitations"] = result["limitations"]


def run_prediction_stage(job, runtime, folder, as_of_session_id, stage, condition_keys=None):
    try:
        from services.progress.prediction import predict_patient
    except ImportError:  # Stage B not installed (Phase 3 only)
        return
    stage("predicting conditions")
    counts, limitations = predict_patient(job, runtime, folder, as_of_session_id, condition_keys=condition_keys, progress=stage)
    job["counts"] = {**job.get("counts", {}), **counts}
    job["limitations"] = list(dict.fromkeys(job.get("limitations", []) + limitations))


def _dsm_extract_and_save(job, runtime, folder, snapshot, scope, context, stage, batch_id):
    from services.api.db import SessionRecord
    from services.api.main import session_factory
    from services.progress import store
    from services.progress.dsm import store as dsm_store
    from services.progress.dsm.extraction import extract_session_dsm
    with session_factory() as db:
        session = db.get(SessionRecord, snapshot["session_id"])
        origin, plans = store.previous_plans(db, session)
    if hasattr(runtime, "deadline"):
        import time
        runtime.deadline = time.monotonic() + float(os.getenv("NOTE_JOB_TIMEOUT", "1800"))
    result = extract_session_dsm(snapshot, runtime, folder, evidence_mode=job["request"].get("evidence_mode", "auto"), plans=plans,
                                 names=context["names"], progress=stage, scope=scope)
    latest, _, _ = clinical_snapshot(snapshot["session_id"])
    if latest["revision"] != snapshot["revision"]:
        raise GenerationError("Transcript changed during analysis; result withheld. Run the analysis again.")
    write(folder / f"dsm-extraction-{snapshot['session_id']}.json", result)
    stage("saving DSM findings")
    with session_factory() as db:
        session = db.get(SessionRecord, snapshot["session_id"])
        retired = dsm_store.retire_v1_for_session(db, session.id, batch_id)
        counts = dsm_store.persist_findings(db, session, result["findings"], job_id=job["id"], batch_id=batch_id)
        # Questionnaire estimates and plan follow-ups use the unchanged v1 persistence. It replaces the session's unreviewed
        # risk flags, so the DSM flags are written after it.
        legacy = store.persist_extraction(db, session, {"domains": {}, "phq_items": result["phq_items"], "gad7_items": result["gad7_items"],
                                                        "risk_statements": [], "plan_followups": result["plan_followups"],
                                                        "limitations": result["limitations"], "version": result["version"]},
                                          job_id=job["id"], origin_session_id=origin, plans=plans)
        counts["risk_saved"] = dsm_store.persist_risks(db, session, result["risk_statements"], job_id=job["id"])
        db.commit()
    counts.update({k: v for k, v in legacy.items() if k.startswith(("scores", "plans"))}, **{f"retired_{k}": v for k, v in retired.items()})
    return counts, result["limitations"]


def run_dsm_extraction(job, runtime, folder, snapshot, scope, context, stage):
    from services.api.main import session_factory
    from services.progress.dsm import store as dsm_store
    with session_factory() as db:
        batch = dsm_store.create_batch(db, context["patient_id"], "extraction", job["id"], [snapshot["session_id"]], [])
        db.commit()
        job["batch_id"] = batch.id
    counts, limitations = _dsm_extract_and_save(job, runtime, folder, snapshot, scope, context, stage, job["batch_id"])
    job["counts"] = counts
    job["limitations"] = limitations
    _finish_batch(job["batch_id"], "completed")


def _finish_batch(batch_id, status):
    from services.api.db import DsmBatchRecord
    from services.api.main import session_factory
    with session_factory() as db:
        batch = db.get(DsmBatchRecord, batch_id)
        if batch is not None and batch.status == "running":
            batch.status = status
            db.commit()


def run_dsm_prediction_stage(job, runtime, folder, as_of_session_id, stage, condition_keys=None):
    from services.progress.dsm.prediction import predict_patient_dsm
    stage("predicting conditions (DSM criteria)")
    counts, limitations = predict_patient_dsm(job, runtime, folder, as_of_session_id, condition_keys=condition_keys,
                                              batch_id=job.get("batch_id"), progress=stage)
    job["counts"] = {**job.get("counts", {}), **counts}
    job["limitations"] = list(dict.fromkeys(job.get("limitations", []) + limitations))


def run_reanalysis(job, runtime, folder, stage):
    """DSM Stage A on each chosen past session (decided-note and empty sessions are skipped and listed), then Stage B."""
    from services.api.db import NoteDraftRecord
    from services.api.main import session_factory
    from services.progress.dsm import store as dsm_store
    session_ids = job["request"]["session_ids"]
    skipped, eligible = [], []
    with session_factory() as db:
        for sid in session_ids:
            note = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == sid))
            if note and note.workflow_status != "DRAFT":
                skipped.append({"session_id": sid, "reason": "note decided: left as it is"})
            else:
                eligible.append(sid)
    analysed, counts, limitations = [], {}, []
    with session_factory() as db:
        batch = dsm_store.create_batch(db, job["patient_id"], "reanalysis", job["id"], eligible, skipped)
        db.commit()
        job["batch_id"] = batch.id
    for n, sid in enumerate(eligible, start=1):
        stage(f"re-analysing session {n} of {len(eligible)}")
        try:
            snapshot, scope, context = clinical_snapshot(sid)
        except HTTPException as exc:
            skipped.append({"session_id": sid, "reason": (exc.detail or {}).get("message", "not eligible") if isinstance(exc.detail, dict) else str(exc.detail)})
            continue
        part, notes = _dsm_extract_and_save(job, runtime, folder, snapshot, scope, context, stage, job["batch_id"])
        analysed.append(sid)
        for k, v in part.items():
            counts[k] = counts.get(k, 0) + v
        limitations += notes
    job["counts"], job["limitations"] = counts, list(dict.fromkeys(limitations))
    job["skipped"] = skipped
    with session_factory() as db:
        from services.api.db import DsmBatchRecord
        batch = db.get(DsmBatchRecord, job["batch_id"])
        batch.session_ids_json, batch.skipped_json = json.dumps(analysed), json.dumps(skipped)
        db.commit()
    if analysed:
        run_dsm_prediction_stage(job, runtime, folder, analysed[-1], stage)
    _finish_batch(job["batch_id"], "completed")


def cancel_job(job_id: str) -> None:
    load_job(job_id)
    if job_id in ACTIVE:
        event, runtime = ACTIVE[job_id]
        event.set()
        process = getattr(runtime, "process", None)
        if process is not None and process.poll() is None:
            process.terminate()


def retry_job(job_id: str) -> str:
    job = load_job(job_id)
    if job["status"] == "running":
        raise HTTPException(409, detail={"code": "JOB_RUNNING", "message": "This job is still running."})
    return start_job(job["kind"], session_id=job["session_id"], request=job["request"])


def shutdown_progress() -> None:
    for event, runtime in list(ACTIVE.values()):
        event.set()
        process = getattr(runtime, "process", None)
        if process is not None and process.poll() is None:
            process.terminate()

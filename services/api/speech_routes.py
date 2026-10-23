import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from services.api.db import (SessionRecord, RecordingRecord, TranscriptionJobRecord,
                            TranscriptSegmentRecord, SegmentSourceRecord, NoteDraftRecord, utc_now)
from services.speech.audio import MAX_BYTES, SpeechError, decode_audio


class TranscribeRequest(BaseModel):
    recording_id: str = Field(min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=100)


class ConsentRequest(BaseModel):
    recording_consent: bool


def fail(code, message, status=409):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def session_record(db, sid):
    session = db.get(SessionRecord, sid)
    if not session:
        fail("SESSION_NOT_FOUND", "Create a session before uploading audio.", 404)
    return session


def writable(db, sid):
    session_record(db, sid)
    draft = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == sid))
    if draft and draft.workflow_status != "DRAFT":
        fail("NOTE_TERMINAL", "A decided session cannot receive new recordings or transcripts.")


def recording_view(r):
    return {k: getattr(r, k) for k in ("id", "session_id", "duration_ms", "sample_rate", "channels", "created_at")}


def job_view(j):
    return {**{k: getattr(j, k) for k in ("id", "session_id", "recording_id", "status", "error_code", "error_message", "attempt")},
            "metrics": json.loads(j.metrics_json)}


def build_router(db_dependency):
    router = APIRouter(prefix="/api/v1")

    @router.patch("/sessions/{sid}/recording-consent")
    def consent(sid: str, payload: ConsentRequest, db=Depends(db_dependency)):
        writable(db, sid)
        session = session_record(db, sid)
        data = json.loads(session.consent_json)
        data["recording_consent"] = payload.recording_consent
        session.consent_json = json.dumps(data)
        db.commit()
        return data

    @router.get("/sessions/{sid}/recordings")
    def recordings(sid: str, db=Depends(db_dependency)):
        session_record(db, sid)
        return [recording_view(r) for r in db.scalars(select(RecordingRecord).where(RecordingRecord.session_id == sid).order_by(RecordingRecord.created_at))]

    @router.post("/sessions/{sid}/recordings", status_code=201)
    async def upload(sid: str, request: Request, db=Depends(db_dependency)):
        writable(db, sid)
        if json.loads(session_record(db, sid).consent_json).get("recording_consent") is not True:
            fail("CONSENT_REQUIRED", "Confirm recording consent before importing or recording audio.", 403)
        content_type = request.headers.get("content-type", "").split(";")[0]
        extensions = {"audio/wav": ".wav", "audio/x-wav": ".wav", "audio/wave": ".wav",
                      "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a"}
        if content_type not in extensions:
            fail("UNSUPPORTED_AUDIO", "Use WAV or a supported browser audio recording.", 415)
        root = Path(os.getenv("LOCAL_DATA_DIR", "data")).resolve() / "recordings"
        root.mkdir(parents=True, exist_ok=True)
        rid = f"recording-{uuid.uuid4().hex}"
        path = root / (rid + extensions[content_type])
        total = 0
        digest = hashlib.sha256()
        committed = False
        try:
            with path.open("xb") as target:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        fail("AUDIO_TOO_LARGE", "Recording exceeds the 100 MB limit.", 413)
                    target.write(chunk)
                    digest.update(chunk)
            with tempfile.TemporaryDirectory(prefix="pshynox-validate-") as temp:
                info = await run_in_threadpool(decode_audio, path, Path(temp) / "check.wav")
            row = RecordingRecord(id=rid, session_id=sid, path=str(path), sha256=digest.hexdigest(),
                mime=content_type, duration_ms=info["duration_ms"], sample_rate=info["sample_rate"], channels=info["channels"], created_at=utc_now())
            db.add(row)
            db.commit()
            committed = True
            return recording_view(row)
        except SpeechError as exc:
            fail(exc.code, str(exc), 422)
        finally:
            if not committed:
                db.rollback()
                path.unlink(missing_ok=True)

    @router.get("/sessions/{sid}/recordings/{rid}/audio")
    def audio(sid: str, rid: str, db=Depends(db_dependency)):
        row = db.get(RecordingRecord, rid)
        if not row or row.session_id != sid:
            fail("RECORDING_NOT_FOUND", "Recording was not found for this session.", 404)
        if not Path(row.path).is_file():
            fail("AUDIO_MISSING", "The saved recording file is missing.", 404)
        return FileResponse(row.path, media_type=row.mime, headers={"Cache-Control": "no-store"})

    @router.post("/sessions/{sid}/transcribe", status_code=202)
    def transcribe(sid: str, payload: TranscribeRequest, db=Depends(db_dependency)):
        writable(db, sid)
        if json.loads(session_record(db, sid).consent_json).get("recording_consent") is not True:
            fail("CONSENT_REQUIRED", "Recording consent is required.", 403)
        recording = db.get(RecordingRecord, payload.recording_id)
        if not recording or recording.session_id != sid:
            fail("RECORDING_NOT_FOUND", "Recording was not found for this session.", 404)
        existing = db.scalar(select(TranscriptionJobRecord).where(TranscriptionJobRecord.recording_id == recording.id))
        if existing:
            if existing.status != "FAILED":
                return job_view(existing)
            if existing.idempotency_key == payload.idempotency_key:
                return job_view(existing)
            existing.status, existing.error_code, existing.error_message = "QUEUED", None, None
            existing.idempotency_key = payload.idempotency_key
            existing.attempt += 1
            existing.updated_at = utc_now()
            db.commit()
            return job_view(existing)
        row = TranscriptionJobRecord(id=f"job-{uuid.uuid4().hex}", session_id=sid,
            recording_id=recording.id, idempotency_key=payload.idempotency_key, status="QUEUED",
            metrics_json="{}", attempt=1, created_at=utc_now(), updated_at=utc_now())
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            fail("SESSION_JOB_EXISTS", "A transcription request already exists. Refresh the job status.")
        return job_view(row)

    @router.get("/sessions/{sid}/transcription-job")
    def session_job(sid: str, db=Depends(db_dependency)):
        session_record(db, sid)
        row = db.scalar(select(TranscriptionJobRecord).where(TranscriptionJobRecord.session_id == sid).order_by(TranscriptionJobRecord.created_at.desc()))
        return job_view(row) if row else None

    @router.get("/sessions/{sid}/transcription-jobs")
    def session_jobs(sid: str, db=Depends(db_dependency)):
        session_record(db, sid)
        return [job_view(row) for row in db.scalars(select(TranscriptionJobRecord).where(
            TranscriptionJobRecord.session_id == sid).order_by(TranscriptionJobRecord.created_at))]

    @router.get("/sessions/{sid}/transcript-sources")
    def sources(sid: str, db=Depends(db_dependency)):
        session_record(db, sid)
        return dict(db.execute(select(SegmentSourceRecord.segment_id, SegmentSourceRecord.recording_id)
            .join(TranscriptSegmentRecord, TranscriptSegmentRecord.id == SegmentSourceRecord.segment_id)
            .where(TranscriptSegmentRecord.session_id == sid)).all())

    @router.get("/jobs/{job_id}")
    def job(job_id: str, db=Depends(db_dependency)):
        row = db.get(TranscriptionJobRecord, job_id)
        if not row:
            fail("JOB_NOT_FOUND", "Job was not found.", 404)
        return job_view(row)

    return router

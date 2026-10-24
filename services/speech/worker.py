"""Single local worker. One process per SQLite database; ASR runs in a subprocess."""
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path

from sqlalchemy import select, update
from services.api.db import (make_engine, make_session_factory, database_url, utc_now,
    TranscriptionJobRecord, RecordingRecord, TranscriptSegmentRecord, SegmentSourceRecord, NoteDraftRecord, SessionRecord)
from services.speech.audio import SpeechError
from services.speech.transcriber import transcribe_with_metrics


def recover_interrupted(factory):
    with factory() as db:
        db.execute(update(TranscriptionJobRecord).where(TranscriptionJobRecord.status == "RUNNING").values(
            status="FAILED", error_code="WORKER_INTERRUPTED", error_message="Processing was interrupted. Retry this recording.", updated_at=utc_now()))
        db.commit()


def process_one(factory, transcriber=transcribe_with_metrics):
    with factory() as db:
        job = db.scalar(select(TranscriptionJobRecord).where(TranscriptionJobRecord.status == "QUEUED").order_by(TranscriptionJobRecord.created_at))
        if job is None:
            return False
        job_id, session_id = job.id, job.session_id
        claimed = db.execute(update(TranscriptionJobRecord).where(
            TranscriptionJobRecord.id == job_id, TranscriptionJobRecord.status == "QUEUED"
        ).values(status="RUNNING", updated_at=utc_now()))
        if not claimed.rowcount:
            db.rollback()
            return True
        recording = db.get(RecordingRecord, job.recording_id)
        path, recording_id, duration, expected_hash = recording.path, recording.id, recording.duration_ms, recording.sha256
        db.commit()
    try:
        with open(path, "rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected_hash:
                raise SpeechError("AUDIO_CHANGED", "The stored recording has changed; create a new session.")
        segments, metrics = transcriber(path, session_id=session_id)
        # Validate the integration boundary independently of the selected adapter.
        from services.api.schemas import TranscriptSegment
        segments = [TranscriptSegment.model_validate(s) for s in segments]
        if any(s.session_id != session_id or s.end_ms > duration + 100 for s in segments):
            raise SpeechError("INVALID_ASR_OUTPUT", "Transcription returned invalid source references.")
        with factory() as db:
            session = db.get(SessionRecord, session_id)
            if json.loads(session.consent_json).get("recording_consent") is not True:
                raise SpeechError("CONSENT_REQUIRED", "Recording consent was withdrawn; transcript was not saved.")
            draft = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session_id))
            if draft:
                if draft.workflow_status != "DRAFT":
                    raise SpeechError("NOTE_TERMINAL", "Decided sessions cannot receive new transcript text.")
                draft.risk_review_status = "PENDING"
                draft.reviewer_name = None
                draft.risk_review_completed_at = None
                draft.review_note = None
                draft.updated_at = utc_now()
            if db.scalar(select(SegmentSourceRecord.segment_id).where(SegmentSourceRecord.job_id == job_id).limit(1)):
                raise SpeechError("TRANSCRIPT_EXISTS", "This recording already has a saved transcript.")
            for s in segments:
                db.add(TranscriptSegmentRecord(**s.model_dump(mode="json")))
            db.flush()
            for s in segments:
                db.add(SegmentSourceRecord(segment_id=s.id, recording_id=recording_id, job_id=job_id, channel=-1 if metrics.get("channel_mode") == "stereo_mix" else 0))
            job = db.get(TranscriptionJobRecord, job_id)
            job.status = "SUCCEEDED"
            job.metrics_json = json.dumps(metrics)
            job.updated_at = utc_now()
            db.commit()
    except Exception as exc:
        with factory() as db:
            job = db.get(TranscriptionJobRecord, job_id)
            job.status = "FAILED"
            job.error_code = exc.code if isinstance(exc, SpeechError) else "PROCESSING_FAILED"
            job.error_message = str(exc) if isinstance(exc, SpeechError) else "Processing or persistence failed; no transcript was committed."
            job.updated_at = utc_now()
            db.commit()
    return True


def main():
    root = Path(os.getenv("LOCAL_DATA_DIR", "data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(database_url().encode()).hexdigest()[:16]
    with (root / f"worker-{name}.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        factory = make_session_factory(make_engine())
        recover_interrupted(factory)
        while True:
            if not process_one(factory):
                time.sleep(.5)


if __name__ == "__main__":
    main()

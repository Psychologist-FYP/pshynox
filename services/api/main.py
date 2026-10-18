import json
import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from services.api.db import (
    AuditEventRecord,
    NoteDraftRecord,
    SessionRecord,
    TranscriptSegmentRecord,
    get_db,
    make_engine,
    make_session_factory,
    serialize_record,
    utc_now,
)
from services.api.schemas import (
    ErrorResponse,
    EvidenceHit,
    HealthResponse,
    NoteDraft,
    NoteDraftUpdate,
    NoteField,
    NOTE_SECTIONS,
    AuditEvent,
    DecisionRequest,
    NoteWorkflowStatus,
    RiskReviewCompleteRequest,
    RiskReviewStatus,
    ReviewState,
    Session,
    SessionCreate,
    TranscriptSegment,
    TranscriptSegmentUpdate,
    WorkflowStatus,
)
from services.api.retrieval import FixtureRetrievalProvider

APP_VERSION = "0.2.0"
FIXTURE_MODE = os.getenv("FIXTURE_MODE", "true").lower() == "true"
ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "packages" / "contracts" / "fixtures"
engine = make_engine()
session_factory = make_session_factory(engine)
retrieval_provider = FixtureRetrievalProvider()


def migrate() -> None:
    config = Config(str(ROOT / "alembic.ini"))
    command.upgrade(config, "head")


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate()
    yield


app = FastAPI(
    title="Psychologist-FYP Module C API",
    version=APP_VERSION,
    description="Offline documentation support API. Not a diagnostic system.",
    lifespan=lifespan,
)
migrate()


def db_dependency():
    yield from get_db(session_factory)


def _load_fixture(name: str, model: type[Session] | type[TranscriptSegment]) -> object:
    path = FIXTURE_DIR / name
    try:
        with path.open(encoding="utf-8") as fixture:
            payload = json.load(fixture)
        if isinstance(payload, list):
            return [model.model_validate(item) for item in payload]
        return model.model_validate(payload)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load fixture {name}") from exc


def _fixture_session() -> Session:
    session = _load_fixture("session.json", Session)
    if not isinstance(session, Session):
        raise RuntimeError("Invalid session fixture")
    return session


def _fixture_segments() -> list[TranscriptSegment]:
    segments = _load_fixture("transcript.json", TranscriptSegment)
    if not isinstance(segments, list):
        raise RuntimeError("Invalid transcript fixture")
    return segments


def _record_to_session(record: SessionRecord) -> Session:
    return Session.model_validate(serialize_record(record))


def _record_to_segment(record: TranscriptSegmentRecord) -> TranscriptSegment:
    return TranscriptSegment.model_validate(
        {
            "id": record.id,
            "session_id": record.session_id,
            "start_ms": record.start_ms,
            "end_ms": record.end_ms,
            "speaker_role": record.speaker_role,
            "text": record.text,
            "revision": record.revision,
        }
    )


def _default_note_fields() -> list[NoteField]:
    return [
        NoteField(section=section, text="", provenance="MANUAL", evidence_ids=[], review_status="PENDING")
        for section in ("subjective", "objective", "assessment", "plan", "functioning")
    ]


def _record_to_draft(record: NoteDraftRecord) -> NoteDraft:
    return NoteDraft.model_validate(
        {
            "id": record.id,
            "session_id": record.session_id,
            "fields": json.loads(record.fields_json),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "workflow_status": record.workflow_status,
            "risk_review_status": record.risk_review_status,
            "reviewer_name": record.reviewer_name,
            "risk_review_completed_at": record.risk_review_completed_at,
            "review_note": record.review_note,
            "decision_actor_name": record.decision_actor_name,
            "decision_at": record.decision_at,
            "rejection_reason": record.rejection_reason,
        }
    )


def _review_state(record: NoteDraftRecord | None, session_id: str) -> ReviewState:
    if record is None:
        return ReviewState(
            session_id=session_id,
            note_draft_id=None,
            workflow_status=NoteWorkflowStatus.DRAFT,
            risk_review_status=RiskReviewStatus.PENDING,
        )
    return ReviewState(
        session_id=session_id,
        note_draft_id=record.id,
        workflow_status=record.workflow_status,
        risk_review_status=record.risk_review_status,
        reviewer_name=record.reviewer_name,
        risk_review_completed_at=record.risk_review_completed_at,
        review_note=record.review_note,
        decision_actor_name=record.decision_actor_name,
        decision_at=record.decision_at,
        rejection_reason=record.rejection_reason,
    )


def _audit(db: DbSession, session_id: str, draft_id: str | None, event_type: str, actor: str, metadata: dict[str, str]) -> None:
    db.add(AuditEventRecord(
        id=f"audit-{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        note_draft_id=draft_id,
        event_type=event_type,
        actor_name=actor,
        occurred_at=utc_now(),
        metadata_json=json.dumps(metadata),
    ))


def _session_segments(db: DbSession, session_id: str) -> list[TranscriptSegment]:
    record = db.get(SessionRecord, session_id)
    if record:
        rows = db.scalars(
            select(TranscriptSegmentRecord)
            .where(TranscriptSegmentRecord.session_id == session_id)
            .order_by(TranscriptSegmentRecord.start_ms, TranscriptSegmentRecord.id)
        ).all()
        return [_record_to_segment(row) for row in rows]
    if FIXTURE_MODE and session_id == _fixture_session().id:
        return _fixture_segments()
    raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": "Session was not found."})


def _ensure_fixture_session(db: DbSession, session_id: str) -> None:
    fixture = _fixture_session()
    if not (FIXTURE_MODE and session_id == fixture.id) or db.get(SessionRecord, session_id):
        return
    db.add(
        SessionRecord(
            id=fixture.id,
            pseudonym_id=fixture.pseudonym_id,
            session_type=fixture.session_type.value,
            consent_json=json.dumps(fixture.consent),
            workflow_status=fixture.workflow_status.value,
            created_at=fixture.created_at,
        )
    )
    for segment in _fixture_segments():
        db.add(
            TranscriptSegmentRecord(
                id=segment.id,
                session_id=segment.session_id,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                speaker_role=segment.speaker_role.value,
                text=segment.text,
                revision=segment.revision,
            )
        )
    db.commit()


@app.get("/api/v1/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", app_version=APP_VERSION, fixture_mode=FIXTURE_MODE)


@app.get("/api/v1/sessions", response_model=list[Session])
def list_sessions(db: DbSession = Depends(db_dependency)) -> list[Session]:
    records = db.scalars(select(SessionRecord).order_by(SessionRecord.created_at.desc())).all()
    persisted = [_record_to_session(record) for record in records]
    if FIXTURE_MODE and not any(item.id == _fixture_session().id for item in persisted):
        return [_fixture_session(), *persisted]
    return persisted


@app.post("/api/v1/sessions", response_model=Session, status_code=201)
def create_session(payload: SessionCreate, db: DbSession = Depends(db_dependency)) -> Session:
    existing_ids = db.scalars(select(SessionRecord.id)).all()
    sequence = max(
        (int(match.group(1)) for value in existing_ids if (match := re.fullmatch(r"session-(\d{3})", value))),
        default=0,
    ) + 1
    session_id = f"session-{sequence:03d}"
    pseudonym_id = payload.pseudonym_id or f"client-{sequence:03d}"
    record = SessionRecord(
        id=session_id,
        pseudonym_id=pseudonym_id,
        session_type=payload.session_type.value,
        consent_json=json.dumps(payload.consent),
        workflow_status=WorkflowStatus.DRAFT.value,
        created_at=utc_now(),
    )
    db.add(record)
    # New fictional sessions start with a synthetic transcript so the edit workflow
    # can be demonstrated without claiming that audio was transcribed.
    for fixture_segment in _fixture_segments():
        db.add(
            TranscriptSegmentRecord(
                id=f"seg-{uuid.uuid4().hex[:12]}",
                session_id=session_id,
                start_ms=fixture_segment.start_ms,
                end_ms=fixture_segment.end_ms,
                speaker_role=fixture_segment.speaker_role.value,
                text=fixture_segment.text,
                revision=1,
            )
        )
    try:
        db.commit()
        db.refresh(record)
    except (IntegrityError, SQLAlchemyError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": "SESSION_PERSISTENCE_FAILED", "message": "The session could not be saved."},
        ) from exc
    return _record_to_session(record)


@app.get("/api/v1/sessions/{session_id}", response_model=Session, responses={404: {"model": ErrorResponse}})
def get_session(session_id: str, db: DbSession = Depends(db_dependency)) -> Session:
    record = db.get(SessionRecord, session_id)
    if record:
        return _record_to_session(record)
    if FIXTURE_MODE and session_id == _fixture_session().id:
        return _fixture_session()
    raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": "Session was not found."})


@app.get(
    "/api/v1/sessions/{session_id}/transcript",
    response_model=list[TranscriptSegment],
    responses={404: {"model": ErrorResponse}},
)
def get_transcript(session_id: str, db: DbSession = Depends(db_dependency)) -> list[TranscriptSegment]:
    return _session_segments(db, session_id)


@app.patch(
    "/api/v1/sessions/{session_id}/transcript/{segment_id}",
    response_model=TranscriptSegment,
    responses={404: {"model": ErrorResponse}},
)
def update_transcript_segment(
    session_id: str,
    segment_id: str,
    payload: TranscriptSegmentUpdate,
    db: DbSession = Depends(db_dependency),
) -> TranscriptSegment:
    record = db.scalar(
        select(TranscriptSegmentRecord).where(
            TranscriptSegmentRecord.id == segment_id,
            TranscriptSegmentRecord.session_id == session_id,
        )
    )
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "SEGMENT_NOT_FOUND", "message": "Transcript segment was not found for this session."},
        )
    if payload.text is not None:
        record.text = payload.text
    if payload.speaker_role is not None:
        record.speaker_role = payload.speaker_role.value
    record.revision += 1
    try:
        db.commit()
        db.refresh(record)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail={"code": "SEGMENT_PERSISTENCE_FAILED", "message": "The transcript edit could not be saved."},
        ) from exc
    return _record_to_segment(record)


@app.get(
    "/api/v1/sessions/{session_id}/evidence",
    response_model=list[EvidenceHit],
    responses={404: {"model": ErrorResponse}},
)
def get_evidence(session_id: str, query: str = "", db: DbSession = Depends(db_dependency)) -> list[EvidenceHit]:
    if not query.strip():
        return []
    segments = _session_segments(db, session_id)
    return retrieval_provider.search(query, session_id, segments)


@app.get("/api/v1/sessions/{session_id}/note-draft", response_model=NoteDraft)
def get_note_draft(session_id: str, db: DbSession = Depends(db_dependency)) -> NoteDraft:
    _ensure_fixture_session(db, session_id)
    _session_segments(db, session_id)
    record = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session_id))
    if record:
        return _record_to_draft(record)
    now = utc_now()
    draft = NoteDraftRecord(
        id=f"note-{uuid.uuid4().hex[:12]}",
        session_id=session_id,
        fields_json=json.dumps([field.model_dump(mode="json") for field in _default_note_fields()]),
        created_at=now,
        updated_at=now,
        workflow_status=NoteWorkflowStatus.DRAFT.value,
        risk_review_status=RiskReviewStatus.PENDING.value,
    )
    db.add(draft)
    try:
        db.commit()
        db.refresh(draft)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail={"code": "NOTE_PERSISTENCE_FAILED", "message": "The note draft could not be created."}) from exc
    return _record_to_draft(draft)


@app.put("/api/v1/sessions/{session_id}/note-draft", response_model=NoteDraft)
def update_note_draft(session_id: str, payload: NoteDraftUpdate, db: DbSession = Depends(db_dependency)) -> NoteDraft:
    _ensure_fixture_session(db, session_id)
    segments = _session_segments(db, session_id)
    segment_ids = {segment.id for segment in segments}
    for field in payload.fields:
        if any(evidence_id not in segment_ids for evidence_id in field.evidence_ids):
            raise HTTPException(
                status_code=422,
                detail={"code": "INVALID_EVIDENCE_REFERENCE", "message": "Every evidence ID must belong to this session."},
            )
    record = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session_id))
    if record is None:
        get_note_draft(session_id, db)
        record = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session_id))
    assert record is not None
    if record.workflow_status != NoteWorkflowStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail={"code": "NOTE_TERMINAL", "message": "A decided note draft is read-only."})
    record.fields_json = json.dumps([field.model_dump(mode="json") for field in payload.fields])
    record.updated_at = utc_now()
    try:
        db.commit()
        db.refresh(record)
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail={"code": "NOTE_PERSISTENCE_FAILED", "message": "The note draft could not be saved."}) from exc
    return _record_to_draft(record)


@app.get("/api/v1/sessions/{session_id}/review-state", response_model=ReviewState)
def get_review_state(session_id: str, db: DbSession = Depends(db_dependency)) -> ReviewState:
    _ensure_fixture_session(db, session_id)
    _session_segments(db, session_id)
    return _review_state(db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session_id)), session_id)


@app.post("/api/v1/sessions/{session_id}/risk-review/complete", response_model=ReviewState)
def complete_risk_review(session_id: str, payload: RiskReviewCompleteRequest, db: DbSession = Depends(db_dependency)) -> ReviewState:
    _ensure_fixture_session(db, session_id)
    _session_segments(db, session_id)
    record = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session_id))
    if record is None:
        raise HTTPException(status_code=409, detail={"code": "NOTE_DRAFT_REQUIRED", "message": "Save a note draft before completing review."})
    if record.workflow_status != NoteWorkflowStatus.DRAFT.value or record.risk_review_status != RiskReviewStatus.PENDING.value:
        raise HTTPException(status_code=409, detail={"code": "INVALID_REVIEW_TRANSITION", "message": "Risk review is already completed or the note is decided."})
    record.risk_review_status = RiskReviewStatus.COMPLETED.value
    record.reviewer_name = payload.reviewer_name.strip()
    record.risk_review_completed_at = utc_now()
    record.review_note = payload.review_note.strip() if payload.review_note else None
    _audit(db, session_id, record.id, "RISK_REVIEW_COMPLETED", record.reviewer_name, {"status": "COMPLETED"})
    db.commit()
    return _review_state(record, session_id)


@app.post("/api/v1/sessions/{session_id}/approve", response_model=ReviewState)
def approve_note(session_id: str, payload: DecisionRequest, db: DbSession = Depends(db_dependency)) -> ReviewState:
    _ensure_fixture_session(db, session_id)
    _session_segments(db, session_id)
    record = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session_id))
    if record is None:
        raise HTTPException(status_code=409, detail={"code": "NOTE_DRAFT_REQUIRED", "message": "Save a note draft before approval."})
    if record.workflow_status != NoteWorkflowStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail={"code": "INVALID_DECISION_TRANSITION", "message": "This note draft has already been decided."})
    if record.risk_review_status != RiskReviewStatus.COMPLETED.value:
        _audit(db, session_id, record.id, "NOTE_APPROVAL_BLOCKED", payload.actor_name.strip(), {"reason": "RISK_REVIEW_REQUIRED"})
        db.commit()
        raise HTTPException(status_code=409, detail={"code": "RISK_REVIEW_REQUIRED", "message": "Complete clinician risk review before approving this session."})
    record.workflow_status = NoteWorkflowStatus.APPROVED.value
    record.decision_actor_name = payload.actor_name.strip()
    record.decision_at = utc_now()
    _audit(db, session_id, record.id, "NOTE_APPROVED", record.decision_actor_name, {"workflow_status": "APPROVED"})
    db.commit()
    return _review_state(record, session_id)


@app.post("/api/v1/sessions/{session_id}/reject", response_model=ReviewState)
def reject_note(session_id: str, payload: DecisionRequest, db: DbSession = Depends(db_dependency)) -> ReviewState:
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(status_code=422, detail={"code": "REJECTION_REASON_REQUIRED", "message": "Provide a non-empty rejection reason."})
    _ensure_fixture_session(db, session_id)
    _session_segments(db, session_id)
    record = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == session_id))
    if record is None:
        raise HTTPException(status_code=409, detail={"code": "NOTE_DRAFT_REQUIRED", "message": "Save a note draft before rejection."})
    if record.workflow_status != NoteWorkflowStatus.DRAFT.value:
        raise HTTPException(status_code=409, detail={"code": "INVALID_DECISION_TRANSITION", "message": "This note draft has already been decided."})
    record.workflow_status = NoteWorkflowStatus.REJECTED.value
    record.decision_actor_name = payload.actor_name.strip()
    record.decision_at = utc_now()
    record.rejection_reason = payload.reason.strip()
    _audit(db, session_id, record.id, "NOTE_REJECTED", record.decision_actor_name, {"reason": record.rejection_reason})
    db.commit()
    return _review_state(record, session_id)


@app.get("/api/v1/sessions/{session_id}/audit-events", response_model=list[AuditEvent])
def audit_events(session_id: str, db: DbSession = Depends(db_dependency)) -> list[AuditEvent]:
    _ensure_fixture_session(db, session_id)
    _session_segments(db, session_id)
    records = db.scalars(select(AuditEventRecord).where(AuditEventRecord.session_id == session_id).order_by(AuditEventRecord.occurred_at, AuditEventRecord.id)).all()
    return [
        AuditEvent(
            id=item.id, session_id=item.session_id, note_draft_id=item.note_draft_id,
            event_type=item.event_type, actor_name=item.actor_name, occurred_at=item.occurred_at,
            metadata=json.loads(item.metadata_json),
        )
        for item in records
    ]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed.", "details": jsonable_encoder(exc.errors())}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if not isinstance(detail, dict) or "code" not in detail or "message" not in detail:
        detail = {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})

import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DbSession

from services.api.db import (
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
    Session,
    SessionCreate,
    TranscriptSegment,
    TranscriptSegmentUpdate,
    WorkflowStatus,
)

APP_VERSION = "0.2.0"
FIXTURE_MODE = os.getenv("FIXTURE_MODE", "true").lower() == "true"
ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "packages" / "contracts" / "fixtures"
engine = make_engine()
session_factory = make_session_factory(engine)


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
    session_id = f"session-{uuid.uuid4().hex[:12]}"
    record = SessionRecord(
        id=session_id,
        pseudonym_id=payload.pseudonym_id,
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
    record = db.get(SessionRecord, session_id)
    if record:
        segments = db.scalars(
            select(TranscriptSegmentRecord)
            .where(TranscriptSegmentRecord.session_id == session_id)
            .order_by(TranscriptSegmentRecord.start_ms, TranscriptSegmentRecord.id)
        ).all()
        return [_record_to_segment(segment) for segment in segments]
    if FIXTURE_MODE and session_id == _fixture_session().id:
        return _fixture_segments()
    raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": "Session was not found."})


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
    session_exists = db.get(SessionRecord, session_id) is not None
    if not session_exists and not (FIXTURE_MODE and session_id == _fixture_session().id):
        raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND", "message": "Session was not found."})
    evidence = _load_fixture("evidence.json", EvidenceHit)
    if not isinstance(evidence, list):
        return []
    return [hit for hit in evidence if hit.session_id == session_id and query.lower() in hit.query.lower()]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": "Request validation failed.", "details": exc.errors()}},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if not isinstance(detail, dict) or "code" not in detail or "message" not in detail:
        detail = {"code": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})

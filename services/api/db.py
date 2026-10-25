import os
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session as DbSession, mapped_column, relationship, sessionmaker


ROOT = Path(__file__).resolve().parents[2]


class Base(DeclarativeBase):
    pass


class PatientRecord(Base):
    __tablename__ = "patients"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    intake_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), nullable=True, index=True)
    pseudonym_id: Mapped[str] = mapped_column(String(100), nullable=False)
    session_type: Mapped[str] = mapped_column(String(40), nullable=False)
    consent_json: Mapped[str] = mapped_column(Text, nullable=False)
    workflow_status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    segments: Mapped[list["TranscriptSegmentRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class TranscriptSegmentRecord(Base):
    __tablename__ = "transcript_segments"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker_role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    session: Mapped[SessionRecord] = relationship(back_populates="segments")


class NoteDraftRecord(Base):
    __tablename__ = "note_drafts"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    fields_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_revisions_json: Mapped[str] = mapped_column(Text, default="{}")
    sources_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    workflow_status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    risk_review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    reviewer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    risk_review_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    decision_actor_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    note_draft_id: Mapped[str | None] = mapped_column(ForeignKey("note_drafts.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False)


def database_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./data/pshynox.db")


def make_engine(url: str | None = None):
    resolved = url or database_url()
    if resolved.startswith("sqlite:///./"):
        local_path = ROOT / resolved.removeprefix("sqlite:///./")
        local_path.parent.mkdir(parents=True, exist_ok=True)
        resolved = f"sqlite:///{local_path}"
    return create_engine(resolved, connect_args={"check_same_thread": False} if resolved.startswith("sqlite") else {})


def make_session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_db(factory: sessionmaker) -> Generator[DbSession, None, None]:
    db = factory()
    try:
        yield db
    finally:
        db.close()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_record(record: SessionRecord) -> dict[str, Any]:
    import json

    return {
        "id": record.id,
        "pseudonym_id": record.pseudonym_id,
        "patient_id": record.patient_id,
        "session_type": record.session_type,
        "consent": json.loads(record.consent_json),
        "workflow_status": record.workflow_status,
        "created_at": record.created_at,
    }


class RecordingRecord(Base):
    __tablename__ = "recordings"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(100))
    duration_ms: Mapped[int] = mapped_column(Integer)
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TranscriptionJobRecord(Base):
    __tablename__ = "transcription_jobs"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SegmentSourceRecord(Base):
    __tablename__ = "segment_sources"
    segment_id: Mapped[str] = mapped_column(ForeignKey("transcript_segments.id"), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("transcription_jobs.id"))
    channel: Mapped[int] = mapped_column(Integer, default=0)


class MeetingRecord(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    host_hash: Mapped[str] = mapped_column(String(64))
    guest_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    created_ms: Mapped[int] = mapped_column(Integer)
    expires_ms: Mapped[int] = mapped_column(Integer)
    started_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    guest_consented: Mapped[bool] = mapped_column(Boolean, default=False)


class MeetingChunkRecord(Base):
    __tablename__ = "meeting_chunks"
    # Client-generated UUID makes retries idempotent, including after a lost response.
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), unique=True)
    speaker_role: Mapped[str] = mapped_column(String(20))
    offset_ms: Mapped[int] = mapped_column(Integer)

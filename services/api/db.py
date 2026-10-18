import os
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session as DbSession, mapped_column, relationship, sessionmaker


ROOT = Path(__file__).resolve().parents[2]


class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
        "session_type": record.session_type,
        "consent": json.loads(record.consent_json),
        "workflow_status": record.workflow_status,
        "created_at": record.created_at,
    }

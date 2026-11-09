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
    # Nullable since 0008: patient-level progress actions may have no session.
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True, index=True)
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("patients.id"), nullable=True, index=True)
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
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    audio_only: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    connection_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class MeetingChunkRecord(Base):
    __tablename__ = "meeting_chunks"
    # Client-generated UUID makes retries idempotent, including after a lost response.
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), unique=True)
    speaker_role: Mapped[str] = mapped_column(String(20))
    offset_ms: Mapped[int] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    offset_uncertainty_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rtt_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---- Progress monitoring (migration 0008). JSON columns are Text, as elsewhere. ----

class RegistryVersionRecord(Base):
    __tablename__ = "registry_versions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(40))
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class _ReviewColumns:
    provenance: Mapped[str] = mapped_column(String(30))
    review_status: Mapped[str] = mapped_column(String(20), default="pending")
    original_ai_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer_comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    extraction_job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_revisions_json: Mapped[str] = mapped_column(Text, default="{}")
    stale_from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InstrumentScoreRecord(_ReviewColumns, Base):
    __tablename__ = "instrument_scores"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id"), nullable=True, index=True)
    instrument_key: Mapped[str] = mapped_column(String(40))
    registry_version: Mapped[str] = mapped_column(String(80))
    administered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    item_scores_json: Mapped[str] = mapped_column(Text)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_is_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    subtotals_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    retired_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class DomainObservationRecord(_ReviewColumns, Base):
    __tablename__ = "domain_observations"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    domain_key: Mapped[str] = mapped_column(String(80))
    registry_version: Mapped[str] = mapped_column(String(80))
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_value: Mapped[str] = mapped_column(String(30))
    direction: Mapped[str | None] = mapped_column(String(40), nullable=True)
    timeframe: Mapped[str] = mapped_column(String(20), default="current")
    frequency_text: Mapped[str] = mapped_column(String(300), default="")
    intensity_text: Mapped[str] = mapped_column(String(300), default="")
    duration_text: Mapped[str] = mapped_column(String(300), default="")
    basis: Mapped[str] = mapped_column(String(600), default="")
    evidence_segment_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    retired_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class DomainObservationVersionRecord(Base):
    __tablename__ = "domain_observation_versions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    observation_id: Mapped[str] = mapped_column(ForeignKey("domain_observations.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True)
    domain_key: Mapped[str] = mapped_column(String(80))
    version_no: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))  # history | alternative
    snapshot_json: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ConditionPredictionRecord(_ReviewColumns, Base):
    __tablename__ = "condition_predictions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    as_of_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    condition_key: Mapped[str] = mapped_column(String(60))
    subtype_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    prediction: Mapped[str] = mapped_column(String(30))
    probability: Mapped[float | None] = mapped_column(nullable=True)
    calibration_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    criteria_met_count: Mapped[int] = mapped_column(Integer, default=0)
    criteria_required: Mapped[int] = mapped_column(Integer, default=0)
    duration_status: Mapped[str] = mapped_column(String(20), default="unknown")
    impairment_status: Mapped[str] = mapped_column(String(20), default="unknown")
    rule_outs_json: Mapped[str] = mapped_column(Text, default="[]")
    differentials_json: Mapped[str] = mapped_column(Text, default="[]")
    missing_information_json: Mapped[str] = mapped_column(Text, default="[]")
    rationale_json: Mapped[str] = mapped_column(Text, default="[]")
    postprocess_json: Mapped[str] = mapped_column(Text, default="{}")
    model_version: Mapped[str] = mapped_column(String(200), default="")
    prompt_version: Mapped[str] = mapped_column(String(80), default="")
    schema_version: Mapped[str] = mapped_column(String(80), default="")
    registry_version: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    retired_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class CriterionFindingRecord(Base):
    __tablename__ = "criterion_findings"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    prediction_id: Mapped[str] = mapped_column(ForeignKey("condition_predictions.id"), index=True)
    criterion_id: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    timeframe: Mapped[str] = mapped_column(String(20), default="unclear")
    duration_note: Mapped[str] = mapped_column(String(600), default="")


class RiskStatementRecord(_ReviewColumns, Base):
    __tablename__ = "risk_statements"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    segment_ids_json: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    basis: Mapped[str] = mapped_column(String(600), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    retired_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    categories_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    note_kind: Mapped[str] = mapped_column(String(20), default="flag")
    registry_kind: Mapped[str] = mapped_column(String(10), default="v1")


class ChangeEventRecord(Base):
    __tablename__ = "change_events"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    measure_kind: Mapped[str] = mapped_column(String(20))
    measure_key: Mapped[str] = mapped_column(String(80))
    series: Mapped[str] = mapped_column(String(30))
    from_session_id: Mapped[str] = mapped_column(String(100))
    to_session_id: Mapped[str] = mapped_column(String(100))
    delta: Mapped[float | None] = mapped_column(nullable=True)
    from_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    to_label: Mapped[str | None] = mapped_column(String(60), nullable=True)
    rule_id: Mapped[str] = mapped_column(String(60))
    provenance_of_inputs: Mapped[str] = mapped_column(String(20))
    acknowledged_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    comment: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanFollowupRecord(_ReviewColumns, Base):
    __tablename__ = "plan_followups"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    origin_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    origin_note_item_id: Mapped[str] = mapped_column(String(100))
    plan_text: Mapped[str] = mapped_column(String(1000))
    followup_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), default="not_reviewed")
    basis: Mapped[str] = mapped_column(String(600), default="")
    evidence_segment_ids_json: Mapped[str] = mapped_column(Text, default="[]")


class ProgressEvidenceLinkRecord(Base):
    __tablename__ = "progress_evidence_links"
    segment_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(30), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    revision_at_link: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------- progress registry v2 (DSM-5-TR)

class DsmBatchRecord(Base):
    __tablename__ = "dsm_batches"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # extraction | reanalysis | prediction
    job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    session_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    skipped_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="running")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reverted_by: Mapped[str | None] = mapped_column(String(120), nullable=True)


class DsmFindingRecord(_ReviewColumns, Base):
    __tablename__ = "dsm_findings"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    feature_key: Mapped[str] = mapped_column(String(80))
    registry_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30))
    frequency: Mapped[str] = mapped_column(String(40), default="unclear")
    onset_or_duration_text: Mapped[str] = mapped_column(String(300), default="")
    time_frame: Mapped[str] = mapped_column(String(20), default="unclear")
    period_label: Mapped[str] = mapped_column(String(160), default="")
    qualifier: Mapped[str | None] = mapped_column(String(40), nullable=True)
    degree: Mapped[str | None] = mapped_column(String(30), nullable=True)
    contexts_json: Mapped[str] = mapped_column(Text, default="[]")
    linked_items_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_segment_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    counter_check_json: Mapped[str] = mapped_column(Text, default="{}")
    basis: Mapped[str] = mapped_column(String(600), default="")
    clinician_intensity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class DsmFindingVersionRecord(Base):
    __tablename__ = "dsm_finding_versions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    finding_id: Mapped[str] = mapped_column(ForeignKey("dsm_findings.id"), index=True)
    session_id: Mapped[str] = mapped_column(String(100))
    feature_key: Mapped[str] = mapped_column(String(80))
    version_no: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))  # history | alternative
    snapshot_json: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DsmPredictionRecord(_ReviewColumns, Base):
    __tablename__ = "dsm_predictions"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("patients.id"), index=True)
    as_of_session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    condition_key: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(30))
    role: Mapped[str] = mapped_column(String(20))
    period_json: Mapped[str] = mapped_column(Text, default="{}")
    elements_json: Mapped[str] = mapped_column(Text, default="[]")
    specifiers_json: Mapped[str] = mapped_column(Text, default="[]")
    codes_json: Mapped[str] = mapped_column(Text, default="{}")
    differentials_json: Mapped[str] = mapped_column(Text, default="[]")
    missing_information_json: Mapped[str] = mapped_column(Text, default="[]")
    periods_json: Mapped[str] = mapped_column(Text, default="[]")
    proposals_json: Mapped[str] = mapped_column(Text, default="{}")
    criteria_met_count: Mapped[int] = mapped_column(Integer, default=0)
    criteria_required: Mapped[int] = mapped_column(Integer, default=0)
    postprocess_json: Mapped[str] = mapped_column(Text, default="{}")
    model_version: Mapped[str] = mapped_column(String(200), default="")
    prompt_version: Mapped[str] = mapped_column(String(80), default="")
    schema_version: Mapped[str] = mapped_column(String(80), default="")
    registry_version: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class RegistryVerificationRecord(Base):
    __tablename__ = "registry_verifications"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    registry_kind: Mapped[str] = mapped_column(String(20))
    entity_kind: Mapped[str] = mapped_column(String(20))
    entity_id: Mapped[str] = mapped_column(String(80))
    content_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_on: Mapped[str | None] = mapped_column(String(20), nullable=True)
    comment: Mapped[str] = mapped_column(String(2000), default="")
    import_batch_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

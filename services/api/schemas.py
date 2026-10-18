from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SpeakerRole(StrEnum):
    CLIENT = "CLIENT"
    CLINICIAN = "CLINICIAN"
    UNKNOWN = "UNKNOWN"


class SessionType(StrEnum):
    INITIAL_ASSESSMENT = "INITIAL_ASSESSMENT"
    FOLLOW_UP = "FOLLOW_UP"


class WorkflowStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_PROGRESS = "IN_PROGRESS"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    REVIEWED = "REVIEWED"


class Provenance(StrEnum):
    MANUAL = "MANUAL"
    RETRIEVAL_CANDIDATE = "RETRIEVAL_CANDIDATE"
    TEMPLATE = "TEMPLATE"


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_role: SpeakerRole
    text: str
    revision: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_time_range(self) -> "TranscriptSegment":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class EvidenceHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    excerpt: str
    score: float
    section: str = Field(min_length=1)
    query: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range(self) -> "EvidenceHit":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class NoteField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str = Field(min_length=1)
    text: str
    provenance: Provenance
    evidence_ids: list[str]
    review_status: ReviewStatus = ReviewStatus.PENDING


NOTE_SECTIONS = {"subjective", "objective", "assessment", "plan", "functioning"}


class NoteDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    fields: list[NoteField]
    created_at: datetime
    updated_at: datetime


class NoteDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[NoteField]

    @model_validator(mode="after")
    def validate_sections(self) -> "NoteDraftUpdate":
        sections = [field.section for field in self.fields]
        if len(sections) != len(set(sections)) or not set(sections).issubset(NOTE_SECTIONS):
            raise ValueError("Note fields must use unique supported sections")
        return self


class Session(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    pseudonym_id: str = Field(min_length=1)
    session_type: SessionType
    consent: dict[str, str | bool]
    workflow_status: WorkflowStatus
    created_at: datetime


class SessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pseudonym_id: str = Field(min_length=1, max_length=100)
    session_type: SessionType
    consent: dict[str, str | bool]


class TranscriptSegmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, max_length=10000)
    speaker_role: SpeakerRole | None = None

    @model_validator(mode="after")
    def require_edit_value(self) -> "TranscriptSegmentUpdate":
        if self.text is None and self.speaker_role is None:
            raise ValueError("Provide text or speaker_role to update a segment")
        return self


class HealthResponse(BaseModel):
    status: str
    app_version: str
    fixture_mode: bool


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody

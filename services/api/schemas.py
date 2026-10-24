from datetime import date, datetime
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


class RiskReviewStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"


class NoteWorkflowStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


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
    sources_stale: bool = False
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    fields: list[NoteField]
    created_at: datetime
    updated_at: datetime
    workflow_status: NoteWorkflowStatus = NoteWorkflowStatus.DRAFT
    risk_review_status: RiskReviewStatus = RiskReviewStatus.PENDING
    reviewer_name: str | None = None
    risk_review_completed_at: datetime | None = None
    review_note: str | None = None
    decision_actor_name: str | None = None
    decision_at: datetime | None = None
    rejection_reason: str | None = None


class NoteDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: list[NoteField]

    @model_validator(mode="after")
    def validate_sections(self) -> "NoteDraftUpdate":
        sections = [field.section for field in self.fields]
        if len(sections) != len(set(sections)) or not set(sections).issubset(NOTE_SECTIONS):
            raise ValueError("Note fields must use unique supported sections")
        return self


class RiskReviewCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_name: str = Field(min_length=1, max_length=120)
    confirmation: bool
    review_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_confirmation(self) -> "RiskReviewCompleteRequest":
        if not self.reviewer_name.strip() or not self.confirmation:
            raise ValueError("A reviewer name and explicit confirmation are required")
        return self


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_name: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_actor(self) -> "DecisionRequest":
        if not self.actor_name.strip():
            raise ValueError("An actor name is required")
        return self


class ReviewState(BaseModel):
    session_id: str
    note_draft_id: str | None
    workflow_status: NoteWorkflowStatus
    risk_review_status: RiskReviewStatus
    reviewer_name: str | None = None
    risk_review_completed_at: datetime | None = None
    review_note: str | None = None
    decision_actor_name: str | None = None
    decision_at: datetime | None = None
    rejection_reason: str | None = None


class AuditEvent(BaseModel):
    id: str
    session_id: str
    note_draft_id: str | None
    event_type: str
    actor_name: str
    occurred_at: datetime
    metadata: dict[str, str]


class Session(BaseModel):
    patient_id: str | None = None
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    pseudonym_id: str = Field(min_length=1)
    session_type: SessionType
    consent: dict[str, str | bool]
    workflow_status: WorkflowStatus
    created_at: datetime


class SessionCreate(BaseModel):
    patient_id: str | None = Field(default=None, min_length=1, max_length=100)
    model_config = ConfigDict(extra="forbid")

    pseudonym_id: str | None = Field(default=None, min_length=1, max_length=100)
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


class PatientInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=200)
    preferred_name: str = Field(default="", max_length=200)
    date_of_birth: date | None = None
    age: int | None = Field(default=None, ge=0, le=120)
    sex: str = Field(default="", pattern="^(|Female|Male|Intersex|Prefer not to say|Not recorded)$")
    gender: str = Field(default="", max_length=100)
    pronouns: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=80)
    email: str = Field(default="", max_length=254)
    address: str = Field(default="", max_length=1000)
    emergency_contact: str = Field(default="", max_length=200)
    emergency_relationship: str = Field(default="", max_length=100)
    emergency_phone: str = Field(default="", max_length=80)
    guardian: str = Field(default="", max_length=200)
    occupation: str = Field(default="", max_length=200)
    education: str = Field(default="", max_length=200)
    marital_status: str = Field(default="", max_length=100)
    language: str = Field(default="", max_length=100)
    referred_by: str = Field(default="", max_length=200)
    presenting_concerns: str = Field(default="", max_length=5000)
    previous_care: str = Field(default="", max_length=5000)
    medical_history: str = Field(default="", max_length=5000)
    medications: str = Field(default="", max_length=3000)
    allergies: str = Field(default="", max_length=2000)
    family_social_history: str = Field(default="", max_length=5000)
    substance_use: str = Field(default="", max_length=3000)
    goals: str = Field(default="", max_length=3000)
    additional_notes: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def validate_demographics(self):
        if self.date_of_birth:
            today = date.today()
            if self.date_of_birth > today:
                raise ValueError("Date of birth cannot be in the future")
            calculated = today.year - self.date_of_birth.year - ((today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day))
            if calculated > 120:
                raise ValueError("Date of birth must be within the last 120 years")
            if self.age is not None and self.age != calculated:
                raise ValueError("Enter date of birth or age, or ensure they agree")
            self.age = None  # Derive current age from DOB instead of storing a stale age.
        return self


class Patient(PatientInput):
    id: str
    created_at: datetime
    updated_at: datetime


class PatientAssignment(BaseModel):
    patient_id: str = Field(min_length=1, max_length=100)

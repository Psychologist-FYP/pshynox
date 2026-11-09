from datetime import date, datetime
from enum import StrEnum
from typing import Literal

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
    AI_GENERATED = "AI_GENERATED"
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


class EvidenceSource(TranscriptSegment):
    recording_id: str | None = None
    matched: bool = False


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
    # Additive metadata; the original segment link/excerpt remain the central turn.
    window_id: str | None = None
    patient_id: str | None = None
    recording_id: str | None = None
    transcript_revision: str | None = None
    source_segment_ids: list[str] = Field(default_factory=list)
    matched_source_segment_ids: list[str] = Field(default_factory=list)
    sources: list[EvidenceSource] = Field(default_factory=list)
    speaker_role: SpeakerRole | None = None
    rank: int | None = None
    method: str | None = None
    retrieval_diagnostics: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_range(self) -> "EvidenceHit":
        if self.end_ms < self.start_ms:
            raise ValueError("end_ms must be greater than or equal to start_ms")
        return self


class GeneratedNoteItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    text: str
    type: Literal['client_report', 'clinician_observation', 'documented_clinician_assessment', 'ai_proposed_formulation', 'intervention_delivered_or_discussed', 'proposed_plan', 'agreed_plan', 'unknown_or_unresolved']
    source_ids: list[str]
    status: Literal['supported','explicit_denial','conflicting']
    attribution: str
    timeframe: str
    uncertainty: str
    basis: str = ''
    missing_information: list[str] = Field(default_factory=list)
    review_status: Literal['pending', 'accepted', 'edited', 'rejected'] = 'pending'
    review_text: str | None = None
    review_history: list[dict] = Field(default_factory=list)


class FormulationReview(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=120)
    action: Literal['accept', 'edit', 'reject']
    text: str = ''
    expected_updated_at: datetime


class NoteField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section: str = Field(min_length=1)
    text: str
    provenance: Provenance
    evidence_ids: list[str]
    review_status: ReviewStatus = ReviewStatus.PENDING
    generated_items: list[GeneratedNoteItem] = Field(default_factory=list)


NOTE_SECTIONS = {"subjective", "objective", "assessment", "plan", "functioning", "progress", "interventions"}


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
    # Optional since 0008 (approved): patient-level progress actions may have no session.
    session_id: str | None
    note_draft_id: str | None
    event_type: str
    actor_name: str
    occurred_at: datetime
    metadata: dict[str, str]
    patient_id: str | None = None


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


# ---- Progress monitoring contracts (additive). Review status is a label, never a filter. ----

class ProgressReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"
    STALE = "stale"


DomainStatus = Literal["rated", "explicit_denial", "conflicting", "insufficient_evidence"]
Timeframe = Literal["current", "past", "unclear"]
PredictionLabel = Literal["likely", "possible", "unlikely", "insufficient_evidence"]
CriterionStatus = Literal["met", "not_met", "insufficient_evidence", "conflicting"]
RiskCategory = Literal["self_harm", "suicidal_ideation", "harm_to_others", "other_safety"]
TriState = Literal["met", "not_met", "unknown"]
PlanStatus = Literal["not_reviewed", "discussed_done", "discussed_partial", "discussed_not_done", "not_discussed"]
# Items an AI may never estimate (handled by risk statements and clinician entry).
AI_FORBIDDEN_ITEMS = frozenset({("phq9", "9")})


class CitedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    speaker_role: SpeakerRole
    basis: str = Field(default="", max_length=300)
    text: str | None = None
    recording_id: str | None = None
    revision: int | None = None


class ReviewInfo(BaseModel):
    provenance: Provenance
    review_status: ProgressReviewStatus
    reviewer_name: str | None = None
    reviewed_at: datetime | None = None
    reviewer_comment: str | None = None
    original_ai: dict | None = None
    extraction_job_id: str | None = None
    locked: bool = False
    created_at: datetime
    updated_at: datetime


class DomainObservationInput(BaseModel):
    """Clinician-entered observation (provenance MANUAL)."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=120)
    status_value: DomainStatus
    rating: int | None = Field(default=None, ge=0, le=3)
    direction: str | None = Field(default=None, max_length=40)
    timeframe: Timeframe = "current"
    frequency_text: str = Field(default="", max_length=300)
    intensity_text: str = Field(default="", max_length=300)
    duration_text: str = Field(default="", max_length=300)
    basis: str = Field(default="", max_length=600)
    evidence_segment_ids: list[str] = Field(default_factory=list, max_length=40)
    expected_updated_at: datetime | None = None

    @model_validator(mode="after")
    def rating_matches_status(self):
        if (self.status_value == "rated") != (self.rating is not None):
            raise ValueError("A rating (0-3) is required exactly when status_value is 'rated'")
        return self


class DomainObservation(ReviewInfo):
    id: str
    patient_id: str
    session_id: str
    domain_key: str
    registry_version: str
    status_value: DomainStatus
    rating: int | None = Field(default=None, ge=0, le=3)
    direction: str | None = None
    timeframe: Timeframe = "current"
    frequency_text: str = ""
    intensity_text: str = ""
    duration_text: str = ""
    basis: str = ""
    evidence: list[CitedEvidence] = Field(default_factory=list)
    alternatives: list[dict] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)


class InstrumentItemScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str
    score: int | None = Field(default=None, ge=0, le=3)
    evidence_segment_ids: list[str] = Field(default_factory=list)
    basis: str = Field(default="", max_length=300)
    status: Literal["rated", "explicit_denial", "insufficient_evidence", "not_administered"] = "rated"
    evidence_strength: Literal["direct", "indirect"] | None = None


class InstrumentScoreInput(BaseModel):
    """Clinician-administered score, entered item by item."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=120)
    instrument_key: str = Field(min_length=1, max_length=40)
    session_id: str | None = None
    administered_at: date | None = None
    items: dict[str, int | None]

    @model_validator(mode="after")
    def anchored(self):
        if self.session_id is None and self.administered_at is None:
            raise ValueError("Link the administration to a session or give its date")
        if any(v is not None and not 0 <= v <= 3 for v in self.items.values()):
            raise ValueError("Item scores must be 0-3")
        return self


class InstrumentScore(ReviewInfo):
    id: str
    patient_id: str
    session_id: str | None = None
    instrument_key: str
    registry_version: str
    administered_at: datetime | None = None
    items: list[InstrumentItemScore]
    total: int | None = None
    total_is_partial: bool = False
    subtotals: dict[str, dict] = Field(default_factory=dict)
    band: str | None = None
    evidence: list[CitedEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def no_forbidden_ai_items(self):
        if self.provenance == Provenance.AI_GENERATED:
            for item in self.items:
                if (self.instrument_key, item.item_id) in AI_FORBIDDEN_ITEMS and item.score is not None:
                    raise ValueError("PHQ-9 item 9 is clinician-entered only and is never AI-estimated")
        return self


class CriterionFinding(BaseModel):
    id: str
    criterion_id: str
    summary: str = ""
    status: CriterionStatus
    evidence: list[CitedEvidence] = Field(default_factory=list)
    counter_evidence: list[CitedEvidence] = Field(default_factory=list)
    timeframe: Literal["current", "past", "both", "unclear"] = "unclear"
    duration_note: str = ""


class RationaleSentence(BaseModel):
    sentence: str
    segment_ids: list[str] = Field(min_length=1)


class ConditionPrediction(ReviewInfo):
    id: str
    patient_id: str
    as_of_session_id: str
    condition_key: str
    condition_label: str = ""
    subtype_key: str | None = None
    prediction: PredictionLabel
    probability: float | None = Field(default=None, ge=0, le=1)
    probability_note: str | None = None
    criteria_met_count: int = Field(ge=0)
    criteria_required: int = Field(ge=0)
    duration_status: TriState
    impairment_status: TriState
    rule_outs: list[dict] = Field(default_factory=list)
    differentials: list[dict] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    rationale: list[RationaleSentence] = Field(default_factory=list, max_length=5)
    postprocess: dict = Field(default_factory=dict)
    findings: list[CriterionFinding] = Field(default_factory=list)
    model_version: str = ""
    prompt_version: str = ""
    schema_version: str = ""
    registry_version: str = ""


class RiskStatement(ReviewInfo):
    """A flagged statement for clinician review. Deliberately has no numeric field."""
    id: str
    patient_id: str
    session_id: str
    category: RiskCategory
    basis: str = ""
    evidence: list[CitedEvidence] = Field(default_factory=list)


class ChangeEvent(BaseModel):
    id: str
    patient_id: str
    measure_kind: Literal["instrument", "domain", "prediction"]
    measure_key: str
    series: str
    from_session_id: str
    to_session_id: str
    delta: float | None = None
    from_label: str | None = None
    to_label: str | None = None
    rule_id: str
    provenance_of_inputs: Literal["ai", "reviewed", "mixed"]
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    comment: str | None = None
    created_at: datetime


class PlanFollowup(ReviewInfo):
    id: str
    patient_id: str
    origin_session_id: str
    origin_note_item_id: str
    plan_text: str
    followup_session_id: str
    status: PlanStatus
    basis: str = ""
    evidence: list[CitedEvidence] = Field(default_factory=list)


class ProgressReview(BaseModel):
    """Accept, edit (with a replacement value) or reject (with a reason) one AI value."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=120)
    action: Literal["accept", "edit", "reject"]
    value: dict | None = None
    comment: str | None = Field(default=None, max_length=1000)
    reason: str | None = Field(default=None, max_length=1000)
    expected_updated_at: datetime | None = None

    @model_validator(mode="after")
    def action_payload(self):
        if self.action == "edit" and not self.value:
            raise ValueError("An edit requires the replacement value")
        if self.action == "reject" and not (self.reason or "").strip():
            raise ValueError("A rejection requires a reason")
        return self


class ChangeAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=1000)


class PatientProgress(BaseModel):
    patient_id: str
    sessions: list[dict]
    versions: dict[str, str]
    pending_review_counts: dict[str, int]
    instruments: list[dict]
    domains: list[dict]
    domain_groups: list[dict]
    change_events: list[ChangeEvent]
    predictions: list[ConditionPrediction]
    prediction_timeline: list[dict]
    risk_statements: list[RiskStatement]
    plan_followups: list[PlanFollowup]
    instrument_scores: list[InstrumentScore]

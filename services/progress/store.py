"""Persistence, evidence resolution, review state machine and dashboard assembly.

Only this module and ``services/api/progress_routes.py`` write progress rows. The AI cores
(extraction/prediction) receive snapshots and return plain results.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, time, timezone
from typing import Callable

from fastapi import HTTPException
from sqlalchemy import select, delete
from sqlalchemy.orm import Session as DbSession

from services.api.db import (
    AuditEventRecord, ChangeEventRecord, ConditionPredictionRecord, CriterionFindingRecord,
    DomainObservationRecord, DomainObservationVersionRecord, DsmFindingRecord, DsmPredictionRecord, InstrumentScoreRecord, NoteDraftRecord,
    PatientRecord, PlanFollowupRecord, ProgressEvidenceLinkRecord, RegistryVersionRecord, RiskStatementRecord,
    SegmentSourceRecord, SessionRecord, TranscriptSegmentRecord, utc_now,
)
from services.api.schemas import (
    ChangeEvent, CitedEvidence, ConditionPrediction, CriterionFinding, DomainObservation, DomainObservationInput,
    InstrumentItemScore, InstrumentScore, InstrumentScoreInput, PatientProgress, PlanFollowup, ProgressReview,
    RiskStatement,
)
from services.progress import trends
from services.progress.registry import registry

DEMO_PATIENT_PREFIX = "lc8-demo-"
RESEARCH_ONLY_NOTE = "Calibrated probability is research-only (LongCounsel-8 dataset terms); shown for demo patients only."
OWNER_MODELS = {
    "domain_observation": DomainObservationRecord, "instrument_score": InstrumentScoreRecord,
    "prediction": ConditionPredictionRecord, "risk_statement": RiskStatementRecord, "plan_followup": PlanFollowupRecord,
    "dsm_finding": DsmFindingRecord, "dsm_prediction": DsmPredictionRecord,
}
RISK_CATEGORIES = {"self_harm", "suicidal_ideation", "harm_to_others", "other_safety", "suicidal_ideation_passive", "suicidal_ideation_active",
                   "self_harm_past", "self_harm_recent", "protective_factor", "denial_of_plan_or_intent"}


class _Instruments:
    """v1 instruments (PHQ-9, GAD-7; benchmark-bound, unchanged) plus the clinician-only instruments of registry v2."""

    def __init__(self):
        from services.progress.dsm.registry import dsm_registry
        self.instruments = list(registry().instruments.instruments) + list(dsm_registry().instruments)
        self.extra = {i.key for i in dsm_registry().instruments}
        self.meta = dsm_registry().instrument_meta

    def get(self, key: str):
        for instrument in self.instruments:
            if instrument.key == key:
                return instrument
        raise KeyError(key)

    def tag(self, key: str) -> str:
        from services.progress.dsm.registry import dsm_registry
        return dsm_registry().tag if key in self.extra else registry().tag("instruments")


def instruments() -> _Instruments:
    return _Instruments()


def error(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message})


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def loads(value, default):
    return json.loads(value) if value else default


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------------------------------------------------------- lookups & guards

def patient_or_404(db: DbSession, patient_id: str) -> PatientRecord:
    row = db.get(PatientRecord, patient_id)
    if row is None:
        raise error(404, "PATIENT_NOT_FOUND", "Patient was not found.")
    return row


def session_or_404(db: DbSession, session_id: str, patient_id: str | None = None) -> SessionRecord:
    row = db.get(SessionRecord, session_id)
    if row is None or (patient_id is not None and row.patient_id != patient_id):
        raise error(404, "SESSION_NOT_FOUND", "Session was not found for this patient.")
    if row.patient_id is None:
        raise error(409, "SESSION_UNASSIGNED", "Link this session to a patient before recording progress.")
    return row


def patient_sessions(db: DbSession, patient_id: str) -> list[SessionRecord]:
    return list(db.scalars(select(SessionRecord).where(SessionRecord.patient_id == patient_id)
                           .order_by(SessionRecord.created_at, SessionRecord.id)))


def note_status(db: DbSession, session_id: str) -> str:
    status = db.scalar(select(NoteDraftRecord.workflow_status).where(NoteDraftRecord.session_id == session_id))
    return status or "DRAFT"


def is_locked(db: DbSession, session_id: str | None) -> bool:
    return session_id is not None and note_status(db, session_id) != "DRAFT"


def ensure_unlocked(db: DbSession, session_id: str | None) -> None:
    if is_locked(db, session_id):
        raise error(409, "NOTE_TERMINAL", "This session's note is decided; its observations, scores and risk statements are read-only.")


def ensure_registry_versions(db: DbSession) -> None:
    for version in registry().versions.values():
        exists = db.scalar(select(RegistryVersionRecord.id).where(
            RegistryVersionRecord.kind == version.kind, RegistryVersionRecord.version == version.version,
            RegistryVersionRecord.sha256 == version.sha256))
        if not exists:
            db.add(RegistryVersionRecord(id=new_id("reg"), kind=version.kind, version=version.version,
                                         sha256=version.sha256, created_at=utc_now()))
    db.flush()


def audit(db: DbSession, *, patient_id: str, session_id: str | None, event_type: str, actor: str, metadata: dict) -> None:
    db.add(AuditEventRecord(id=f"audit-{uuid.uuid4().hex[:12]}", session_id=session_id, patient_id=patient_id,
                            note_draft_id=None, event_type=event_type, actor_name=actor, occurred_at=utc_now(),
                            metadata_json=json.dumps({k: v if isinstance(v, str) else dumps(v) for k, v in metadata.items()})))


# ---------------------------------------------------------------- evidence

def resolve_evidence(db: DbSession, patient_id: str, segment_ids: list[str], *, own_session: str | None = None,
                     basis: dict[str, str] | None = None, strict: bool = True) -> list[dict]:
    """Resolve ids to cited evidence; every id must belong to the patient (and own session if given)."""
    ids = list(dict.fromkeys(segment_ids))
    if not ids:
        return []
    rows = db.execute(select(TranscriptSegmentRecord, SessionRecord.patient_id, SegmentSourceRecord.recording_id)
                      .join(SessionRecord, SessionRecord.id == TranscriptSegmentRecord.session_id)
                      .outerjoin(SegmentSourceRecord, SegmentSourceRecord.segment_id == TranscriptSegmentRecord.id)
                      .where(TranscriptSegmentRecord.id.in_(ids))).all()
    found = {seg.id: (seg, owner, rec) for seg, owner, rec in rows}
    out = []
    for sid in ids:
        item = found.get(sid)
        valid = item is not None and item[1] == patient_id and (own_session is None or item[0].session_id == own_session)
        if not valid:
            if strict:
                raise error(422, "INVALID_EVIDENCE_REFERENCE",
                            "Every evidence ID must be a transcript segment of this patient" + (" in this session." if own_session else "."))
            continue
        seg, _, recording = item
        out.append(CitedEvidence(segment_id=seg.id, session_id=seg.session_id, start_ms=seg.start_ms, end_ms=seg.end_ms,
                                 speaker_role=seg.speaker_role, basis=(basis or {}).get(seg.id, "")[:300], text=seg.text,
                                 recording_id=recording, revision=seg.revision).model_dump(mode="json"))
    return out


def current_revisions(db: DbSession, segment_ids: list[str]) -> dict[str, int]:
    if not segment_ids:
        return {}
    return dict(db.execute(select(TranscriptSegmentRecord.id, TranscriptSegmentRecord.revision)
                           .where(TranscriptSegmentRecord.id.in_(set(segment_ids)))).all())


def write_links(db: DbSession, owner_kind: str, owner_id: str, segment_ids: list[str]) -> dict[str, int]:
    db.execute(delete(ProgressEvidenceLinkRecord).where(ProgressEvidenceLinkRecord.owner_kind == owner_kind,
                                                        ProgressEvidenceLinkRecord.owner_id == owner_id))
    revisions = current_revisions(db, segment_ids)
    for sid, rev in revisions.items():
        db.add(ProgressEvidenceLinkRecord(segment_id=sid, owner_kind=owner_kind, owner_id=owner_id, revision_at_link=rev))
    return revisions


def segments_of(db: DbSession, rec) -> list[str]:
    """All segment ids a record cites (used for evidence links and staleness)."""
    if isinstance(rec, DomainObservationRecord):
        return loads(rec.evidence_segment_ids_json, [])
    if isinstance(rec, InstrumentScoreRecord):
        return [s for item in loads(rec.item_scores_json, []) for s in item.get("evidence_segment_ids", [])]
    if isinstance(rec, RiskStatementRecord):
        return loads(rec.segment_ids_json, [])
    if isinstance(rec, PlanFollowupRecord):
        return loads(rec.evidence_segment_ids_json, [])
    if isinstance(rec, DsmFindingRecord):
        from services.progress.dsm.store import finding_segments
        return finding_segments(rec)
    if isinstance(rec, DsmPredictionRecord):
        from services.progress.dsm.store import prediction_segments
        return prediction_segments(rec)
    if isinstance(rec, ConditionPredictionRecord):
        ids = [sid for sentence in loads(rec.rationale_json, []) for sid in sentence.get("segment_ids", [])]
        for finding in db.scalars(select(CriterionFindingRecord).where(CriterionFindingRecord.prediction_id == rec.id)):
            ids += [e["segment_id"] for e in loads(finding.evidence_json, []) + loads(finding.counter_evidence_json, [])]
        return list(dict.fromkeys(ids))
    return []


# ---------------------------------------------------------------- serialization

def _review(db: DbSession, rec, session_id: str | None) -> dict:
    return {"provenance": rec.provenance, "review_status": rec.review_status, "reviewer_name": rec.reviewer_name,
            "reviewed_at": rec.reviewed_at, "reviewer_comment": rec.reviewer_comment,
            "original_ai": loads(rec.original_ai_json, None), "extraction_job_id": rec.extraction_job_id,
            "locked": is_locked(db, session_id), "created_at": rec.created_at, "updated_at": rec.updated_at}


def domain_snapshot(rec: DomainObservationRecord) -> dict:
    return {k: getattr(rec, k) for k in ("rating", "status_value", "direction", "timeframe", "frequency_text", "intensity_text",
                                         "duration_text", "basis", "provenance", "review_status", "reviewer_name",
                                         "reviewer_comment", "extraction_job_id")} | {
        "evidence_segment_ids": loads(rec.evidence_segment_ids_json, []),
        "reviewed_at": rec.reviewed_at.isoformat() if rec.reviewed_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None}


def to_domain(db: DbSession, rec: DomainObservationRecord, *, details: bool = True) -> DomainObservation:
    versions = list(db.scalars(select(DomainObservationVersionRecord).where(DomainObservationVersionRecord.observation_id == rec.id)
                               .order_by(DomainObservationVersionRecord.version_no))) if details else []
    basis = {sid: rec.basis for sid in loads(rec.evidence_segment_ids_json, [])}
    return DomainObservation(
        id=rec.id, patient_id=rec.patient_id, session_id=rec.session_id, domain_key=rec.domain_key,
        registry_version=rec.registry_version, status_value=rec.status_value, rating=rec.rating, direction=rec.direction,
        timeframe=rec.timeframe, frequency_text=rec.frequency_text, intensity_text=rec.intensity_text,
        duration_text=rec.duration_text, basis=rec.basis,
        evidence=resolve_evidence(db, rec.patient_id, loads(rec.evidence_segment_ids_json, []), strict=False, basis=basis),
        alternatives=[{"id": v.id, "created_at": v.created_at, "actor": v.actor, **loads(v.snapshot_json, {})} for v in versions if v.role == "alternative"],
        history=[{"id": v.id, "version_no": v.version_no, "created_at": v.created_at, "actor": v.actor, **loads(v.snapshot_json, {})} for v in versions if v.role == "history"],
        **_review(db, rec, rec.session_id))


def score_totals(instrument, items: dict[str, int | None]) -> tuple[int | None, bool, dict]:
    values = [items.get(i.id) for i in instrument.items]
    present = [v for v in values if v is not None]
    total = sum(present) if present else None
    subtotals = {}
    for sub in instrument.subtotals:
        part = [items.get(i) for i in sub.items]
        got = [v for v in part if v is not None]
        sub_total = sum(got) if got else None
        subtotals[sub.key] = {"total": sub_total, "partial": len(got) != len(part), "band": instrument.band(sub_total, sub.key) if len(got) == len(part) else None}
    return total, len(present) != len(values), subtotals


def to_score(db: DbSession, rec: InstrumentScoreRecord) -> InstrumentScore:
    instrument = instruments().get(rec.instrument_key)
    items = [InstrumentItemScore.model_validate(i) for i in loads(rec.item_scores_json, [])]
    ids = [s for i in items for s in i.evidence_segment_ids]
    basis = {s: i.basis for i in items for s in i.evidence_segment_ids}
    return InstrumentScore(
        id=rec.id, patient_id=rec.patient_id, session_id=rec.session_id, instrument_key=rec.instrument_key,
        registry_version=rec.registry_version, administered_at=rec.administered_at, items=items, total=rec.total,
        total_is_partial=rec.total_is_partial, subtotals=loads(rec.subtotals_json, {}),
        band=None if rec.total_is_partial else instrument.band(rec.total),
        evidence=resolve_evidence(db, rec.patient_id, ids, strict=False, basis=basis), **_review(db, rec, rec.session_id))


def to_prediction(db: DbSession, rec: ConditionPredictionRecord, *, details: bool = True) -> ConditionPrediction:
    condition = registry().conditions.get(rec.condition_key)
    findings = []
    if details:
        for f in db.scalars(select(CriterionFindingRecord).where(CriterionFindingRecord.prediction_id == rec.id)):
            def cite(items):
                ids = [e["segment_id"] for e in items]
                resolved = {e["segment_id"]: e for e in resolve_evidence(db, rec.patient_id, ids, strict=False,
                                                                            basis={e["segment_id"]: e.get("basis", "") for e in items})}
                return [resolved[i] for i in ids if i in resolved]
            try:
                summary = condition.criterion(f.criterion_id).summary
            except StopIteration:
                summary = ""
            findings.append(CriterionFinding(id=f.id, criterion_id=f.criterion_id, summary=summary, status=f.status,
                                             evidence=cite(loads(f.evidence_json, [])), counter_evidence=cite(loads(f.counter_evidence_json, [])),
                                             timeframe=f.timeframe, duration_note=f.duration_note))
    probability, note = rec.probability, None
    if probability is not None and not rec.patient_id.startswith(DEMO_PATIENT_PREFIX):
        probability, note = None, RESEARCH_ONLY_NOTE
    elif rec.condition_key != "depressive_episode" or probability is None:
        note = "Not calibrated; label only."
    return ConditionPrediction(
        id=rec.id, patient_id=rec.patient_id, as_of_session_id=rec.as_of_session_id, condition_key=rec.condition_key,
        condition_label=condition.label, subtype_key=rec.subtype_key, prediction=rec.prediction, probability=probability,
        probability_note=note, criteria_met_count=rec.criteria_met_count, criteria_required=rec.criteria_required,
        duration_status=rec.duration_status, impairment_status=rec.impairment_status,
        rule_outs=loads(rec.rule_outs_json, []), differentials=loads(rec.differentials_json, []),
        missing_information=loads(rec.missing_information_json, []), rationale=loads(rec.rationale_json, []),
        postprocess=loads(rec.postprocess_json, {}), findings=findings, model_version=rec.model_version,
        prompt_version=rec.prompt_version, schema_version=rec.schema_version, registry_version=rec.registry_version,
        **_review(db, rec, None))


def to_risk(db: DbSession, rec: RiskStatementRecord) -> RiskStatement:
    ids = loads(rec.segment_ids_json, [])
    return RiskStatement(id=rec.id, patient_id=rec.patient_id, session_id=rec.session_id, category=rec.category, basis=rec.basis,
                         categories=loads(rec.categories_json, []) or [rec.category], note_kind=rec.note_kind or "flag",
                         registry_kind=rec.registry_kind or "v1",
                         evidence=resolve_evidence(db, rec.patient_id, ids, strict=False, basis={i: rec.basis for i in ids}),
                         **_review(db, rec, rec.session_id))


def to_plan(db: DbSession, rec: PlanFollowupRecord) -> PlanFollowup:
    ids = loads(rec.evidence_segment_ids_json, [])
    return PlanFollowup(id=rec.id, patient_id=rec.patient_id, origin_session_id=rec.origin_session_id,
                        origin_note_item_id=rec.origin_note_item_id, plan_text=rec.plan_text,
                        followup_session_id=rec.followup_session_id, status=rec.status, basis=rec.basis,
                        evidence=resolve_evidence(db, rec.patient_id, ids, strict=False, basis={i: rec.basis for i in ids}),
                        **_review(db, rec, rec.followup_session_id))


# ---------------------------------------------------------------- domain observations

def add_version(db: DbSession, rec: DomainObservationRecord, role: str, actor: str, snapshot: dict | None = None) -> None:
    count = len(db.scalars(select(DomainObservationVersionRecord.id).where(DomainObservationVersionRecord.observation_id == rec.id)).all())
    db.add(DomainObservationVersionRecord(id=new_id("dobsv"), observation_id=rec.id, session_id=rec.session_id,
                                          domain_key=rec.domain_key, version_no=count + 1, role=role,
                                          snapshot_json=dumps(snapshot or domain_snapshot(rec)), actor=actor, created_at=utc_now()))


def validate_domain_value(domain_key: str, value: DomainObservationInput) -> None:
    try:
        domain = registry().domains.get(domain_key)
    except KeyError:
        raise error(404, "DOMAIN_NOT_FOUND", "Unknown domain.") from None
    if value.direction is not None and value.direction not in domain.directions:
        raise error(422, "INVALID_DIRECTION", f"Allowed directions for {domain_key}: {', '.join(domain.directions) or 'none'}.")


def put_manual_domain(db: DbSession, session: SessionRecord, domain_key: str, value: DomainObservationInput) -> DomainObservationRecord:
    validate_domain_value(domain_key, value)
    ensure_unlocked(db, session.id)
    resolve_evidence(db, session.patient_id, value.evidence_segment_ids, own_session=session.id)
    rec = db.scalar(select(DomainObservationRecord).where(DomainObservationRecord.session_id == session.id,
                                                          DomainObservationRecord.domain_key == domain_key))
    now = utc_now()
    if rec is None:
        rec = DomainObservationRecord(id=new_id("dobs"), patient_id=session.patient_id, session_id=session.id,
                                      domain_key=domain_key, created_at=now, original_ai_json=None)
        db.add(rec)
    else:
        check_expected(rec, value.expected_updated_at)
        add_version(db, rec, "history", value.actor)
    rec.registry_version = registry().tag("domains")
    for field in ("rating", "status_value", "direction", "timeframe", "frequency_text", "intensity_text", "duration_text", "basis"):
        setattr(rec, field, getattr(value, field))
    rec.evidence_segment_ids_json = dumps(list(dict.fromkeys(value.evidence_segment_ids)))
    rec.provenance, rec.review_status = "MANUAL", "accepted"
    rec.reviewer_name, rec.reviewed_at, rec.reviewer_comment, rec.stale_from_status = value.actor, now, None, None
    rec.extraction_job_id, rec.updated_at = None, now
    db.flush()
    rec.source_revisions_json = dumps(write_links(db, "domain_observation", rec.id, value.evidence_segment_ids))
    audit(db, patient_id=session.patient_id, session_id=session.id, event_type="PROGRESS_DOMAIN_ENTERED", actor=value.actor,
          metadata={"observation_id": rec.id, "domain_key": domain_key, "status_value": value.status_value,
                    "rating": "" if value.rating is None else str(value.rating)})
    return rec


# ---------------------------------------------------------------- instrument scores

def build_items(instrument, items: dict[str, int | None], *, ai: bool) -> list[dict]:
    known = {i.id for i in instrument.items}
    if set(items) - known:
        raise error(422, "UNKNOWN_ITEM", f"Unknown {instrument.label} items: {sorted(set(items) - known)}")
    out = []
    for item in instrument.items:
        score = items.get(item.id)
        if ai and item.id not in instrument.ai_estimable_items and score is not None:
            raise error(422, "CLINICIAN_ONLY_ITEM", f"{instrument.label} item {item.id} is clinician-entered only.")
        if score is not None and not instrument.item_min <= score <= instrument.item_max:
            raise error(422, "INVALID_ITEM_SCORE", f"{instrument.label} items are scored {instrument.item_min}-{instrument.item_max}.")
        out.append({"item_id": item.id, "score": score, "evidence_segment_ids": [], "basis": "",
                    "status": "rated" if score is not None else "not_administered"})
    return out


def apply_score_items(rec: InstrumentScoreRecord, instrument, item_rows: list[dict]) -> None:
    rec.item_scores_json = dumps(item_rows)
    total, partial, subtotals = score_totals(instrument, {i["item_id"]: i["score"] for i in item_rows})
    rec.total, rec.total_is_partial, rec.subtotals_json = total, partial, dumps(subtotals)


def add_manual_score(db: DbSession, patient: PatientRecord, value: InstrumentScoreInput) -> InstrumentScoreRecord:
    try:
        instrument = instruments().get(value.instrument_key)
    except KeyError:
        raise error(404, "INSTRUMENT_NOT_FOUND", "Unknown instrument.") from None
    if value.session_id:
        session_or_404(db, value.session_id, patient.id)
        ensure_unlocked(db, value.session_id)
    now = utc_now()
    rec = InstrumentScoreRecord(id=new_id("iscore"), patient_id=patient.id, session_id=value.session_id,
                                instrument_key=instrument.key, registry_version=instruments().tag(instrument.key),
                                administered_at=datetime.combine(value.administered_at, time(12), tzinfo=timezone.utc) if value.administered_at else None,
                                provenance="MANUAL", review_status="accepted", reviewer_name=value.actor, reviewed_at=now,
                                is_active=True, source_revisions_json="{}", created_at=now, updated_at=now)
    apply_score_items(rec, instrument, build_items(instrument, value.items, ai=False))
    if value.session_id:
        previous = db.scalar(select(InstrumentScoreRecord).where(
            InstrumentScoreRecord.session_id == value.session_id, InstrumentScoreRecord.instrument_key == instrument.key,
            InstrumentScoreRecord.provenance == "MANUAL", InstrumentScoreRecord.is_active.is_(True)))
        if previous:
            previous.is_active, previous.superseded_by_id, previous.updated_at = False, rec.id, now
            db.flush()
    db.add(rec)
    db.flush()
    audit(db, patient_id=patient.id, session_id=value.session_id, event_type="PROGRESS_INSTRUMENT_ADMINISTERED", actor=value.actor,
          metadata={"score_id": rec.id, "instrument_key": instrument.key, "total": "" if rec.total is None else str(rec.total),
                    "partial": str(rec.total_is_partial)})
    return rec


# ---------------------------------------------------------------- review state machine

def check_expected(rec, expected: datetime | None) -> None:
    if expected is not None and rec.updated_at.replace(tzinfo=None) != expected.replace(tzinfo=None):
        raise error(409, "PROGRESS_VALUE_CHANGED", "This value changed; reload before reviewing.")


def review(db: DbSession, rec, payload: ProgressReview, *, kind: str, patient_id: str, session_id: str | None,
           apply_edit: Callable[[object, dict], None]) -> None:
    """accept (pending/stale), edit (any non-rejected), reject (any non-rejected, reason required).

    original_ai_json is never modified. Edited values replace the active value.
    """
    check_expected(rec, payload.expected_updated_at)
    if rec.provenance != "AI_GENERATED":
        raise error(409, "NOT_AI_GENERATED", "Only AI-generated values are reviewed; edit manual entries directly.")
    if rec.review_status == "rejected":
        raise error(409, "ALREADY_REJECTED", "This value was rejected and is kept in history only.")
    if payload.action == "accept" and rec.review_status not in {"pending", "stale"}:
        raise error(409, "ALREADY_REVIEWED", "This value has already been reviewed.")
    before = rec.review_status
    if isinstance(rec, DomainObservationRecord):
        add_version(db, rec, "history", payload.actor)
    if isinstance(rec, DsmFindingRecord):
        from services.progress.dsm.store import add_version as add_dsm_version
        add_dsm_version(db, rec, "history", payload.actor)
    if payload.action == "edit":
        apply_edit(rec, payload.value or {})
        rec.review_status = "edited"
    elif payload.action == "accept":
        rec.review_status = "accepted"
    else:
        rec.review_status = "rejected"  # kept in history; trends exclude it by status
    now = utc_now()
    rec.reviewer_name, rec.reviewed_at = payload.actor, now
    rec.reviewer_comment = payload.reason if payload.action == "reject" else payload.comment
    if before == "stale" or payload.action == "edit":
        # Re-review against current source text acknowledges the current revisions.
        rec.source_revisions_json = dumps(write_links(db, kind, rec.id, segments_of(db, rec)))
        rec.stale_from_status = None
    rec.updated_at = now
    db.flush()
    past = {"accept": "ACCEPTED", "edit": "EDITED", "reject": "REJECTED"}[payload.action]
    audit(db, patient_id=patient_id, session_id=session_id, event_type=f"PROGRESS_{kind.upper()}_{past}",
          actor=payload.actor, metadata={"record_id": rec.id, "action": payload.action, "previous_status": before,
                                         "comment": payload.comment or "", "reason": payload.reason or "",
                                         "value": payload.value or {}})


def edit_domain(rec: DomainObservationRecord, value: dict) -> None:
    current = domain_snapshot(rec)
    merged = {k: value.get(k, current.get(k)) for k in ("status_value", "rating", "direction", "timeframe", "frequency_text",
                                                         "intensity_text", "duration_text", "basis")}
    merged["evidence_segment_ids"] = value.get("evidence_segment_ids", current["evidence_segment_ids"])
    try:
        parsed = DomainObservationInput(actor="review", **merged)
    except ValueError as exc:
        raise error(422, "INVALID_REVIEW_VALUE", str(exc)) from None
    validate_domain_value(rec.domain_key, parsed)
    for field in ("rating", "status_value", "direction", "timeframe", "frequency_text", "intensity_text", "duration_text", "basis"):
        setattr(rec, field, getattr(parsed, field))
    rec.evidence_segment_ids_json = dumps(parsed.evidence_segment_ids)


def make_score_editor(db: DbSession):
    def edit(rec: InstrumentScoreRecord, value: dict) -> None:
        instrument = instruments().get(rec.instrument_key)
        incoming = value.get("items")
        if not isinstance(incoming, dict):
            raise error(422, "INVALID_REVIEW_VALUE", "Provide items as {item_id: score|null}.")
        rows = loads(rec.item_scores_json, [])
        by_id = {r["item_id"]: r for r in rows}
        for item_id, score in incoming.items():
            if item_id not in by_id:
                raise error(422, "UNKNOWN_ITEM", f"Unknown item {item_id}.")
            if item_id not in instrument.ai_estimable_items and score is not None:
                raise error(422, "CLINICIAN_ONLY_ITEM", "Clinician-only items cannot be added to an AI estimate; enter an administration instead.")
            if score is not None and (not isinstance(score, int) or not instrument.item_min <= score <= instrument.item_max):
                raise error(422, "INVALID_REVIEW_VALUE", f"Item scores must be integers {instrument.item_min}-{instrument.item_max}.")
            by_id[item_id].update(score=score, status="rated" if score is not None else "insufficient_evidence")
        apply_score_items(rec, instrument, rows)
    return edit


def edit_prediction(rec: ConditionPredictionRecord, value: dict) -> None:
    label = value.get("prediction")
    if label not in {"likely", "possible", "unlikely", "insufficient_evidence"}:
        raise error(422, "INVALID_REVIEW_VALUE", "prediction must be likely, possible, unlikely or insufficient_evidence.")
    rec.prediction = label
    if "subtype_key" in value:
        subtypes = {s.key for s in registry().conditions.get(rec.condition_key).subtypes}
        if value["subtype_key"] is not None and value["subtype_key"] not in subtypes:
            raise error(422, "INVALID_REVIEW_VALUE", "Unknown subtype for this condition.")
        rec.subtype_key = value["subtype_key"]


def edit_risk(rec: RiskStatementRecord, value: dict) -> None:
    category = value.get("category", rec.category)
    if category not in RISK_CATEGORIES:
        raise error(422, "INVALID_REVIEW_VALUE", "Unknown risk category.")
    rec.category = category
    if rec.categories_json:
        rest = [c for c in loads(rec.categories_json, []) if c != category]
        rec.categories_json = dumps([category] + rest)
    rec.basis = str(value.get("basis", rec.basis))[:600]


def edit_plan(rec: PlanFollowupRecord, value: dict) -> None:
    status = value.get("status")
    if status not in {"not_reviewed", "discussed_done", "discussed_partial", "discussed_not_done", "not_discussed"}:
        raise error(422, "INVALID_REVIEW_VALUE", "Unknown follow-up status.")
    rec.status = status
    rec.basis = str(value.get("basis", rec.basis))[:600]


# ---------------------------------------------------------------- dashboard

def _date(rec_session: SessionRecord) -> datetime:
    value = rec_session.created_at
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _point(slot, rec, *, value=None, label=None, probability=None, status=None, meta=None) -> trends.Point:
    return trends.Point(slot=slot, value=value, label=label, probability=probability, status=status,
                        provenance=rec.provenance if rec else None, review_status=rec.review_status if rec else None,
                        record_id=rec.id if rec else None, meta=meta or {})


def _point_json(p: trends.Point, active: trends.Point) -> dict:
    return {"slot": p.slot, "value": active.value, "raw_value": p.value, "label": active.label, "probability": active.probability,
            "status": p.status, "provenance": p.provenance, "review_status": p.review_status, "record_id": p.record_id,
            "gap_reason": active.gap_reason, **p.meta}


def _changes_json(kind: str, key: str, series: str, changes: list[trends.Change], slot_session: dict[str, str]) -> list[dict]:
    return [{"measure_kind": kind, "measure_key": key, "series": series, "rule_id": c.rule_id,
             "from_session_id": slot_session.get(c.from_slot, c.from_slot), "to_session_id": slot_session.get(c.to_slot, c.to_slot),
             "delta": c.delta, "from_label": c.from_label, "to_label": c.to_label, "provenance_of_inputs": c.provenance_of_inputs}
            for c in changes]


def sync_change_events(db: DbSession, patient_id: str, computed: list[dict]) -> list[ChangeEvent]:
    out = []
    for change in computed:
        key = hashlib.sha256(dumps([patient_id, change["measure_kind"], change["measure_key"], change["series"], change["rule_id"],
                                    change["from_session_id"], change["to_session_id"]]).encode()).hexdigest()
        row = db.scalar(select(ChangeEventRecord).where(ChangeEventRecord.dedupe_key == key))
        if row is None:
            row = ChangeEventRecord(id=new_id("chg"), patient_id=patient_id, dedupe_key=key, created_at=utc_now(), **change)
            db.add(row)
        else:
            # Values can be edited after detection; keep the latest computed delta/provenance.
            row.delta, row.from_label, row.to_label, row.provenance_of_inputs = change["delta"], change["from_label"], change["to_label"], change["provenance_of_inputs"]
        out.append(row)
    db.flush()
    return [ChangeEvent.model_validate(r, from_attributes=True) for r in out]


def build_progress(db: DbSession, patient_id: str) -> PatientProgress:
    reg = registry()
    patient_or_404(db, patient_id)
    sessions = patient_sessions(db, patient_id)
    statuses = {s.id: note_status(db, s.id) for s in sessions}
    session_rows = [{"id": s.id, "index": i + 1, "created_at": _date(s), "session_type": s.session_type,
                     "note_status": statuses[s.id], "research_demo": bool(loads(s.consent_json, {}).get("research_demo"))}
                    for i, s in enumerate(sessions)]
    slot_session = {s.id: s.id for s in sessions}
    computed_changes: list[dict] = []

    # Instruments: administered and AI-estimate series, plotted on the chart metric (PHQ-8 subtotal for PHQ-9).
    scores = list(db.scalars(select(InstrumentScoreRecord).where(InstrumentScoreRecord.patient_id == patient_id,
                                                                 InstrumentScoreRecord.is_active.is_(True))))
    instruments = []
    catalogue = _Instruments()
    for instrument in catalogue.instruments:
        metric = instrument.chart_subtotal
        series_out = []
        for series, provenance in (("administered", "MANUAL"), ("ai_estimate", "AI_GENERATED")):
            rows = [r for r in scores if r.instrument_key == instrument.key and r.provenance == provenance]
            by_session = {r.session_id: r for r in rows if r.session_id}
            slots: list[tuple[datetime, str, InstrumentScoreRecord | None]] = [(_date(s), s.id, by_session.get(s.id)) for s in sessions]
            for r in rows:
                if r.session_id is None and r.administered_at:
                    stamp = r.administered_at if r.administered_at.tzinfo else r.administered_at.replace(tzinfo=timezone.utc)
                    slots.append((stamp, f"date:{stamp.date().isoformat()}:{r.id}", r))
            slots.sort(key=lambda x: (x[0], x[1]))
            points = []
            for stamp, slot, r in slots:
                if r is None:
                    points.append(_point(slot, None, meta={"date": stamp.isoformat()}))
                    continue
                subtotals = loads(r.subtotals_json, {})
                value, partial = (subtotals[metric]["total"], subtotals[metric]["partial"]) if metric else (r.total, r.total_is_partial)
                points.append(_point(slot, r, value=value, meta={"date": stamp.isoformat(), "partial": partial, "total": r.total,
                                                                 "total_is_partial": r.total_is_partial, "session_id": r.session_id,
                                                                 "reviewer_name": r.reviewer_name,
                                                                 "items": {i["item_id"]: i["score"] for i in loads(r.item_scores_json, [])}}))
            active = trends.activate(points)
            threshold = instrument.reliable_change_threshold
            changes = trends.instrument_changes(points, threshold)
            measure = f"{instrument.key}:{metric}" if metric else instrument.key
            computed_changes += _changes_json("instrument", measure, series, changes, slot_session)
            series_out.append({"series": series, "points": [_point_json(p, a) for p, a in zip(points, active)],
                               "summary": trends.summary(points), "trajectory": trends.series_trajectory(points, threshold),
                               "changes": len(changes)})
        instruments.append({"instrument_key": instrument.key, "label": instrument.label, "metric": metric,
                            "metric_label": next((s.label for s in instrument.subtotals if s.key == metric), instrument.label),
                            "metric_max": next((s.max for s in instrument.subtotals if s.key == metric), instrument.total_max),
                            "bands": [b.model_dump() for b in next((s.bands for s in instrument.subtotals if s.key == metric), instrument.bands)],
                            "reliable_change_threshold": instrument.reliable_change_threshold, "series": series_out,
                            "items": [i.model_dump() for i in instrument.items], "item_min": instrument.item_min, "item_max": instrument.item_max,
                            "clinician_only": instrument.key in catalogue.extra, "reporting_period": instrument.reporting_period,
                            "licence_note": instrument.licence_note if instrument.key in catalogue.extra else "",
                            "followup_domains": catalogue.meta.get("followup_domains", {}).get(instrument.key, [])})

    # Domains.
    observations = list(db.scalars(select(DomainObservationRecord).where(DomainObservationRecord.patient_id == patient_id,
                                                                         DomainObservationRecord.retired_batch_id.is_(None))))
    by_cell = {(o.session_id, o.domain_key): o for o in observations}
    domains = []
    for domain in reg.domains.domains:
        points = []
        for s in sessions:
            o = by_cell.get((s.id, domain.key))
            if o is None:
                points.append(_point(s.id, None))
                continue
            plotted = o.rating if o.status_value == "rated" and o.timeframe != "past" else None
            points.append(_point(s.id, o, value=plotted, status=o.status_value,
                                 meta={"rating": o.rating, "timeframe": o.timeframe, "direction": o.direction,
                                       "frequency_text": o.frequency_text, "intensity_text": o.intensity_text,
                                       "duration_text": o.duration_text, "reviewer_name": o.reviewer_name}))
        changes = trends.domain_changes(points)
        computed_changes += _changes_json("domain", domain.key, "domain", changes, slot_session)
        active = trends.activate(points)
        domains.append({"domain_key": domain.key, "label": domain.label, "group": domain.group, "directions": domain.directions,
                        "cells": [_point_json(p, a) for p, a in zip(points, active)], "summary": trends.summary(points),
                        "changes": len(changes)})

    # Predictions.
    preds = list(db.scalars(select(ConditionPredictionRecord).where(ConditionPredictionRecord.patient_id == patient_id,
                                                                    ConditionPredictionRecord.is_active.is_(True))))
    order = {s.id: i for i, s in enumerate(sessions)}
    timeline, latest = [], []
    for condition in reg.conditions.enabled:
        rows = {p.as_of_session_id: p for p in preds if p.condition_key == condition.key}
        points = []
        for s in sessions:
            p = rows.get(s.id)
            if p is None:
                points.append(_point(s.id, None))
            else:
                shown = to_prediction(db, p, details=False)
                points.append(_point(s.id, p, label=f"{p.prediction}:{p.subtype_key or ''}", probability=shown.probability,
                                     meta={"prediction": p.prediction, "subtype_key": p.subtype_key,
                                           "criteria_met_count": p.criteria_met_count, "criteria_required": p.criteria_required,
                                           "reviewer_name": p.reviewer_name}))
        changes = trends.prediction_changes(points)
        computed_changes += _changes_json("prediction", condition.key, "prediction", changes, slot_session)
        active = trends.activate(points)
        timeline.append({"condition_key": condition.key, "label": condition.label,
                         "points": [_point_json(p, a) for p, a in zip(points, active)]})
        candidates = [p for p in rows.values() if p.review_status != "rejected"] or list(rows.values())
        if candidates:
            latest.append(to_prediction(db, max(candidates, key=lambda p: order.get(p.as_of_session_id, -1))))

    from services.progress.dsm.registry import registry_kind
    from services.progress.dsm.store import build_dsm_block
    dsm_block, dsm_changes = build_dsm_block(db, patient_id, sessions)
    computed_changes += dsm_changes
    events = sync_change_events(db, patient_id, computed_changes)
    risks = list(db.scalars(select(RiskStatementRecord).where(RiskStatementRecord.patient_id == patient_id,
                                                              RiskStatementRecord.is_active.is_(True)).order_by(RiskStatementRecord.created_at)))
    plans = list(db.scalars(select(PlanFollowupRecord).where(PlanFollowupRecord.patient_id == patient_id)
                            .order_by(PlanFollowupRecord.created_at)))
    pending = {
        "domain_observations": sum(o.review_status in {"pending", "stale"} for o in observations),
        "instrument_scores": sum(r.review_status in {"pending", "stale"} for r in scores),
        "predictions": sum(p.review_status in {"pending", "stale"} for p in preds),
        "risk_statements": sum(r.review_status in {"pending", "stale"} for r in risks),
        "plan_followups": sum(p.review_status in {"pending", "stale"} for p in plans),
    }
    pending.update(dsm_block["pending"])
    pending["total"] = sum(pending.values())
    db.commit()
    return PatientProgress(
        patient_id=patient_id, sessions=session_rows, versions={k: v.tag for k, v in reg.versions.items()},
        pending_review_counts=pending, instruments=instruments, domains=domains,
        domain_groups=[g.model_dump() for g in reg.domains.groups], change_events=events, predictions=latest,
        prediction_timeline=timeline, risk_statements=[to_risk(db, r) for r in risks],
        plan_followups=[to_plan(db, p) for p in plans], instrument_scores=[to_score(db, r) for r in scores],
        registry_kind=registry_kind(), dsm=dsm_block)


# ---------------------------------------------------------------- Stage A persistence

PROTECTED = {"accepted", "edited"}


def previous_plans(db: DbSession, session: SessionRecord) -> tuple[str | None, list[dict]]:
    """Plan items from the patient's previous session note (generated plan items, else plan text lines)."""
    sessions = patient_sessions(db, session.patient_id)
    index = next((i for i, s in enumerate(sessions) if s.id == session.id), 0)
    if index == 0:
        return None, []
    origin = sessions[index - 1]
    note = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == origin.id))
    if note is None:
        return origin.id, []
    plans = []
    for field in loads(note.fields_json, []):
        if field.get("section") != "plan":
            continue
        items = [i for i in field.get("generated_items", []) if i.get("type") in {"proposed_plan", "agreed_plan"} and i.get("review_status") != "rejected"]
        if items:
            plans += [{"plan_id": f"P{n}", "note_item_id": i["id"], "text": (i.get("review_text") or i["text"])[:400]} for n, i in enumerate(items)]
        else:
            lines = [line.strip(" -•\t") for line in field.get("text", "").splitlines()]
            lines = [line for line in lines if line and not line.lower().startswith(("not documented", "no specific action plan"))]
            plans += [{"plan_id": f"P{n}", "note_item_id": f"plan-line-{n}", "text": line[:400]} for n, line in enumerate(lines)]
    return origin.id, plans[:8]


def persist_extraction(db: DbSession, session: SessionRecord, result: dict, *, job_id: str, origin_session_id: str | None = None,
                       plans: list[dict] | None = None) -> dict:
    """Save Stage A output as active AI values (pending review). Manual and reviewed values are never overwritten."""
    reg = registry()
    now = utc_now()
    counts = {"domains_saved": 0, "domains_alternative": 0, "scores_saved": 0, "scores_alternative": 0, "risk_saved": 0, "plans_saved": 0}
    ensure_unlocked(db, session.id)
    for key, item in result["domains"].items():
        rec = db.scalar(select(DomainObservationRecord).where(DomainObservationRecord.session_id == session.id,
                                                              DomainObservationRecord.domain_key == key))
        suggestion = {**item, "provenance": "AI_GENERATED", "review_status": "pending", "extraction_job_id": job_id}
        if rec is not None and (rec.provenance == "MANUAL" or rec.review_status in PROTECTED):
            add_version(db, rec, "alternative", "AI extraction", suggestion)
            counts["domains_alternative"] += 1
            continue
        if rec is None:
            rec = DomainObservationRecord(id=new_id("dobs"), patient_id=session.patient_id, session_id=session.id, domain_key=key, created_at=now)
            db.add(rec)
        else:
            add_version(db, rec, "history", "AI extraction")
        for field in ("rating", "status_value", "direction", "timeframe", "frequency_text", "intensity_text", "duration_text", "basis"):
            setattr(rec, field, item[field])
        rec.registry_version = reg.tag("domains")
        rec.evidence_segment_ids_json = dumps(item["evidence_segment_ids"])
        rec.provenance, rec.review_status, rec.original_ai_json = "AI_GENERATED", "pending", dumps(item)
        rec.reviewer_name = rec.reviewed_at = rec.reviewer_comment = rec.stale_from_status = None
        rec.extraction_job_id, rec.updated_at = job_id, now
        db.flush()
        rec.source_revisions_json = dumps(write_links(db, "domain_observation", rec.id, item["evidence_segment_ids"]))
        counts["domains_saved"] += 1
    for instrument_key, field in (("phq9", "phq_items"), ("gad7", "gad7_items")):
        given = result.get(field) or {}
        if not given:
            continue
        instrument = reg.instruments.get(instrument_key)
        rows = []
        for item in instrument.items:
            value = given.get(item.id)
            if item.id not in instrument.ai_estimable_items or value is None:
                rows.append({"item_id": item.id, "score": None, "evidence_segment_ids": [], "basis": "",
                             "status": "not_administered" if item.clinician_only else "insufficient_evidence"})
            else:
                rows.append({"item_id": item.id, **value})
        rec = InstrumentScoreRecord(id=new_id("iscore"), patient_id=session.patient_id, session_id=session.id, instrument_key=instrument_key,
                                    registry_version=reg.tag("instruments"), provenance="AI_GENERATED", review_status="pending",
                                    original_ai_json=dumps({"items": rows}), extraction_job_id=job_id, is_active=True,
                                    source_revisions_json="{}", created_at=now, updated_at=now)
        apply_score_items(rec, instrument, rows)
        previous = db.scalar(select(InstrumentScoreRecord).where(InstrumentScoreRecord.session_id == session.id,
            InstrumentScoreRecord.instrument_key == instrument_key, InstrumentScoreRecord.provenance == "AI_GENERATED",
            InstrumentScoreRecord.is_active.is_(True)))
        if previous is not None and previous.review_status in PROTECTED:
            rec.is_active = False  # kept as a non-active alternative suggestion
            counts["scores_alternative"] += 1
        else:
            if previous is not None:
                previous.is_active, previous.superseded_by_id, previous.updated_at = False, rec.id, now
                db.flush()
            counts["scores_saved"] += 1
        db.add(rec)
        db.flush()
        ids = [s for r in rows for s in r.get("evidence_segment_ids", [])]
        rec.source_revisions_json = dumps(write_links(db, "instrument_score", rec.id, ids))
    # Risk statements: replace unreviewed AI flags for this session; reviewed flags stay.
    existing = list(db.scalars(select(RiskStatementRecord).where(RiskStatementRecord.session_id == session.id,
                                                                 RiskStatementRecord.is_active.is_(True))))
    reviewed_keys = {(r.category, tuple(sorted(loads(r.segment_ids_json, [])))) for r in existing if r.review_status in PROTECTED | {"rejected"}}
    for r in existing:
        if r.provenance == "AI_GENERATED" and r.review_status in {"pending", "stale"}:
            r.is_active, r.updated_at = False, now
    for item in result.get("risk_statements", []):
        if (item["category"], tuple(sorted(item["segment_ids"]))) in reviewed_keys:
            continue
        rec = RiskStatementRecord(id=new_id("risk"), patient_id=session.patient_id, session_id=session.id, segment_ids_json=dumps(item["segment_ids"]),
                                  category=item["category"], basis=item["basis"], provenance="AI_GENERATED", review_status="pending",
                                  original_ai_json=dumps(item), extraction_job_id=job_id, is_active=True, source_revisions_json="{}",
                                  created_at=now, updated_at=now)
        db.add(rec)
        db.flush()
        rec.source_revisions_json = dumps(write_links(db, "risk_statement", rec.id, item["segment_ids"]))
        counts["risk_saved"] += 1
    by_plan = {p["plan_id"]: p for p in plans or []}
    for item in result.get("plan_followups", []):
        plan = by_plan.get(item["plan_id"])
        if plan is None or origin_session_id is None:
            continue
        rec = db.scalar(select(PlanFollowupRecord).where(PlanFollowupRecord.followup_session_id == session.id,
                                                         PlanFollowupRecord.origin_note_item_id == plan["note_item_id"]))
        if rec is not None and rec.review_status in PROTECTED:
            continue
        if rec is None:
            rec = PlanFollowupRecord(id=new_id("plan"), patient_id=session.patient_id, origin_session_id=origin_session_id,
                                     origin_note_item_id=plan["note_item_id"], followup_session_id=session.id, created_at=now)
            db.add(rec)
        rec.plan_text, rec.status, rec.basis = plan["text"], item["status"], item["basis"]
        rec.evidence_segment_ids_json = dumps(item["evidence_segment_ids"])
        rec.provenance, rec.review_status, rec.original_ai_json, rec.extraction_job_id = "AI_GENERATED", "pending", dumps(item), job_id
        rec.reviewer_name = rec.reviewed_at = rec.reviewer_comment = None
        rec.updated_at = now
        db.flush()
        rec.source_revisions_json = dumps(write_links(db, "plan_followup", rec.id, item["evidence_segment_ids"]))
        counts["plans_saved"] += 1
    audit(db, patient_id=session.patient_id, session_id=session.id, event_type="PROGRESS_EXTRACTION_SAVED", actor="AI progress extraction",
          metadata={"job_id": job_id, **{k: str(v) for k, v in counts.items()}, "version": result.get("version", ""),
                    "limitations": str(len(result.get("limitations", [])))})
    return counts


# ---------------------------------------------------------------- staleness

def mark_stale(db: DbSession, segment_id: str) -> int:
    """Called when a transcript segment's revision changes: dependants become 'stale' and leave active series."""
    changed = 0
    for link in db.scalars(select(ProgressEvidenceLinkRecord).where(ProgressEvidenceLinkRecord.segment_id == segment_id)):
        model = OWNER_MODELS.get(link.owner_kind)
        rec = db.get(model, link.owner_id) if model else None
        if rec is None or rec.review_status in {"stale", "rejected"}:
            continue
        if getattr(rec, "is_active", True) is False:
            continue
        rec.stale_from_status, rec.review_status, rec.updated_at = rec.review_status, "stale", utc_now()
        changed += 1
        patient = rec.patient_id
        session = getattr(rec, "session_id", None) or getattr(rec, "as_of_session_id", None) or getattr(rec, "followup_session_id", None)
        audit(db, patient_id=patient, session_id=session, event_type="PROGRESS_SOURCES_CHANGED", actor="system",
              metadata={"record_kind": link.owner_kind, "record_id": rec.id, "segment_id": segment_id})
    return changed


# ---------------------------------------------------------------- Stage B persistence

def calibrated_probability(db: DbSession, patient_id: str, as_of_session_id: str, condition_key: str) -> tuple[float | None, str | None]:
    """Depression only, and only for research demo patients (Q3 default). Null until a calibration mapping exists."""
    if condition_key != "depressive_episode" or not patient_id.startswith(DEMO_PATIENT_PREFIX):
        return None, None
    try:
        from services.progress.evaluation.calibration import load_active_mapping
    except ImportError:
        return None, None
    mapping = load_active_mapping()
    if mapping is None:
        return None, None
    score = db.scalar(select(InstrumentScoreRecord).where(InstrumentScoreRecord.session_id == as_of_session_id,
        InstrumentScoreRecord.instrument_key == "phq9", InstrumentScoreRecord.provenance == "AI_GENERATED",
        InstrumentScoreRecord.is_active.is_(True)))
    if score is None or score.review_status in {"rejected", "stale"}:
        return None, None
    phq8 = loads(score.subtotals_json, {}).get("phq8", {})
    if phq8.get("total") is None:
        return None, None
    return mapping.predict(phq8["total"]), mapping.id


def persist_prediction(db: DbSession, patient_id: str, result: dict, *, job_id: str) -> ConditionPredictionRecord:
    now = utc_now()
    probability, calibration_id = calibrated_probability(db, patient_id, result["as_of_session_id"], result["condition_key"])
    rec = ConditionPredictionRecord(
        id=new_id("pred"), patient_id=patient_id, as_of_session_id=result["as_of_session_id"], condition_key=result["condition_key"],
        subtype_key=result["subtype_key"], prediction=result["prediction"], probability=probability, calibration_id=calibration_id,
        criteria_met_count=result["criteria_met_count"], criteria_required=result["criteria_required"],
        duration_status=result["duration_status"], impairment_status=result["impairment_status"],
        rule_outs_json=dumps(result["rule_outs"]), differentials_json=dumps(result["differentials"]),
        missing_information_json=dumps(result["missing_information"]), rationale_json=dumps(result["rationale"]),
        postprocess_json=dumps(result["postprocess"]), provenance="AI_GENERATED", review_status="pending",
        original_ai_json=dumps({"model_output": result["original"], "prediction": result["postprocess"]["model_label"]}),
        model_version=result["model_version"], prompt_version=result["prompt_version"], schema_version=result["schema_version"],
        registry_version=result["registry_version"], extraction_job_id=job_id, is_active=True, source_revisions_json="{}",
        created_at=now, updated_at=now)
    previous = list(db.scalars(select(ConditionPredictionRecord).where(
        ConditionPredictionRecord.patient_id == patient_id, ConditionPredictionRecord.as_of_session_id == result["as_of_session_id"],
        ConditionPredictionRecord.condition_key == result["condition_key"], ConditionPredictionRecord.is_active.is_(True))))
    for old in previous:  # predictions are regenerable; earlier versions stay in history
        old.is_active, old.superseded_by_id, old.updated_at = False, rec.id, now
    db.add(rec)
    db.flush()
    for f in result["findings"]:
        db.add(CriterionFindingRecord(id=new_id("crit"), prediction_id=rec.id, criterion_id=f["criterion_id"], status=f["status"],
                                      evidence_json=dumps(f["evidence"]), counter_evidence_json=dumps(f["counter_evidence"]),
                                      timeframe=f["timeframe"], duration_note=f["duration_note"][:600]))
    db.flush()
    rec.source_revisions_json = dumps(write_links(db, "prediction", rec.id, segments_of(db, rec)))
    audit(db, patient_id=patient_id, session_id=result["as_of_session_id"], event_type="PROGRESS_PREDICTION_SAVED", actor="AI condition prediction",
          metadata={"prediction_id": rec.id, "condition_key": rec.condition_key, "prediction": rec.prediction, "subtype": rec.subtype_key or "",
                    "model_label": result["postprocess"]["model_label"], "job_id": job_id})
    return rec

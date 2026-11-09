"""Persistence and dashboard assembly for registry v2 (DSM). Reuses the v1 store helpers (evidence resolution,
links, audit, review state machine, decided-note lock) without changing v1 behaviour.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from services.api.db import (
    ConditionPredictionRecord, DomainObservationRecord, DsmBatchRecord, DsmFindingRecord, DsmFindingVersionRecord, DsmPredictionRecord,
    InstrumentScoreRecord, PlanFollowupRecord, RiskStatementRecord, SessionRecord, TranscriptSegmentRecord, utc_now,
)
from services.api.schemas import DsmFinding, DsmFindingInput, DsmPrediction
from services.progress import store as v1
from services.progress import trends

from . import rules
from .registry import DsmRegistry, dsm_registry

PENDING = {"pending", "stale"}
INACTIVE = {"rejected", "stale"}
FIELDS = ("status", "frequency", "onset_or_duration_text", "time_frame", "period_label", "qualifier", "degree", "basis")
GRID_GROUPS = [
    {"key": "depression", "label": "Depression", "disorders": ["MDE", "ANXD", "PDD"]},
    {"key": "bipolar", "label": "Bipolar (manic and hypomanic episodes)", "disorders": ["MANIA", "HYPO", "CYC"]},
    {"key": "gad", "label": "Generalized anxiety disorder", "disorders": ["GAD"]},
    {"key": "sad", "label": "Social anxiety disorder", "disorders": ["SAD"]},
]
CARD_ORDER = ["mdd", "mde", "bipolar_i", "bipolar_ii", "manic", "hypomanic", "gad", "sad", "pdd", "cyclothymic"]
A9_RISK = {"suicidal_ideation_passive", "suicidal_ideation_active", "self_harm_past", "self_harm_recent", "suicidal_ideation", "self_harm"}


# ---------------------------------------------------------------- findings

def snapshot(rec: DsmFindingRecord) -> dict:
    return {k: getattr(rec, k) for k in FIELDS + ("provenance", "review_status", "reviewer_name", "reviewer_comment", "extraction_job_id", "clinician_intensity")} | {
        "contexts": v1.loads(rec.contexts_json, []), "evidence_segment_ids": v1.loads(rec.evidence_segment_ids_json, []),
        "counter_evidence": v1.loads(rec.counter_evidence_json, []), "reviewed_at": rec.reviewed_at.isoformat() if rec.reviewed_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None}


def add_version(db: DbSession, rec: DsmFindingRecord, role: str, actor: str, snap: dict | None = None) -> None:
    count = len(db.scalars(select(DsmFindingVersionRecord.id).where(DsmFindingVersionRecord.finding_id == rec.id)).all())
    db.add(DsmFindingVersionRecord(id=v1.new_id("dfindv"), finding_id=rec.id, session_id=rec.session_id, feature_key=rec.feature_key,
                                   version_no=count + 1, role=role, snapshot_json=v1.dumps(snap or snapshot(rec)), actor=actor, created_at=utc_now()))


def finding_segments(rec: DsmFindingRecord) -> list[str]:
    return list(dict.fromkeys(v1.loads(rec.evidence_segment_ids_json, []) + [c["segment_id"] for c in v1.loads(rec.counter_evidence_json, [])]))


def to_finding(db: DbSession, rec: DsmFindingRecord, *, details: bool = True) -> DsmFinding:
    reg = dsm_registry()
    feat = reg.feature(rec.feature_key)
    counter = v1.loads(rec.counter_evidence_json, [])
    basis = {s: rec.basis for s in v1.loads(rec.evidence_segment_ids_json, [])}
    resolved = {e["segment_id"]: e for e in v1.resolve_evidence(db, rec.patient_id, [c["segment_id"] for c in counter], strict=False,
                                                                 basis={c["segment_id"]: c.get("basis", "") for c in counter})}
    versions = list(db.scalars(select(DsmFindingVersionRecord).where(DsmFindingVersionRecord.finding_id == rec.id)
                               .order_by(DsmFindingVersionRecord.version_no))) if details else []
    return DsmFinding(
        id=rec.id, patient_id=rec.patient_id, session_id=rec.session_id, feature_key=rec.feature_key, feature_label=feat.label, group=feat.group,
        registry_version=rec.registry_version, **{k: getattr(rec, k) for k in FIELDS}, contexts=v1.loads(rec.contexts_json, []),
        linked_items=v1.loads(rec.linked_items_json, []), clinician_intensity=rec.clinician_intensity,
        evidence=v1.resolve_evidence(db, rec.patient_id, v1.loads(rec.evidence_segment_ids_json, []), strict=False, basis=basis),
        counter_evidence=[{**resolved[c["segment_id"]], "kind": c.get("kind", "alternative")} for c in counter if c["segment_id"] in resolved],
        counter_check=v1.loads(rec.counter_check_json, {}), batch_id=rec.batch_id,
        alternatives=[v1.loads(x.snapshot_json, {}) | {"actor": x.actor, "created_at": x.created_at.isoformat()} for x in versions if x.role == "alternative"],
        history=[v1.loads(x.snapshot_json, {}) | {"actor": x.actor, "created_at": x.created_at.isoformat()} for x in versions if x.role == "history"],
        **v1._review(db, rec, rec.session_id))


def check_feature(reg: DsmRegistry, key: str):
    try:
        return reg.feature(key)
    except KeyError:
        raise v1.error(404, "FEATURE_NOT_FOUND", "Unknown DSM feature.") from None


def _check_value(reg: DsmRegistry, key: str, value: DsmFindingInput) -> None:
    feat = check_feature(reg, key)
    if set(value.contexts) - set(reg.features.contexts):
        raise v1.error(422, "INVALID_CONTEXT", f"Allowed contexts: {', '.join(reg.features.contexts)}.")
    if value.qualifier and value.qualifier not in feat.qualifiers:
        raise v1.error(422, "INVALID_QUALIFIER", f"Allowed qualifiers for {key}: {', '.join(feat.qualifiers) or 'none'}.")
    if value.degree and not feat.degree:
        raise v1.error(422, "INVALID_DEGREE", "Only impairment findings take a degree.")
    if value.status in {"present", "absent", "explicit_denial", "conflicting", "subthreshold"} and not value.evidence_segment_ids and value.status != "absent":
        pass  # manual entries may rest on the clinician's own observation without a cited turn


def put_manual_finding(db: DbSession, session: SessionRecord, key: str, value: DsmFindingInput) -> DsmFindingRecord:
    reg = dsm_registry()
    _check_value(reg, key, value)
    v1.ensure_unlocked(db, session.id)
    v1.resolve_evidence(db, session.patient_id, value.evidence_segment_ids + value.counter_evidence_segment_ids, own_session=session.id)
    now = utc_now()
    rec = active_finding(db, session.id, key)
    v1.check_expected(rec, value.expected_updated_at) if rec else None
    if rec is None:
        rec = DsmFindingRecord(id=v1.new_id("dfind"), patient_id=session.patient_id, session_id=session.id, feature_key=key, is_active=True, created_at=now)
        db.add(rec)
    else:
        add_version(db, rec, "history", value.actor)
    _apply(rec, {**value.model_dump(), "counter_evidence": [{"segment_id": s, "kind": "denial" if value.status in {"explicit_denial", "conflicting"} else "alternative", "basis": ""}
                                                            for s in value.counter_evidence_segment_ids]}, reg)
    rec.clinician_intensity = value.clinician_intensity
    rec.provenance, rec.review_status, rec.reviewer_name, rec.reviewed_at = "MANUAL", "accepted", value.actor, now
    rec.reviewer_comment = rec.stale_from_status = None
    rec.updated_at = now
    db.flush()
    rec.source_revisions_json = v1.dumps(v1.write_links(db, "dsm_finding", rec.id, finding_segments(rec)))
    v1.audit(db, patient_id=session.patient_id, session_id=session.id, event_type="PROGRESS_DSM_FINDING_ENTERED", actor=value.actor,
             metadata={"finding_id": rec.id, "feature_key": key, "status": rec.status})
    return rec


def _apply(rec: DsmFindingRecord, item: dict, reg: DsmRegistry) -> None:
    for k in FIELDS:
        if k in item:
            setattr(rec, k, item[k] if item[k] is not None or k in {"qualifier", "degree"} else getattr(rec, k))
    rec.frequency = rec.frequency or "unclear"
    rec.onset_or_duration_text = (rec.onset_or_duration_text or "")[:300]
    rec.period_label = (rec.period_label or "")[:160]
    rec.basis = (rec.basis or "")[:600]
    rec.contexts_json = v1.dumps(item.get("contexts", []))
    rec.evidence_segment_ids_json = v1.dumps(item.get("evidence_segment_ids", []))
    rec.counter_evidence_json = v1.dumps(item.get("counter_evidence", []))
    rec.counter_check_json = v1.dumps(item.get("counter_check", {}))
    rec.linked_items_json = v1.dumps([i.id for i in reg.items_for_feature(rec.feature_key)])
    rec.registry_version = reg.tag


def edit_finding(rec: DsmFindingRecord, value: dict) -> None:
    current = snapshot(rec)
    merged = {k: value.get(k, current.get(k)) for k in FIELDS + ("contexts", "clinician_intensity")}
    merged["evidence_segment_ids"] = value.get("evidence_segment_ids", current["evidence_segment_ids"])
    merged["counter_evidence_segment_ids"] = value.get("counter_evidence_segment_ids", [c["segment_id"] for c in current["counter_evidence"]])
    try:
        parsed = DsmFindingInput(actor="review", **{k: v for k, v in merged.items() if v is not None or k in {"qualifier", "degree", "clinician_intensity"}})
    except ValueError as exc:
        raise v1.error(422, "INVALID_REVIEW_VALUE", str(exc)) from None
    reg = dsm_registry()
    _check_value(reg, rec.feature_key, parsed)
    kinds = {c["segment_id"]: c.get("kind", "alternative") for c in current["counter_evidence"]}
    _apply(rec, {**parsed.model_dump(), "counter_check": v1.loads(rec.counter_check_json, {}),
                 "counter_evidence": [{"segment_id": s, "kind": kinds.get(s, "alternative"), "basis": ""} for s in parsed.counter_evidence_segment_ids]}, reg)
    rec.clinician_intensity = parsed.clinician_intensity


def active_finding(db: DbSession, session_id: str, key: str) -> DsmFindingRecord | None:
    return db.scalar(select(DsmFindingRecord).where(DsmFindingRecord.session_id == session_id, DsmFindingRecord.feature_key == key,
                                                    DsmFindingRecord.is_active.is_(True)))


def persist_findings(db: DbSession, session: SessionRecord, findings: dict[str, dict], *, job_id: str, batch_id: str | None) -> dict:
    """Stage A findings as active AI values (pending review). Manual and reviewed values are never overwritten."""
    reg = dsm_registry()
    now = utc_now()
    counts = {"findings_saved": 0, "findings_alternative": 0}
    v1.ensure_unlocked(db, session.id)
    for key, item in findings.items():
        rec = active_finding(db, session.id, key)
        suggestion = {**item, "provenance": "AI_GENERATED", "review_status": "pending", "extraction_job_id": job_id}
        if rec is not None and (rec.provenance == "MANUAL" or rec.review_status in v1.PROTECTED):
            add_version(db, rec, "alternative", "AI extraction", suggestion)
            counts["findings_alternative"] += 1
            continue
        if rec is None:
            rec = DsmFindingRecord(id=v1.new_id("dfind"), patient_id=session.patient_id, session_id=session.id, feature_key=key, is_active=True, created_at=now)
            db.add(rec)
        else:
            add_version(db, rec, "history", "AI extraction")
        _apply(rec, item, reg)
        rec.clinician_intensity = None
        rec.provenance, rec.review_status, rec.original_ai_json = "AI_GENERATED", "pending", v1.dumps(item)
        rec.reviewer_name = rec.reviewed_at = rec.reviewer_comment = rec.stale_from_status = None
        rec.extraction_job_id, rec.batch_id, rec.updated_at = job_id, batch_id, now
        db.flush()
        rec.source_revisions_json = v1.dumps(v1.write_links(db, "dsm_finding", rec.id, finding_segments(rec)))
        counts["findings_saved"] += 1
    return counts


def persist_risks(db: DbSession, session: SessionRecord, risks: list[dict], *, job_id: str) -> int:
    """DSM risk flags and supporting notes: replace this session's unreviewed AI flags; reviewed flags stay."""
    now = utc_now()
    existing = list(db.scalars(select(RiskStatementRecord).where(RiskStatementRecord.session_id == session.id, RiskStatementRecord.is_active.is_(True))))
    reviewed = {tuple(sorted(v1.loads(r.segment_ids_json, []))) for r in existing if r.review_status in v1.PROTECTED | {"rejected"}}
    for r in existing:
        if r.provenance == "AI_GENERATED" and r.review_status in PENDING:
            r.is_active, r.updated_at = False, now
    saved = 0
    for item in risks:
        if tuple(sorted(item["segment_ids"])) in reviewed:
            continue
        rec = RiskStatementRecord(id=v1.new_id("risk"), patient_id=session.patient_id, session_id=session.id, segment_ids_json=v1.dumps(item["segment_ids"]),
                                  category=item["categories"][0], categories_json=v1.dumps(item["categories"]), note_kind=item["note_kind"], registry_kind="dsm",
                                  basis=item["basis"], provenance="AI_GENERATED", review_status="pending", original_ai_json=v1.dumps(item),
                                  extraction_job_id=job_id, is_active=True, source_revisions_json="{}", created_at=now, updated_at=now)
        db.add(rec)
        db.flush()
        rec.source_revisions_json = v1.dumps(v1.write_links(db, "risk_statement", rec.id, item["segment_ids"]))
        saved += 1
    return saved


# ---------------------------------------------------------------- inputs for Stage B and the grid

def roles_of(db: DbSession, segment_ids: set[str]) -> dict[str, dict]:
    if not segment_ids:
        return {}
    rows = db.scalars(select(TranscriptSegmentRecord).where(TranscriptSegmentRecord.id.in_(segment_ids)))
    return {r.id: {"segment_id": r.id, "session_id": r.session_id, "start_ms": r.start_ms, "end_ms": r.end_ms, "speaker_role": r.speaker_role} for r in rows}


def session_findings(db: DbSession, session_ids: list[str]) -> dict[str, list[DsmFindingRecord]]:
    rows = list(db.scalars(select(DsmFindingRecord).where(DsmFindingRecord.session_id.in_(session_ids), DsmFindingRecord.is_active.is_(True))))
    out: dict[str, list[DsmFindingRecord]] = {s: [] for s in session_ids}
    for r in rows:
        out[r.session_id].append(r)
    return out


def proposal_of(rec: DsmFindingRecord, segments: dict[str, dict]) -> dict:
    ev = [{**segments[s], "basis": rec.basis} for s in v1.loads(rec.evidence_segment_ids_json, []) if s in segments]
    counter = [{**segments[c["segment_id"]], "kind": c.get("kind", "alternative"), "basis": c.get("basis", "")}
               for c in v1.loads(rec.counter_evidence_json, []) if c["segment_id"] in segments]
    return {"status": rec.status, "contexts": v1.loads(rec.contexts_json, []), "degree": rec.degree, "qualifier": rec.qualifier,
            "frequency": rec.frequency, "evidence": ev, "counter_evidence": counter, "finding_id": rec.id, "review_status": rec.review_status,
            "provenance": rec.provenance, "reviewer_name": rec.reviewer_name, "time_frame": rec.time_frame, "period_label": rec.period_label,
            "onset_or_duration_text": rec.onset_or_duration_text}


def a9_status(db: DbSession, session_id: str) -> dict:
    """MDE.A9 counts only from a clinician-accepted (or edited) risk statement or clinician-entered PHQ-9 item 9."""
    risks = db.scalars(select(RiskStatementRecord).where(RiskStatementRecord.session_id == session_id, RiskStatementRecord.is_active.is_(True),
                                                         RiskStatementRecord.review_status.in_(["accepted", "edited"])))
    for r in risks:
        cats = set(v1.loads(r.categories_json, []) or [r.category])
        if cats & A9_RISK and r.note_kind != "supporting":
            ids = v1.loads(r.segment_ids_json, [])
            seg = roles_of(db, set(ids))
            return {"present": True, "source": f"risk statement reviewed by {r.reviewer_name}", "evidence": [seg[i] for i in ids if i in seg]}
    score = db.scalar(select(InstrumentScoreRecord).where(InstrumentScoreRecord.session_id == session_id, InstrumentScoreRecord.instrument_key == "phq9",
                                                          InstrumentScoreRecord.provenance == "MANUAL", InstrumentScoreRecord.is_active.is_(True)))
    if score is not None:
        item9 = next((i for i in v1.loads(score.item_scores_json, []) if i["item_id"] == "9"), None)
        if item9 and item9.get("score") is not None and item9["score"] >= 1:
            return {"present": True, "source": f"PHQ-9 item 9 entered by {score.reviewer_name}", "evidence": []}
    return {"present": False}


# ---------------------------------------------------------------- predictions

def prediction_segments(rec: DsmPredictionRecord) -> list[str]:
    ids = []
    for e in v1.loads(rec.elements_json, []):
        for c in e.get("evidence", []) + e.get("counter_evidence", []):
            ids.append(c["segment_id"])
        for r in e.get("items", []):
            ids += [c["segment_id"] for c in r.get("evidence", []) + r.get("counter_evidence", [])]
    for p in v1.loads(rec.periods_json, []):
        ids += [c["segment_id"] for c in p.get("duration_evidence", [])]
    return list(dict.fromkeys(i for i in ids if i))


def to_dsm_prediction(db: DbSession, rec: DsmPredictionRecord) -> DsmPrediction:
    reg = dsm_registry()
    try:
        cond = reg.condition(rec.condition_key)
        label = cond.label
    except KeyError:
        label = rec.condition_key
    resolved = {e["segment_id"]: e for e in v1.resolve_evidence(db, rec.patient_id, prediction_segments(rec), strict=False)}

    def cite(items):
        return [{**resolved[c["segment_id"]], "basis": c.get("basis", ""), **({"kind": c["kind"]} if c.get("kind") else {})}
                for c in items if c.get("segment_id") in resolved]
    elements = []
    for e in v1.loads(rec.elements_json, []):
        elements.append({**e, "evidence": cite(e.get("evidence", [])), "counter_evidence": cite(e.get("counter_evidence", [])),
                         "items": [{**r, "paraphrase": _paraphrase(reg, r["item"]), "evidence": cite(r.get("evidence", [])),
                                    "counter_evidence": cite(r.get("counter_evidence", []))} for r in e.get("items", [])]})
    periods = [{**p, "duration_evidence": cite(p.get("duration_evidence", []))} for p in v1.loads(rec.periods_json, [])]
    return DsmPrediction(
        id=rec.id, patient_id=rec.patient_id, as_of_session_id=rec.as_of_session_id, condition_key=rec.condition_key, condition_label=label,
        role=rec.role, label=rec.label, label_text=f"Criteria pattern consistent with {label.lower()}: {rules.LABEL_TEXT[rec.label].lower()}",
        footer=reg.conditions.label_footer, period=v1.loads(rec.period_json, {}), elements=elements, specifiers=v1.loads(rec.specifiers_json, []),
        codes=v1.loads(rec.codes_json, {}), differentials=v1.loads(rec.differentials_json, []), missing_information=v1.loads(rec.missing_information_json, []),
        periods=periods, criteria_met_count=rec.criteria_met_count, criteria_required=rec.criteria_required, postprocess=v1.loads(rec.postprocess_json, {}),
        model_version=rec.model_version, prompt_version=rec.prompt_version, registry_version=rec.registry_version, batch_id=rec.batch_id,
        **v1._review(db, rec, None))


def _paraphrase(reg: DsmRegistry, item_id: str) -> str:
    try:
        return reg.item(item_id).paraphrase
    except KeyError:
        return ""


def persist_dsm_prediction(db: DbSession, patient_id: str, result: dict, *, job_id: str, batch_id: str | None) -> DsmPredictionRecord:
    now = utc_now()
    rec = DsmPredictionRecord(
        id=v1.new_id("dpred"), patient_id=patient_id, as_of_session_id=result["as_of_session_id"], condition_key=result["condition_key"],
        label=result["label"], role=result["role"], period_json=v1.dumps(result["period"]), elements_json=v1.dumps(result["elements"]),
        specifiers_json=v1.dumps(result["specifiers"]), codes_json=v1.dumps(result["codes"]), differentials_json=v1.dumps(result["differentials"]),
        missing_information_json=v1.dumps(result["missing_information"]), periods_json=v1.dumps(result.get("periods", [])),
        proposals_json=v1.dumps(result.get("proposals", {})), criteria_met_count=result["criteria_met_count"], criteria_required=result["criteria_required"],
        postprocess_json=v1.dumps(result.get("postprocess", {})), model_version=result.get("model_version", ""), prompt_version=result.get("prompt_version", ""),
        schema_version=result.get("schema_version", ""), registry_version=result.get("registry_version", dsm_registry().tag), is_active=True,
        provenance="AI_GENERATED", review_status="pending", original_ai_json=v1.dumps({"label": result["label"], "model_label": result.get("postprocess", {}).get("model_label")}),
        extraction_job_id=job_id, batch_id=batch_id, source_revisions_json="{}", created_at=now, updated_at=now)
    for old in db.scalars(select(DsmPredictionRecord).where(DsmPredictionRecord.patient_id == patient_id, DsmPredictionRecord.as_of_session_id == rec.as_of_session_id,
                                                           DsmPredictionRecord.condition_key == rec.condition_key, DsmPredictionRecord.is_active.is_(True))):
        old.is_active, old.superseded_by_id, old.updated_at = False, rec.id, now
    db.add(rec)
    db.flush()
    rec.source_revisions_json = v1.dumps(v1.write_links(db, "dsm_prediction", rec.id, prediction_segments(rec)))
    v1.audit(db, patient_id=patient_id, session_id=rec.as_of_session_id, event_type="PROGRESS_DSM_PREDICTION_SAVED", actor="AI condition prediction (DSM)",
             metadata={"prediction_id": rec.id, "condition_key": rec.condition_key, "label": rec.label,
                       "model_label": str(result.get("postprocess", {}).get("model_label")), "job_id": job_id})
    return rec


def edit_dsm_prediction(rec: DsmPredictionRecord, value: dict) -> None:
    label = value.get("label")
    if label not in rules.LABEL_TEXT:
        raise v1.error(422, "INVALID_REVIEW_VALUE", "label must be likely, possible, insufficient_evidence or not_met.")
    rec.label = label


# ---------------------------------------------------------------- batches (re-analysis and revert)

def create_batch(db: DbSession, patient_id: str, kind: str, job_id: str | None, session_ids: list[str], skipped: list[dict]) -> DsmBatchRecord:
    rec = DsmBatchRecord(id=v1.new_id("dbatch"), patient_id=patient_id, kind=kind, job_id=job_id, session_ids_json=v1.dumps(session_ids),
                         skipped_json=v1.dumps(skipped), status="running", created_at=utc_now())
    db.add(rec)
    db.flush()
    return rec


def retire_v1_for_session(db: DbSession, session_id: str, batch_id: str) -> dict:
    """Earlier (v1) model output for a re-analysed session leaves the dashboard; clinician-entered and reviewed values stay.

    Rows are never deleted: they carry ``retired_batch_id`` so a revert restores them.
    """
    counts = {"domains": 0, "scores": 0, "risks": 0, "predictions": 0}
    unreviewed = PENDING
    for r in db.scalars(select(DomainObservationRecord).where(DomainObservationRecord.session_id == session_id, DomainObservationRecord.retired_batch_id.is_(None))):
        if r.provenance == "AI_GENERATED" and r.review_status in unreviewed:
            r.retired_batch_id = batch_id
            counts["domains"] += 1
    for r in db.scalars(select(InstrumentScoreRecord).where(InstrumentScoreRecord.session_id == session_id, InstrumentScoreRecord.is_active.is_(True))):
        if r.provenance == "AI_GENERATED" and r.review_status in unreviewed:
            r.is_active, r.retired_batch_id = False, batch_id
            counts["scores"] += 1
    for r in db.scalars(select(RiskStatementRecord).where(RiskStatementRecord.session_id == session_id, RiskStatementRecord.is_active.is_(True))):
        if r.provenance == "AI_GENERATED" and r.review_status in unreviewed and r.registry_kind == "v1":
            r.is_active, r.retired_batch_id = False, batch_id
            counts["risks"] += 1
    for r in db.scalars(select(ConditionPredictionRecord).where(ConditionPredictionRecord.as_of_session_id == session_id, ConditionPredictionRecord.is_active.is_(True))):
        if r.review_status in unreviewed:
            r.is_active, r.retired_batch_id = False, batch_id
            counts["predictions"] += 1
    db.flush()
    return counts


def revert_batch(db: DbSession, batch: DsmBatchRecord, actor: str) -> dict:
    """Deactivate everything the batch's jobs created (kept in history) and restore the v1 rows it retired."""
    if batch.reverted_at is not None:
        raise v1.error(409, "ALREADY_REVERTED", "This analysis was already reverted.")
    job_ids = {batch.job_id} if batch.job_id else set()
    counts = {"deactivated": 0, "restored": 0}
    for model in (DsmFindingRecord, DsmPredictionRecord):
        for r in db.scalars(select(model).where(model.batch_id == batch.id, model.is_active.is_(True))):
            r.is_active = False
            counts["deactivated"] += 1
    for model in (RiskStatementRecord, InstrumentScoreRecord):
        for r in db.scalars(select(model).where(model.extraction_job_id.in_(job_ids), model.is_active.is_(True))) if job_ids else []:
            r.is_active = False
            counts["deactivated"] += 1
    for model in (InstrumentScoreRecord, RiskStatementRecord, ConditionPredictionRecord):
        for r in db.scalars(select(model).where(model.retired_batch_id == batch.id)):
            r.is_active, r.retired_batch_id = True, None
            counts["restored"] += 1
    for r in db.scalars(select(DomainObservationRecord).where(DomainObservationRecord.retired_batch_id == batch.id)):
        r.retired_batch_id = None
        counts["restored"] += 1
    batch.reverted_at, batch.reverted_by, batch.status = utc_now(), actor, "reverted"
    db.flush()
    v1.audit(db, patient_id=batch.patient_id, session_id=None, event_type="PROGRESS_DSM_ANALYSIS_REVERTED", actor=actor,
             metadata={"batch_id": batch.id, **{k: str(v) for k, v in counts.items()}})
    return counts


# ---------------------------------------------------------------- dashboard block

def _review_of(props: list[dict]) -> dict:
    if not props:
        return {"provenance": None, "review_status": None, "reviewer_name": None}
    statuses = [p.get("review_status") for p in props]
    for s in ("stale", "pending", "edited", "accepted"):
        if s in statuses:
            chosen = next(p for p in props if p.get("review_status") == s)
            return {"provenance": chosen.get("provenance"), "review_status": s, "reviewer_name": chosen.get("reviewer_name")}
    return {"provenance": props[0].get("provenance"), "review_status": statuses[0], "reviewer_name": props[0].get("reviewer_name")}


def build_dsm_block(db: DbSession, patient_id: str, sessions: list[SessionRecord]) -> tuple[dict, list[dict]]:
    """Criteria grid, hypotheses, timeline and verification summary. Returns (block, computed change events)."""
    from .verification import effective
    reg = dsm_registry()
    ids = [s.id for s in sessions]
    found = session_findings(db, ids)
    segments = roles_of(db, {sid for rows in found.values() for r in rows for sid in finding_segments(r)})
    status_map = effective(db, reg)
    computed: list[dict] = []
    feature_rows = []
    per_session_props: dict[str, dict[str, dict]] = {}
    per_session_all: dict[str, dict[str, dict]] = {}
    for sid in ids:
        per_session_all[sid] = {r.feature_key: proposal_of(r, segments) for r in found[sid]}
        per_session_props[sid] = {k: p for k, p in per_session_all[sid].items() if p["review_status"] not in INACTIVE}
    for feat in reg.features.features:
        cells = []
        for sid in ids:
            p = per_session_all[sid].get(feat.key)
            cells.append({"session_id": sid, "record_id": p["finding_id"] if p else None, "status": p["status"] if p else None,
                          "frequency": p["frequency"] if p else None, "qualifier": p["qualifier"] if p else None,
                          "time_frame": p["time_frame"] if p else None, **_review_of([p] if p else [])})
        feature_rows.append({"key": feat.key, "label": feat.label, "group": feat.group, "linked_items": [i.id for i in reg.items_for_feature(feat.key)], "cells": cells})
    grid = []
    for group in GRID_GROUPS:
        rows = []
        for item in reg.items.items:
            if item.disorder not in group["disorders"] or (item.satisfied_by is None and item.model_assessed):
                continue
            cells, points = [], []
            for s in sessions:
                period = {"id": s.id, "label": s.id, "time_frame": "current", "duration_days": None, "hospitalised": False,
                          "duration_evidence": [], "proposals": per_session_props[s.id]}
                extras = {"a9": a9_status(db, s.id)} if not item.model_assessed else {}
                res = rules.item_status(reg, item.id, period, extras)
                used = [per_session_all[s.id][leaf.feature] for leaf in _leaves(item) if leaf.feature in per_session_all[s.id]]
                review = _review_of(used)
                assessed = bool(used) or res["status"] == "present"
                if review["review_status"] == "stale":
                    status = "stale"
                elif not assessed:
                    status = None
                else:
                    status = res["status"]
                cells.append({"session_id": s.id, "status": status, "note": res.get("note", ""), "frequency": res.get("frequency", ""),
                              "finding_ids": [p["finding_id"] for p in used], **review})
                points.append(trends.Point(slot=s.id, label=status if status not in {None, "stale"} else None, status=status,
                                           provenance=review["provenance"], review_status=review["review_status"],
                                           record_id=used[0]["finding_id"] if used else None))
            changes = trends.criterion_changes(points)
            computed += v1._changes_json("criterion", item.id, "criterion", changes, {s.id: s.id for s in sessions})
            rows.append({"id": item.id, "label": item.label, "paraphrase": item.paraphrase, "disorder": item.disorder, "criterion": item.criterion,
                         "kind": item.kind, "letter": item.criterion[0], "verification": status_map[("item", item.id)]["status"],
                         "features": [leaf.feature for leaf in _leaves(item)], "model_assessed": item.model_assessed, "cells": cells})
        grid.append({**{k: group[k] for k in ("key", "label")}, "rows": rows})
    preds = list(db.scalars(select(DsmPredictionRecord).where(DsmPredictionRecord.patient_id == patient_id, DsmPredictionRecord.is_active.is_(True))))
    order = {s.id: i for i, s in enumerate(sessions)}
    latest, timeline = [], []
    for key in CARD_ORDER:
        rows_ = {p.as_of_session_id: p for p in preds if p.condition_key == key}
        points = []
        for s in sessions:
            p = rows_.get(s.id)
            points.append(trends.Point(slot=s.id, label=p.label if p else None, provenance=p.provenance if p else None,
                                       review_status=p.review_status if p else None, record_id=p.id if p else None,
                                       value=p.criteria_met_count if p else None,
                                       meta={"criteria_met_count": p.criteria_met_count, "criteria_required": p.criteria_required} if p else {}))
        computed += v1._changes_json("condition", key, "condition", trends.condition_changes(points), {s.id: s.id for s in sessions})
        active = trends.activate(points)
        timeline.append({"condition_key": key, "label": reg.condition(key).label, "role": reg.condition(key).role,
                         "points": [v1._point_json(p, a) for p, a in zip(points, active)]})
        candidates = [p for p in rows_.values() if p.review_status != "rejected"] or list(rows_.values())
        if candidates:
            latest.append(to_dsm_prediction(db, max(candidates, key=lambda p: order.get(p.as_of_session_id, -1))).model_dump(mode="json"))
    counts = {"pending": 0, "verified": 0, "changed": 0}
    for (kind, _), v in status_map.items():
        if kind == "item":
            counts[v["status"]] = counts.get(v["status"], 0) + 1
    batches = list(db.scalars(select(DsmBatchRecord).where(DsmBatchRecord.patient_id == patient_id).order_by(DsmBatchRecord.created_at.desc())))[:10]
    pending = {"dsm_findings": sum(1 for rows in found.values() for r in rows if r.review_status in PENDING and r.status != "insufficient_evidence"),
               "dsm_predictions": sum(p.review_status in PENDING for p in preds)}
    block = {"registry_version": reg.tag, "label_footer": reg.conditions.label_footer, "verification_summary": counts,
             "verification_note": "Paraphrased from DSM-5-TR · pending clinical verification", "grid": grid, "features": feature_rows,
             "feature_groups": [g.model_dump() for g in reg.features.groups], "predictions": latest, "timeline": timeline,
             "pending": pending, "has_findings": any(found.values()),
             "batches": [{"id": b.id, "kind": b.kind, "status": b.status, "created_at": b.created_at.isoformat(), "job_id": b.job_id,
                          "session_ids": v1.loads(b.session_ids_json, []), "skipped": v1.loads(b.skipped_json, []),
                          "reverted_at": b.reverted_at.isoformat() if b.reverted_at else None, "reverted_by": b.reverted_by} for b in batches]}
    return block, computed


def _leaves(item):
    from .registry import leaves
    return leaves(item.satisfied_by)

"""Progress monitoring API: observations, instrument scores, condition hypotheses, risk statements.

AI values are first-class (shown and trended immediately); review status is a visible label.
"""
import json
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from services.api.db import (
    AuditEventRecord, ChangeEventRecord, ConditionPredictionRecord, DomainObservationRecord, InstrumentScoreRecord,
    PlanFollowupRecord, RiskStatementRecord, utc_now,
)
from services.api.schemas import (
    AuditEvent, ChangeAcknowledgement, ChangeEvent, ConditionPrediction, DomainObservation, DomainObservationInput,
    InstrumentScore, InstrumentScoreInput, PatientProgress, PlanFollowup, ProgressReview, RiskStatement,
)
from services.progress import jobs, store
from services.progress.registry import registry


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript_reviewed: bool = False
    evidence_mode: Literal["auto", "full", "retrieval"] = "auto"
    predict: bool = True


class PredictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    as_of_session_id: str = Field(min_length=1)
    condition_keys: list[str] | None = None


def build_progress_router(db_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["progress monitoring"])

    @router.get("/progress/registry")
    def registry_view():
        reg = registry()
        return {"versions": {k: v.tag for k, v in reg.versions.items()},
                "instruments": reg.instruments.model_dump(), "domains": reg.domains.model_dump(),
                "conditions": reg.conditions.model_dump()}

    # ---------------- domain observations
    @router.get("/sessions/{session_id}/domain-observations", response_model=list[DomainObservation])
    def session_observations(session_id: str, db=Depends(db_dependency)):
        session = store.session_or_404(db, session_id)
        rows = db.scalars(select(DomainObservationRecord).where(DomainObservationRecord.session_id == session.id))
        order = {d.key: i for i, d in enumerate(registry().domains.domains)}
        return sorted((store.to_domain(db, r) for r in rows), key=lambda o: order.get(o.domain_key, 999))

    @router.put("/sessions/{session_id}/domain-observations/{domain_key}", response_model=DomainObservation)
    def put_observation(session_id: str, domain_key: str, payload: DomainObservationInput, db=Depends(db_dependency)):
        session = store.session_or_404(db, session_id)
        store.ensure_registry_versions(db)
        rec = store.put_manual_domain(db, session, domain_key, payload)
        db.commit()
        return store.to_domain(db, rec)

    @router.post("/sessions/{session_id}/domain-observations/{observation_id}/review", response_model=DomainObservation)
    def review_observation(session_id: str, observation_id: str, payload: ProgressReview, db=Depends(db_dependency)):
        session = store.session_or_404(db, session_id)
        rec = db.get(DomainObservationRecord, observation_id)
        if rec is None or rec.session_id != session.id:
            raise store.error(404, "OBSERVATION_NOT_FOUND", "Observation was not found for this session.")
        store.ensure_unlocked(db, session.id)

        def edit(r, value):
            store.edit_domain(r, value)
            store.resolve_evidence(db, session.patient_id, json.loads(r.evidence_segment_ids_json), own_session=session.id)
        store.review(db, rec, payload, kind="domain_observation", patient_id=session.patient_id, session_id=session.id, apply_edit=edit)
        db.commit()
        return store.to_domain(db, rec)

    # ---------------- instrument scores
    @router.get("/patients/{patient_id}/instrument-scores", response_model=list[InstrumentScore])
    def list_scores(patient_id: str, include_inactive: bool = False, db=Depends(db_dependency)):
        store.patient_or_404(db, patient_id)
        query = select(InstrumentScoreRecord).where(InstrumentScoreRecord.patient_id == patient_id)
        if not include_inactive:
            query = query.where(InstrumentScoreRecord.is_active.is_(True))
        return [store.to_score(db, r) for r in db.scalars(query.order_by(InstrumentScoreRecord.created_at))]

    @router.post("/patients/{patient_id}/instrument-scores", response_model=InstrumentScore, status_code=201)
    def add_score(patient_id: str, payload: InstrumentScoreInput, db=Depends(db_dependency)):
        patient = store.patient_or_404(db, patient_id)
        store.ensure_registry_versions(db)
        rec = store.add_manual_score(db, patient, payload)
        db.commit()
        return store.to_score(db, rec)

    @router.post("/patients/{patient_id}/instrument-scores/{score_id}/review", response_model=InstrumentScore)
    def review_score(patient_id: str, score_id: str, payload: ProgressReview, db=Depends(db_dependency)):
        store.patient_or_404(db, patient_id)
        rec = db.get(InstrumentScoreRecord, score_id)
        if rec is None or rec.patient_id != patient_id:
            raise store.error(404, "SCORE_NOT_FOUND", "Score was not found for this patient.")
        store.ensure_unlocked(db, rec.session_id)
        store.review(db, rec, payload, kind="instrument_score", patient_id=patient_id, session_id=rec.session_id,
                     apply_edit=store.make_score_editor(db))
        db.commit()
        return store.to_score(db, rec)

    # ---------------- condition predictions
    @router.get("/patients/{patient_id}/condition-predictions")
    def list_predictions(patient_id: str, db=Depends(db_dependency)):
        store.patient_or_404(db, patient_id)
        rows = list(db.scalars(select(ConditionPredictionRecord).where(ConditionPredictionRecord.patient_id == patient_id)
                               .order_by(ConditionPredictionRecord.created_at)))
        history = [store.to_prediction(db, r, details=False) for r in rows]
        progress = store.build_progress(db, patient_id)
        return {"latest": progress.predictions, "history": history}

    @router.get("/patients/{patient_id}/condition-predictions/{prediction_id}", response_model=ConditionPrediction)
    def get_prediction(patient_id: str, prediction_id: str, db=Depends(db_dependency)):
        rec = db.get(ConditionPredictionRecord, prediction_id)
        if rec is None or rec.patient_id != patient_id:
            raise store.error(404, "PREDICTION_NOT_FOUND", "Prediction was not found for this patient.")
        return store.to_prediction(db, rec)

    @router.post("/patients/{patient_id}/condition-predictions/{prediction_id}/review", response_model=ConditionPrediction)
    def review_prediction(patient_id: str, prediction_id: str, payload: ProgressReview, db=Depends(db_dependency)):
        rec = db.get(ConditionPredictionRecord, prediction_id)
        if rec is None or rec.patient_id != patient_id:
            raise store.error(404, "PREDICTION_NOT_FOUND", "Prediction was not found for this patient.")
        store.review(db, rec, payload, kind="prediction", patient_id=patient_id, session_id=rec.as_of_session_id,
                     apply_edit=store.edit_prediction)
        db.commit()
        return store.to_prediction(db, rec)

    # ---------------- risk statements
    @router.get("/patients/{patient_id}/risk-statements", response_model=list[RiskStatement])
    def list_risks(patient_id: str, db=Depends(db_dependency)):
        store.patient_or_404(db, patient_id)
        rows = db.scalars(select(RiskStatementRecord).where(RiskStatementRecord.patient_id == patient_id, RiskStatementRecord.is_active.is_(True))
                          .order_by(RiskStatementRecord.created_at))
        return [store.to_risk(db, r) for r in rows]

    @router.post("/patients/{patient_id}/risk-statements/{risk_id}/review", response_model=RiskStatement)
    def review_risk(patient_id: str, risk_id: str, payload: ProgressReview, db=Depends(db_dependency)):
        rec = db.get(RiskStatementRecord, risk_id)
        if rec is None or rec.patient_id != patient_id:
            raise store.error(404, "RISK_STATEMENT_NOT_FOUND", "Risk statement was not found for this patient.")
        store.ensure_unlocked(db, rec.session_id)
        store.review(db, rec, payload, kind="risk_statement", patient_id=patient_id, session_id=rec.session_id, apply_edit=store.edit_risk)
        db.commit()
        return store.to_risk(db, rec)

    # ---------------- plan follow-ups
    @router.get("/patients/{patient_id}/plan-followups", response_model=list[PlanFollowup])
    def list_plans(patient_id: str, db=Depends(db_dependency)):
        store.patient_or_404(db, patient_id)
        rows = db.scalars(select(PlanFollowupRecord).where(PlanFollowupRecord.patient_id == patient_id).order_by(PlanFollowupRecord.created_at))
        return [store.to_plan(db, r) for r in rows]

    @router.post("/patients/{patient_id}/plan-followups/{plan_id}/review", response_model=PlanFollowup)
    def review_plan(patient_id: str, plan_id: str, payload: ProgressReview, db=Depends(db_dependency)):
        rec = db.get(PlanFollowupRecord, plan_id)
        if rec is None or rec.patient_id != patient_id:
            raise store.error(404, "PLAN_FOLLOWUP_NOT_FOUND", "Plan follow-up was not found for this patient.")
        store.ensure_unlocked(db, rec.followup_session_id)
        store.review(db, rec, payload, kind="plan_followup", patient_id=patient_id, session_id=rec.followup_session_id, apply_edit=store.edit_plan)
        db.commit()
        return store.to_plan(db, rec)

    # ---------------- local AI jobs (Stage A extraction, Stage B prediction)
    @router.post("/sessions/{session_id}/progress-extraction", status_code=202)
    def start_extraction(session_id: str, payload: ExtractionRequest):
        return {"job_id": jobs.start_job("extraction", session_id=session_id, request=payload.model_dump())}

    @router.post("/patients/{patient_id}/condition-predictions", status_code=202)
    def start_prediction(patient_id: str, payload: PredictionRequest, db=Depends(db_dependency)):
        store.session_or_404(db, payload.as_of_session_id, patient_id)
        unknown = set(payload.condition_keys or []) - set(registry().conditions.enabled_conditions)
        if unknown:
            raise store.error(422, "UNKNOWN_CONDITION", f"Unknown or disabled conditions: {sorted(unknown)}")
        return {"job_id": jobs.start_job("prediction", session_id=payload.as_of_session_id,
                                         request={"condition_keys": payload.condition_keys, "predict": True})}

    @router.get("/progress-jobs/{job_id}")
    def get_job(job_id: str):
        return jobs.load_job(job_id)

    @router.get("/sessions/{session_id}/progress-jobs")
    def session_jobs(session_id: str):
        return jobs.list_jobs(session_id=session_id)

    @router.post("/progress-jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        jobs.cancel_job(job_id)
        return {"cancel_requested": True}

    @router.post("/progress-jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str):
        return {"job_id": jobs.retry_job(job_id)}

    # ---------------- dashboard, changes, audit
    @router.get("/patients/{patient_id}/progress", response_model=PatientProgress)
    def progress(patient_id: str, db=Depends(db_dependency)):
        return store.build_progress(db, patient_id)

    @router.post("/patients/{patient_id}/change-events/{event_id}/acknowledge", response_model=ChangeEvent)
    def acknowledge(patient_id: str, event_id: str, payload: ChangeAcknowledgement, db=Depends(db_dependency)):
        rec = db.get(ChangeEventRecord, event_id)
        if rec is None or rec.patient_id != patient_id:
            raise store.error(404, "CHANGE_EVENT_NOT_FOUND", "Change event was not found for this patient.")
        if rec.acknowledged_at:
            raise store.error(409, "ALREADY_ACKNOWLEDGED", "This change has already been acknowledged.")
        rec.acknowledged_by, rec.acknowledged_at, rec.comment = payload.actor, utc_now(), payload.comment
        store.audit(db, patient_id=patient_id, session_id=rec.to_session_id if not rec.to_session_id.startswith("date:") else None,
                    event_type="PROGRESS_CHANGE_ACKNOWLEDGED", actor=payload.actor,
                    metadata={"change_event_id": rec.id, "rule_id": rec.rule_id, "measure_key": rec.measure_key, "comment": payload.comment or ""})
        db.commit()
        return ChangeEvent.model_validate(rec, from_attributes=True)

    @router.get("/patients/{patient_id}/audit-events", response_model=list[AuditEvent])
    def patient_audit(patient_id: str, db=Depends(db_dependency)):
        store.patient_or_404(db, patient_id)
        rows = db.scalars(select(AuditEventRecord).where(AuditEventRecord.patient_id == patient_id)
                          .order_by(AuditEventRecord.occurred_at, AuditEventRecord.id))
        return [AuditEvent(id=r.id, session_id=r.session_id, patient_id=r.patient_id, note_draft_id=r.note_draft_id,
                           event_type=r.event_type, actor_name=r.actor_name, occurred_at=r.occurred_at,
                           metadata=json.loads(r.metadata_json)) for r in rows]

    # ---------------- research results (LongCounsel-8 benchmark reports; research data only)
    @router.get("/research/progress/runs")
    def research_runs():
        from services.progress.evaluation.benchmark import RUNS
        out = []
        for folder in sorted(RUNS.glob("*")) if RUNS.is_dir() else []:
            if folder.name.startswith("_") or not (folder / "config.json").exists():
                continue
            config = json.loads((folder / "config.json").read_text())
            status = json.loads((folder / "status.json").read_text()) if (folder / "status.json").exists() else {}
            out.append({"run_id": folder.name, "arm": config.get("arm"), "split": config.get("subset", {}).get("split"),
                        "state": status.get("state"), "completed": status.get("completed"), "total": status.get("total"),
                        "has_report": (folder / "report" / "report.json").exists()})
        return out

    @router.get("/research/progress/runs/{run_id}/report")
    def research_report(run_id: str):
        from services.progress.evaluation.benchmark import RUNS
        if not run_id.replace("-", "").replace("_", "").isalnum():
            raise store.error(404, "RUN_NOT_FOUND", "Unknown run.")
        path = RUNS / run_id / "report" / "report.json"
        if not path.exists():
            raise store.error(404, "REPORT_NOT_FOUND", "No report yet; run the report command for this run.")
        return json.loads(path.read_text())

    @router.get("/research/progress/vignette-runs")
    def vignette_runs():
        from services.progress.evaluation.vignettes import RUNS as VIGNETTE_RUNS
        out = []
        for folder in sorted(VIGNETTE_RUNS.glob("*")) if VIGNETTE_RUNS.is_dir() else []:
            if not (folder / "config.json").exists():
                continue
            status = json.loads((folder / "status.json").read_text()) if (folder / "status.json").exists() else {}
            out.append({"run_id": folder.name, "state": status.get("state"), "completed": status.get("completed"), "total": status.get("total"),
                        "has_report": (folder / "report" / "report.json").exists()})
        return out

    @router.get("/research/progress/vignette-runs/{run_id}/report")
    def vignette_report(run_id: str):
        from services.progress.evaluation.vignettes import RUNS as VIGNETTE_RUNS
        if not run_id.replace("-", "").replace("_", "").isalnum():
            raise store.error(404, "RUN_NOT_FOUND", "Unknown run.")
        path = VIGNETTE_RUNS / run_id / "report" / "report.json"
        if not path.exists():
            raise store.error(404, "REPORT_NOT_FOUND", "No report yet; run the vignette report command for this run.")
        return json.loads(path.read_text())

    return router

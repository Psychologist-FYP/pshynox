"""Progress registry v2 (DSM-5-TR) API: registry review (export/import), session findings, DSM hypotheses,
re-analysis and revert. AI values are first-class; review status is a visible label.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from services.api.db import DsmBatchRecord, DsmFindingRecord, DsmPredictionRecord
from services.api.schemas import DsmFinding, DsmFindingInput, DsmPrediction, ProgressReview, RegistryImportResult
from services.progress import jobs, store
from services.progress.dsm import store as dsm_store
from services.progress.dsm import verification
from services.progress.dsm.registry import dsm_registry, registry_kind


class ReanalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transcript_reviewed: bool = False
    session_ids: list[str] | None = None


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=120)
    csv: str = Field(min_length=1, max_length=2_000_000)


class RevertRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor: str = Field(min_length=1, max_length=120)


def build_dsm_router(db_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["progress monitoring (DSM registry)"])

    @router.get("/progress/registry/dsm")
    def dsm_registry_view(db=Depends(db_dependency)):
        reg = dsm_registry()
        status = verification.effective(db, reg)
        return {"active": registry_kind(), "version": reg.tag, "label_footer": reg.conditions.label_footer,
                "note": verification.PARAPHRASE_NOTE, "rows": verification.rows(db, reg),
                "items": [i.model_dump() | {"verification": status[("item", i.id)]} for i in reg.items.items],
                "features": [f.model_dump() | {"verification": status[("feature", f.key)]} for f in reg.features.features],
                "conditions": [c.model_dump() | {"verification": status[("condition", c.key)], "rule_summary": verification.rule_summary(c.model_dump())}
                               for c in reg.conditions.conditions],
                "feature_groups": [g.model_dump() for g in reg.features.groups], "contexts": reg.features.contexts,
                "frequencies": reg.features.frequencies, "statuses": reg.features.statuses,
                "instruments": [i.model_dump() for i in reg.instruments], "instrument_map": reg.instrument_map,
                "v1_mapping": reg.v1_mapping, "risk": reg.risk}

    @router.get("/progress/registry/dsm/export.csv")
    def export_csv(db=Depends(db_dependency)):
        return PlainTextResponse(verification.export_csv(db, dsm_registry()), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="dsm-registry-review.csv"'})

    @router.get("/progress/registry/dsm/export.html", response_class=HTMLResponse)
    def export_html(db=Depends(db_dependency)):
        return HTMLResponse(verification.export_html(db, dsm_registry()))

    @router.post("/progress/registry/dsm/verifications/import", response_model=RegistryImportResult)
    def import_verifications(payload: ImportRequest, db=Depends(db_dependency)):
        actor = payload.actor
        try:
            result = verification.import_csv(db, dsm_registry(), payload.csv, actor=actor)
        except ValueError as exc:
            raise store.error(422, "INVALID_REVIEW_CSV", str(exc)) from None
        store.audit(db, patient_id=None, session_id=None, event_type="DSM_REGISTRY_REVIEW_IMPORTED", actor=actor,
                    metadata={"import_batch_id": result["import_batch_id"], "recorded": str(result["recorded"]), "rejected": str(len(result["rejected"]))})
        db.commit()
        return result

    # ---------------- session findings
    @router.get("/sessions/{session_id}/dsm-findings", response_model=list[DsmFinding])
    def session_findings(session_id: str, db=Depends(db_dependency)):
        session = store.session_or_404(db, session_id)
        order = {f.key: i for i, f in enumerate(dsm_registry().features.features)}
        rows = db.scalars(select(DsmFindingRecord).where(DsmFindingRecord.session_id == session.id, DsmFindingRecord.is_active.is_(True)))
        return sorted((dsm_store.to_finding(db, r) for r in rows), key=lambda f: order.get(f.feature_key, 999))

    @router.put("/sessions/{session_id}/dsm-findings/{feature_key}", response_model=DsmFinding)
    def put_finding(session_id: str, feature_key: str, payload: DsmFindingInput, db=Depends(db_dependency)):
        session = store.session_or_404(db, session_id)
        rec = dsm_store.put_manual_finding(db, session, feature_key, payload)
        db.commit()
        return dsm_store.to_finding(db, rec)

    @router.post("/sessions/{session_id}/dsm-findings/{finding_id}/review", response_model=DsmFinding)
    def review_finding(session_id: str, finding_id: str, payload: ProgressReview, db=Depends(db_dependency)):
        rec = db.get(DsmFindingRecord, finding_id)
        if rec is None or rec.session_id != session_id:
            raise store.error(404, "FINDING_NOT_FOUND", "DSM finding was not found.")
        store.ensure_unlocked(db, session_id)
        store.review(db, rec, payload, kind="dsm_finding", patient_id=rec.patient_id, session_id=session_id, apply_edit=dsm_store.edit_finding)
        db.commit()
        return dsm_store.to_finding(db, rec)

    # ---------------- DSM hypotheses
    @router.get("/patients/{patient_id}/dsm-predictions/{prediction_id}", response_model=DsmPrediction)
    def get_prediction(patient_id: str, prediction_id: str, db=Depends(db_dependency)):
        rec = db.get(DsmPredictionRecord, prediction_id)
        if rec is None or rec.patient_id != patient_id:
            raise store.error(404, "PREDICTION_NOT_FOUND", "Hypothesis was not found.")
        return dsm_store.to_dsm_prediction(db, rec)

    @router.post("/patients/{patient_id}/dsm-predictions/{prediction_id}/review", response_model=DsmPrediction)
    def review_prediction(patient_id: str, prediction_id: str, payload: ProgressReview, db=Depends(db_dependency)):
        rec = db.get(DsmPredictionRecord, prediction_id)
        if rec is None or rec.patient_id != patient_id:
            raise store.error(404, "PREDICTION_NOT_FOUND", "Hypothesis was not found.")
        store.review(db, rec, payload, kind="dsm_prediction", patient_id=patient_id, session_id=rec.as_of_session_id, apply_edit=dsm_store.edit_dsm_prediction)
        db.commit()
        return dsm_store.to_dsm_prediction(db, rec)

    # ---------------- re-analysis and revert
    @router.post("/patients/{patient_id}/dsm-reanalysis", status_code=202)
    def start_reanalysis(patient_id: str, payload: ReanalysisRequest, db=Depends(db_dependency)):
        store.patient_or_404(db, patient_id)
        if not payload.transcript_reviewed:
            raise store.error(422, "TRANSCRIPT_REVIEW_REQUIRED", "Confirm that you reviewed the transcripts first.")
        sessions = store.patient_sessions(db, patient_id)
        chosen = [s for s in sessions if payload.session_ids is None or s.id in payload.session_ids]
        if payload.session_ids and len(chosen) != len(set(payload.session_ids)):
            raise store.error(422, "UNKNOWN_SESSION", "Every session must belong to this patient.")
        if not chosen:
            raise store.error(422, "NO_SESSIONS", "This patient has no sessions to analyse.")
        return {"job_id": jobs.start_job("reanalysis", session_id=chosen[-1].id,
                                         request={"session_ids": [s.id for s in chosen], "transcript_reviewed": True, "patient_id": patient_id})}

    @router.post("/patients/{patient_id}/dsm-batches/{batch_id}/revert")
    def revert(patient_id: str, batch_id: str, payload: RevertRequest, db=Depends(db_dependency)):
        batch = db.get(DsmBatchRecord, batch_id)
        if batch is None or batch.patient_id != patient_id:
            raise store.error(404, "BATCH_NOT_FOUND", "Analysis batch was not found.")
        counts = dsm_store.revert_batch(db, batch, payload.actor)
        db.commit()
        return {"batch_id": batch_id, **counts}

    return router

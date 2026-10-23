import json
import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from services.api.db import PatientRecord, SessionRecord, NoteDraftRecord, serialize_record, utc_now
from services.api.schemas import Patient, PatientInput, PatientAssignment, Session


def patient_or_404(db, patient_id):
    row = db.get(PatientRecord, patient_id)
    if row is None:
        raise HTTPException(404, detail={"code": "PATIENT_NOT_FOUND", "message": "Patient was not found."})
    return row


def view(row):
    return Patient.model_validate({**json.loads(row.intake_json), "name": row.name,
        "id": row.id, "created_at": row.created_at, "updated_at": row.updated_at})


def build_patient_router(db_dependency):
    router = APIRouter(prefix="/api/v1")

    @router.get("/patients", response_model=list[Patient])
    def patients(db=Depends(db_dependency)):
        return [view(row) for row in db.scalars(select(PatientRecord).order_by(PatientRecord.name, PatientRecord.id))]

    @router.post("/patients", response_model=Patient, status_code=201)
    def create(payload: PatientInput, db=Depends(db_dependency)):
        row = PatientRecord(id=f"patient-{uuid.uuid4().hex}", name=payload.name,
            intake_json=payload.model_dump_json(), created_at=utc_now(), updated_at=utc_now())
        db.add(row)
        db.commit()
        db.refresh(row)
        return view(row)

    @router.get("/patients/{patient_id}", response_model=Patient)
    def get(patient_id: str, db=Depends(db_dependency)):
        return view(patient_or_404(db, patient_id))

    @router.put("/patients/{patient_id}", response_model=Patient)
    def edit(patient_id: str, payload: PatientInput, db=Depends(db_dependency)):
        row = patient_or_404(db, patient_id)
        row.name, row.intake_json, row.updated_at = payload.name, payload.model_dump_json(), utc_now()
        db.commit()
        db.refresh(row)
        return view(row)

    @router.get("/patients/{patient_id}/sessions", response_model=list[Session])
    def sessions(patient_id: str, db=Depends(db_dependency)):
        patient_or_404(db, patient_id)
        rows = db.execute(select(SessionRecord, NoteDraftRecord.workflow_status)
            .outerjoin(NoteDraftRecord, NoteDraftRecord.session_id == SessionRecord.id)
            .where(SessionRecord.patient_id == patient_id).order_by(SessionRecord.created_at.desc())).all()
        return [{**serialize_record(row), "workflow_status": status or row.workflow_status} for row, status in rows]

    @router.patch("/sessions/{session_id}/patient", response_model=Session)
    def assign(session_id: str, payload: PatientAssignment, db=Depends(db_dependency)):
        patient_or_404(db, payload.patient_id)
        row = db.get(SessionRecord, session_id)
        if row is None:
            raise HTTPException(404, detail={"code": "SESSION_NOT_FOUND", "message": "Session was not found."})
        if row.patient_id and row.patient_id != payload.patient_id:
            raise HTTPException(409, detail={"code": "PATIENT_ALREADY_ASSIGNED", "message": "This session already belongs to another patient."})
        row.patient_id = payload.patient_id
        db.commit()
        return serialize_record(row)

    return router

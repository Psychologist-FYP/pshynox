"""Fictional fixtures for progress-monitoring tests (no real patient data)."""
import json
from datetime import timedelta
from uuid import uuid4

from services.api.db import (
    ConditionPredictionRecord, CriterionFindingRecord, DomainObservationRecord, InstrumentScoreRecord, NoteDraftRecord,
    RiskStatementRecord, SessionRecord, TranscriptSegmentRecord, utc_now,
)
from services.api.main import session_factory
from services.progress import store
from services.progress.registry import registry


def make_patient(client, sessions=3, name="Fictional Progress Patient"):
    patient = client.post("/api/v1/patients", json={"name": name}).json()
    ids = []
    for index in range(sessions):
        session = client.post("/api/v1/sessions", json={"patient_id": patient["id"], "session_type": "FOLLOW_UP", "consent": {}}).json()
        ids.append(session["id"])
    # Deterministic chronological order regardless of request timing.
    with session_factory() as db:
        base = utc_now() - timedelta(days=30)
        for index, sid in enumerate(ids):
            db.get(SessionRecord, sid).created_at = base + timedelta(days=7 * index)
        db.commit()
    return patient["id"], ids


def add_segment(session_id, text, role="CLIENT", start_ms=0):
    with session_factory() as db:
        row = TranscriptSegmentRecord(id=f"pseg-{uuid4().hex[:12]}", session_id=session_id, start_ms=start_ms,
                                      end_ms=start_ms + 1000, speaker_role=role, text=text, revision=1)
        db.add(row)
        db.commit()
        return row.id


def add_ai_observation(patient_id, session_id, domain_key, rating, segment_ids, status="rated", timeframe="current"):
    with session_factory() as db:
        now = utc_now()
        rec = DomainObservationRecord(
            id=store.new_id("dobs"), patient_id=patient_id, session_id=session_id, domain_key=domain_key,
            registry_version=registry().tag("domains"), rating=rating, status_value=status, direction=None, timeframe=timeframe,
            frequency_text="", intensity_text="", duration_text="", basis="Client described the domain.",
            evidence_segment_ids_json=json.dumps(segment_ids), provenance="AI_GENERATED", review_status="pending",
            original_ai_json=json.dumps({"rating": rating, "status_value": status}), source_revisions_json="{}",
            created_at=now, updated_at=now)
        db.add(rec)
        db.flush()
        rec.source_revisions_json = json.dumps(store.write_links(db, "domain_observation", rec.id, segment_ids))
        db.commit()
        return rec.id


def add_ai_score(patient_id, session_id, items, segment_ids=(), instrument_key="phq9"):
    instrument = registry().instruments.get(instrument_key)
    with session_factory() as db:
        now = utc_now()
        rows = [{"item_id": i.id, "score": items.get(i.id), "evidence_segment_ids": list(segment_ids) if items.get(i.id) is not None else [],
                 "basis": "", "status": "rated" if items.get(i.id) is not None else "insufficient_evidence"} for i in instrument.items]
        rec = InstrumentScoreRecord(id=store.new_id("iscore"), patient_id=patient_id, session_id=session_id,
                                    instrument_key=instrument_key, registry_version=registry().tag("instruments"),
                                    provenance="AI_GENERATED", review_status="pending", is_active=True,
                                    original_ai_json=json.dumps({"items": rows}), source_revisions_json="{}", created_at=now, updated_at=now)
        store.apply_score_items(rec, instrument, rows)
        db.add(rec)
        db.flush()
        store.write_links(db, "instrument_score", rec.id, list(segment_ids))
        db.commit()
        return rec.id


def add_prediction(patient_id, as_of, condition="depressive_episode", label="possible", segment_ids=(), probability=None):
    with session_factory() as db:
        now = utc_now()
        rec = ConditionPredictionRecord(
            id=store.new_id("pred"), patient_id=patient_id, as_of_session_id=as_of, condition_key=condition, subtype_key=None,
            prediction=label, probability=probability, criteria_met_count=1, criteria_required=5, duration_status="unknown",
            impairment_status="unknown", rule_outs_json="[]", differentials_json="[]", missing_information_json="[]",
            rationale_json=json.dumps([{"sentence": "Low mood was reported.", "segment_ids": list(segment_ids)}]) if segment_ids else "[]",
            postprocess_json="{}", provenance="AI_GENERATED", review_status="pending", is_active=True,
            original_ai_json=json.dumps({"prediction": label}), source_revisions_json="{}", created_at=now, updated_at=now)
        db.add(rec)
        db.flush()
        if segment_ids:
            db.add(CriterionFindingRecord(id=store.new_id("crit"), prediction_id=rec.id, criterion_id="mde.depressed_mood", status="met",
                                          evidence_json=json.dumps([{"segment_id": s, "basis": "Client reported low mood."} for s in segment_ids]),
                                          counter_evidence_json="[]", timeframe="current", duration_note=""))
            db.flush()
            store.write_links(db, "prediction", rec.id, store.segments_of(db, rec))
        db.commit()
        return rec.id


def add_risk(patient_id, session_id, segment_ids):
    with session_factory() as db:
        now = utc_now()
        rec = RiskStatementRecord(id=store.new_id("risk"), patient_id=patient_id, session_id=session_id,
                                  segment_ids_json=json.dumps(segment_ids), category="self_harm", basis="Client described a statement for review.",
                                  provenance="AI_GENERATED", review_status="pending", is_active=True, original_ai_json="{}",
                                  source_revisions_json="{}", created_at=now, updated_at=now)
        db.add(rec)
        db.flush()
        store.write_links(db, "risk_statement", rec.id, segment_ids)
        db.commit()
        return rec.id


def decide_note(session_id, status="APPROVED"):
    with session_factory() as db:
        now = utc_now()
        note = db.query(NoteDraftRecord).filter_by(session_id=session_id).one_or_none()
        if note is None:
            note = NoteDraftRecord(id=f"note-{uuid4().hex[:12]}", session_id=session_id, fields_json="[]",
                                   created_at=now, updated_at=now)
            db.add(note)
        note.workflow_status = status
        db.commit()

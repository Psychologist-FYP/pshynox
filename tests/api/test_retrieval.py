from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from services.api import main
from services.api.db import AuditEventRecord, NoteDraftRecord, SessionRecord, TranscriptSegmentRecord, PatientRecord
from services.retrieval.config import RetrievalSettings
from services.retrieval.engine import EvidenceRetrievalProvider


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setattr(main, "retrieval_provider", EvidenceRetrievalProvider(RetrievalSettings()))
    patient_ids, session_ids = [], []
    with TestClient(main.app) as client:
        for text in ("I sleep four hours because of night shifts.", "Another patient's private headache."):
            patient = client.post("/api/v1/patients", json={"name": f"Fictional {uuid4().hex}"}).json()
            patient_ids.append(patient["id"])
            session = client.post("/api/v1/sessions", json={"patient_id": patient["id"], "session_type": "FOLLOW_UP", "consent": {}}).json()
            session_ids.append(session["id"])
            with main.session_factory() as db:
                db.add(TranscriptSegmentRecord(id=f"{session['id']}-turn", session_id=session["id"],
                                               start_ms=1000, end_ms=2000, text=text, speaker_role="CLIENT", revision=1))
                db.commit()
        yield client, session_ids, patient_ids
    with main.session_factory() as db:
        for model in (AuditEventRecord, NoteDraftRecord, TranscriptSegmentRecord, SessionRecord):
            field = model.id if model is SessionRecord else model.session_id
            db.query(model).filter(field.in_(session_ids)).delete(synchronize_session=False)
        db.query(PatientRecord).filter(PatientRecord.id.in_(patient_ids)).delete(synchronize_session=False)
        db.commit()


def test_evidence_api_enforces_patient_session_and_metadata_filters(workspace):
    client, sessions, patients = workspace
    url = f"/api/v1/sessions/{sessions[0]}/evidence"
    result = client.get(url, params={"query": "sleep", "patient_id": patients[0]}).json()
    assert len(result) == 1
    assert result[0]["patient_id"] == patients[0]
    assert result[0]["speaker_role"] == "CLIENT"
    assert result[0]["sources"][0]["text"] == result[0]["excerpt"]
    assert client.get(url, params={"query": "headache"}).json() == []
    assert client.get(url, params={"query": "sleep", "patient_id": patients[1]}).status_code == 404
    assert client.get(url, params={"query": "sleep", "speaker_role": "CLINICIAN"}).json() == []
    assert client.get(url, params={"query": "sleep", "start_ms": 2500}).json() == []
    assert client.get(url, params={"query": "sleep", "recording_id": "unrelated"}).json() == []
    assert client.get(url, params={"query": '"four hours"', "section": "plan"}).json()[0]["section"] == "plan"


def test_edit_invalidates_index_and_keeps_note_staleness(workspace):
    client, sessions, _ = workspace
    sid = sessions[0]
    url = f"/api/v1/sessions/{sid}/evidence"
    first = client.get(url, params={"query": "sleep"}).json()[0]
    client.post(f"/api/v1/sessions/{sid}/note-draft/populate")
    edited = client.patch(f"/api/v1/sessions/{sid}/transcript/{sid}-turn",
                          json={"text": "I now enjoy exercise.", "speaker_role": "CLINICIAN"})
    assert edited.status_code == 200
    assert edited.json()["revision"] == 2
    assert client.get(url, params={"query": "sleep"}).json() == []
    current = client.get(url, params={"query": "exercise"}).json()[0]
    assert current["transcript_revision"] != first["transcript_revision"]
    assert current["sources"][0]["revision"] == 2
    assert current["speaker_role"] == "CLINICIAN"
    assert client.get(f"/api/v1/sessions/{sid}/note-draft").json()["sources_stale"] is True


@pytest.mark.parametrize("params", [{"query": '"unclosed'}, {"query": "sleep AND"}, {"k": 0},
                                        {"start_ms": 5, "end_ms": 1}, {"speaker_role": "INVALID"}])
def test_invalid_search_returns_validation_error(workspace, params):
    client, sessions, _ = workspace
    assert client.get(f"/api/v1/sessions/{sessions[0]}/evidence", params=params).status_code == 422


def test_empty_query_still_checks_session_exists(workspace):
    client, _, _ = workspace
    assert client.get("/api/v1/sessions/unknown/evidence").status_code == 404

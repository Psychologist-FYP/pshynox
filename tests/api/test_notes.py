from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.db import NoteDraftRecord, SessionRecord, TranscriptSegmentRecord
from services.api.main import app, session_factory


def test_note_draft_saves_evidence_and_reopens() -> None:
    with TestClient(app) as client:
        session = client.post(
            "/api/v1/sessions",
            json={"pseudonym_id": f"note-{uuid4().hex[:8]}", "session_type": "FOLLOW_UP", "consent": {}},
        ).json()
        draft = client.get(f"/api/v1/sessions/{session['id']}/note-draft").json()
        segment = client.get(f"/api/v1/sessions/{session['id']}/transcript").json()[0]
        fields = draft["fields"]
        fields[0].update({"text": "Clinician-reviewed fictional note.", "provenance": "MANUAL", "evidence_ids": [segment["id"]]})
        saved = client.put(f"/api/v1/sessions/{session['id']}/note-draft", json={"fields": fields})
        assert saved.status_code == 200
        reopened = client.get(f"/api/v1/sessions/{session['id']}/note-draft")
        assert reopened.json()["fields"][0]["text"] == "Clinician-reviewed fictional note."
        assert reopened.json()["fields"][0]["evidence_ids"] == [segment["id"]]
        assert reopened.json()["fields"][0]["provenance"] == "MANUAL"
        assert reopened.json()["fields"][0]["review_status"] == "PENDING"
        created_id = session["id"]

    db = session_factory()
    try:
        db.query(NoteDraftRecord).filter_by(session_id=created_id).delete()
        db.query(TranscriptSegmentRecord).filter_by(session_id=created_id).delete()
        db.query(SessionRecord).filter_by(id=created_id).delete()
        db.commit()
    finally:
        db.close()


def test_note_rejects_invalid_sections_and_cross_session_evidence() -> None:
    with TestClient(app) as client:
        first = client.post("/api/v1/sessions", json={"pseudonym_id": f"a-{uuid4().hex[:6]}", "session_type": "FOLLOW_UP", "consent": {}}).json()
        second = client.post("/api/v1/sessions", json={"pseudonym_id": f"b-{uuid4().hex[:6]}", "session_type": "FOLLOW_UP", "consent": {}}).json()
        first_draft = client.get(f"/api/v1/sessions/{first['id']}/note-draft").json()
        other_segment = client.get(f"/api/v1/sessions/{second['id']}/transcript").json()[0]["id"]
        fields = first_draft["fields"]
        fields[0]["section"] = "diagnosis"
        invalid_section = client.put(f"/api/v1/sessions/{first['id']}/note-draft", json={"fields": fields})
        fields[0]["section"] = "subjective"
        fields[0]["evidence_ids"] = [other_segment]
        cross_session = client.put(f"/api/v1/sessions/{first['id']}/note-draft", json={"fields": fields})
        assert invalid_section.status_code == 422
        assert cross_session.status_code == 422
        ids = [first["id"], second["id"]]

    db = session_factory()
    try:
        db.query(NoteDraftRecord).filter(NoteDraftRecord.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(TranscriptSegmentRecord).filter(TranscriptSegmentRecord.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(SessionRecord).filter(SessionRecord.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

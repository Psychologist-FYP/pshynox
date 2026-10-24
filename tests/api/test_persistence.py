from tests.api.helpers import seed_segment
from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.db import SessionRecord, TranscriptSegmentRecord
from services.api.main import app, session_factory


def test_create_list_edit_and_reopen_persisted_session() -> None:
    pseudonym = f"client-test-{uuid4().hex[:8]}"
    with TestClient(app) as test_client:
        created = test_client.post(
            "/api/v1/sessions",
            json={
                "pseudonym_id": pseudonym,
                "session_type": "FOLLOW_UP",
                "consent": {"recording_consent": False, "fictional_fixture": True},
            },
        )
        assert created.status_code == 201
        session = created.json()
        assert session["workflow_status"] == "DRAFT"

        listed = test_client.get("/api/v1/sessions")
        assert any(item["id"] == session["id"] for item in listed.json())

        seed_segment(session["id"])
        transcript = test_client.get(f"/api/v1/sessions/{session['id']}/transcript")
        assert transcript.status_code == 200
        segment = transcript.json()[0]
        edited = test_client.patch(
            f"/api/v1/sessions/{session['id']}/transcript/{segment['id']}",
            json={"text": "Corrected fictional text.", "speaker_role": "CLINICIAN"},
        )
        assert edited.status_code == 200
        assert edited.json()["revision"] == segment["revision"] + 1
        assert edited.json()["speaker_role"] == "CLINICIAN"

        reopened = test_client.get(f"/api/v1/sessions/{session['id']}/transcript")
        saved = next(item for item in reopened.json() if item["id"] == segment["id"])
        assert saved["text"] == "Corrected fictional text."
        assert saved["revision"] == edited.json()["revision"]

    db = session_factory()
    try:
        db.query(TranscriptSegmentRecord).filter_by(session_id=session["id"]).delete()
        db.query(SessionRecord).filter_by(id=session["id"]).delete()
        db.commit()
    finally:
        db.close()


def test_invalid_role_and_cross_session_segment_edit_are_rejected() -> None:
    with TestClient(app) as test_client:
        first = test_client.post(
            "/api/v1/sessions",
            json={"pseudonym_id": f"client-{uuid4().hex[:8]}", "session_type": "FOLLOW_UP", "consent": {}},
        ).json()
        second = test_client.post(
            "/api/v1/sessions",
            json={"pseudonym_id": f"client-{uuid4().hex[:8]}", "session_type": "FOLLOW_UP", "consent": {}},
        ).json()
        seed_segment(first["id"])
        segment = test_client.get(f"/api/v1/sessions/{first['id']}/transcript").json()[0]

        invalid = test_client.patch(
            f"/api/v1/sessions/{first['id']}/transcript/{segment['id']}",
            json={"speaker_role": "SYSTEM"},
        )
        cross_session = test_client.patch(
            f"/api/v1/sessions/{second['id']}/transcript/{segment['id']}",
            json={"text": "Must not cross session boundaries."},
        )
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
        assert cross_session.status_code == 404

    db = session_factory()
    try:
        db.query(TranscriptSegmentRecord).filter(
            TranscriptSegmentRecord.session_id.in_([first["id"], second["id"]])
        ).delete(synchronize_session=False)
        db.query(SessionRecord).filter(SessionRecord.id.in_([first["id"], second["id"]])).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

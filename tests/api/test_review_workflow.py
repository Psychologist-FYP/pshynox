from uuid import uuid4

from fastapi.testclient import TestClient

from services.api.db import AuditEventRecord, NoteDraftRecord, SessionRecord, TranscriptSegmentRecord
from services.api.main import app, session_factory


def _session(client: TestClient) -> dict:
    return client.post(
        "/api/v1/sessions",
        json={"pseudonym_id": f"review-{uuid4().hex[:8]}", "session_type": "FOLLOW_UP", "consent": {}},
    ).json()


def _cleanup(session_id: str) -> None:
    db = session_factory()
    try:
        db.query(AuditEventRecord).filter_by(session_id=session_id).delete()
        db.query(NoteDraftRecord).filter_by(session_id=session_id).delete()
        db.query(TranscriptSegmentRecord).filter_by(session_id=session_id).delete()
        db.query(SessionRecord).filter_by(id=session_id).delete()
        db.commit()
    finally:
        db.close()


def test_approval_requires_draft_and_completed_review() -> None:
    with TestClient(app) as client:
        session = _session(client)
        blocked_no_draft = client.post(f"/api/v1/sessions/{session['id']}/approve", json={"actor_name": "Dr Demo"})
        assert blocked_no_draft.status_code == 409
        assert blocked_no_draft.json()["error"]["code"] == "NOTE_DRAFT_REQUIRED"
        client.get(f"/api/v1/sessions/{session['id']}/note-draft")
        blocked_pending = client.post(f"/api/v1/sessions/{session['id']}/approve", json={"actor_name": "Dr Demo"})
        assert blocked_pending.status_code == 409
        assert blocked_pending.json()["error"]["code"] == "RISK_REVIEW_REQUIRED"
        state = client.get(f"/api/v1/sessions/{session['id']}/review-state").json()
        assert state["workflow_status"] == "DRAFT"
        assert state["risk_review_status"] == "PENDING"
    _cleanup(session["id"])


def test_review_approval_and_audit_are_persisted() -> None:
    with TestClient(app) as client:
        session = _session(client)
        client.get(f"/api/v1/sessions/{session['id']}/note-draft")
        missing_confirmation = client.post(
            f"/api/v1/sessions/{session['id']}/risk-review/complete",
            json={"reviewer_name": "Dr Demo", "confirmation": False},
        )
        assert missing_confirmation.status_code == 422
        reviewed = client.post(
            f"/api/v1/sessions/{session['id']}/risk-review/complete",
            json={"reviewer_name": "Dr Demo", "confirmation": True, "review_note": "Reviewed context manually."},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["risk_review_status"] == "COMPLETED"
        approved = client.post(f"/api/v1/sessions/{session['id']}/approve", json={"actor_name": "Dr Demo"})
        assert approved.status_code == 200
        assert approved.json()["workflow_status"] == "APPROVED"
        events = client.get(f"/api/v1/sessions/{session['id']}/audit-events").json()
        assert [event["event_type"] for event in events][-2:] == ["RISK_REVIEW_COMPLETED", "NOTE_APPROVED"]
        assert all("text" not in event["metadata"] for event in events)
        terminal_edit = client.put(
            f"/api/v1/sessions/{session['id']}/note-draft",
            json={"fields": client.get(f"/api/v1/sessions/{session['id']}/note-draft").json()["fields"]},
        )
        assert terminal_edit.status_code == 409
    _cleanup(session["id"])


def test_rejection_requires_reason_and_is_terminal() -> None:
    with TestClient(app) as client:
        session = _session(client)
        client.get(f"/api/v1/sessions/{session['id']}/note-draft")
        missing_reason = client.post(f"/api/v1/sessions/{session['id']}/reject", json={"actor_name": "Dr Demo", "reason": " "})
        assert missing_reason.status_code == 422
        rejected = client.post(f"/api/v1/sessions/{session['id']}/reject", json={"actor_name": "Dr Demo", "reason": "Needs clinician revision."})
        assert rejected.status_code == 200
        assert rejected.json()["workflow_status"] == "REJECTED"
        events = client.get(f"/api/v1/sessions/{session['id']}/audit-events").json()
        assert events[-1]["metadata"]["reason"] == "Needs clinician revision."
        assert client.post(f"/api/v1/sessions/{session['id']}/reject", json={"actor_name": "Dr Demo", "reason": "Again"}).status_code == 409
    _cleanup(session["id"])

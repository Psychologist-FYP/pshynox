from fastapi.testclient import TestClient

from services.api.main import app

client = TestClient(app)


def test_health_is_local_and_explicit_about_fixture_mode() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["fixture_mode"] is True


def test_fixture_session_and_transcript_are_available() -> None:
    session = client.get("/api/v1/sessions/session-demo-001")
    transcript = client.get("/api/v1/sessions/session-demo-001/transcript")
    assert session.status_code == 200
    assert session.json()["pseudonym_id"] == "client-demo-001"
    assert transcript.status_code == 200
    assert len(transcript.json()) == 2


def test_missing_session_returns_consistent_error() -> None:
    response = client.get("/api/v1/sessions/missing")
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "SESSION_NOT_FOUND",
            "message": "Session was not found.",
        }
    }


def test_empty_evidence_query_does_not_claim_safety() -> None:
    response = client.get("/api/v1/sessions/session-demo-001/evidence")
    assert response.status_code == 200
    assert response.json() == []

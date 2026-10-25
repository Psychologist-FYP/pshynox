from fastapi.testclient import TestClient

from services.api.main import app


def test_soap_preview_is_separate_and_not_persisted() -> None:
    with TestClient(app) as client:
        response = client.get("/api/browse/soap-preview/session-soap-demo-001")

    assert response.status_code == 200
    body = response.json()
    assert body["preview_only"] is True
    assert body["draft"]["fields"][0]["evidence_ids"] == ["seg-soap-001", "seg-soap-003"]
    assert body["draft"]["fields"][2]["text"] == ""
    assert body["risk_review"]["status"] == "PENDING"
    assert body["risk_review"]["candidates"][0]["label"] == "search candidates, not a risk assessment"


def test_unknown_soap_preview_returns_not_found() -> None:
    with TestClient(app) as client:
        response = client.get("/api/browse/soap-preview/missing")

    assert response.status_code == 404

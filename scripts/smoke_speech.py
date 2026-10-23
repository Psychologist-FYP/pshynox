"""Real model + worker + SQLite integration smoke test in a temporary workspace.

Run from the repository root: .venv/bin/python scripts/smoke_speech.py path/to/mono.wav
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    audio = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="pshynox-real-smoke-") as temp:
        os.environ.update(DATABASE_URL=f"sqlite:///{temp}/smoke.db", LOCAL_DATA_DIR=temp,
                          SPEECH_WORKER_ENABLED="true", FIXTURE_MODE="false")
        from fastapi.testclient import TestClient
        from services.api.main import app
        with TestClient(app) as client:
            response = client.post("/api/v1/sessions", json={"session_type": "FOLLOW_UP", "consent": {"recording_consent": True}})
            assert response.status_code == 201, response.text
            sid = response.json()["id"]
            response = client.post(f"/api/v1/sessions/{sid}/recordings", content=audio.read_bytes(), headers={"Content-Type": "audio/wav"})
            assert response.status_code == 201, response.text
            rid = response.json()["id"]
            response = client.post(f"/api/v1/sessions/{sid}/transcribe", json={"recording_id": rid, "idempotency_key": "real-model-smoke"})
            assert response.status_code == 202, response.text
            jid = response.json()["id"]
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                job = client.get(f"/api/v1/jobs/{jid}").json()
                if job["status"] in ("FAILED", "SUCCEEDED"):
                    break
                time.sleep(.25)
            assert job["status"] == "SUCCEEDED", job
            segments = client.get(f"/api/v1/sessions/{sid}/transcript").json()
            assert segments, "Use a sample containing speech."
            for segment in segments:
                response = client.patch(f"/api/v1/sessions/{sid}/transcript/{segment['id']}", json={"speaker_role": "CLIENT"})
                assert response.status_code == 200, response.text
            draft = client.post(f"/api/v1/sessions/{sid}/note-draft/populate").json()
            assert draft["fields"][0]["evidence_ids"]
        with TestClient(app) as client:
            assert len(client.get(f"/api/v1/sessions/{sid}/transcript").json()) == len(segments)
            reopened = client.get(f"/api/v1/sessions/{sid}/note-draft").json()
            assert reopened["id"] == draft["id"]
            assert reopened["fields"] == draft["fields"]
            assert reopened["risk_review_status"] == "PENDING"
        print(json.dumps({"result": "passed", "segment_count": len(segments), "worker_metrics": job["metrics"], "restart_persistence": True}, indent=2))


if __name__ == "__main__":
    main()

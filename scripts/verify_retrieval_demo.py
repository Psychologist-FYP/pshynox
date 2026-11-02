"""Read-only BM25 API checks against the seeded fictional patient.

Run from the repository root: .venv/bin/python scripts/verify_retrieval_demo.py
Checks assume the initial demo text has not been edited.
"""
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["SPEECH_WORKER_ENABLED"] = "false"

from fastapi.testclient import TestClient
from scripts.seed_retrieval_demo import PATIENT_ID
from services.api import main
from services.retrieval.config import RetrievalSettings
from services.retrieval.engine import EvidenceRetrievalProvider


def verify():
    # Configure this in-process verifier only; no running API is reconfigured.
    main.retrieval_provider = EvidenceRetrievalProvider(RetrievalSettings(method="bm25"))
    client = TestClient(main.app, client=("127.0.0.1", 50000))
    patient = client.get(f"/api/v1/patients/{PATIENT_ID}")
    assert patient.status_code == 200, f"Demo lookup failed ({patient.status_code}). Run scripts/seed_retrieval_demo.py first."
    first, second = "session-retrieval-demo-001", "session-retrieval-demo-002"
    checks = [
        (first, '"four hours"', ["002"]),
        (first, "anxious", ["005"]),
        (first, "anxious AND NOT anymore", []),
        (first, '"sister has depression"', ["006"]),
        (first, "sleeping", ["003"]),
        (first, "neend AND nahi", ["011"]),
        (first, "نیند", ["012"]),
        (first, '"breathing exercise"', ["013"]),
        (first, '"follow up"', ["015"]),
        (first, "hallucinations", []),
        (first, "ink", []),
        (first, "watercolour", []),
        (second, "watercolour", ["003"]),
        (second, '"six hours"', ["002"]),
    ]
    results = []
    for sid, query, expected in checks:
        started = time.perf_counter()
        response = client.get(f"/api/v1/sessions/{sid}/evidence", params={"query": query, "patient_id": PATIENT_ID})
        assert response.status_code == 200, response.text
        hits = response.json()
        assert {h["segment_id"] for h in hits} == {f"{sid}-turn-{n}" for n in expected}, (query, hits)
        assert all(h["patient_id"] == PATIENT_ID and h["session_id"] == sid for h in hits)
        results.append({"session_id": sid, "query": query, "status": "passed",
                        "elapsed_ms": (time.perf_counter() - started) * 1000,
                        "hits": [{key: hit[key] for key in ("segment_id", "speaker_role", "excerpt", "score", "rank", "source_segment_ids")}
                                 for hit in hits]})
    wrong_patient = client.get(f"/api/v1/sessions/{first}/evidence", params={"query": "sleep", "patient_id": "unrelated-patient"})
    assert wrong_patient.status_code == 404
    client.close()
    return {"patient_id": PATIENT_ID, "method": "bm25", "search_checks_passed": len(results),
            "patient_mismatch_blocked": True, "results": results}


def main_cli():
    result = verify()
    path = ROOT / "data" / "retrieval-demo-checks.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Passed {result['search_checks_passed']} search checks and the patient-boundary check. Results: {path}")


if __name__ == "__main__":
    main_cli()

import json

import pytest

from services.api.db import InstrumentScoreRecord, PatientRecord, SessionRecord, TranscriptSegmentRecord
from services.api.import_longcounsel_demo import import_trajectories, select_transcripts
from services.api.main import session_factory
from tests.progress.evaluation.helpers import EDITION, prepared
from tests.progress.evaluation.test_benchmark import env  # noqa: F401  (fixture)


def test_demo_import_creates_labelled_patients_without_labels_and_blocks_test(tmp_path):
    out = prepared(tmp_path)
    with pytest.raises(SystemExit, match="blocked"):
        select_transcripts("test", 5, EDITION, out)
    rows = select_transcripts("validation", 1, EDITION, out)
    assert [r["run_id"] for r in rows] == ["mini_a"]
    with session_factory() as db:
        counts = import_trajectories(db, rows, EDITION)
        assert counts == {"patients_created": 1, "sessions_created": 5, "turns_created": 40, "trajectories_skipped": 0}
        patient = db.get(PatientRecord, "lc8-demo-patient-mini_a")
        assert patient.name == "LongCounsel demo · mini_a" and "Not a real patient" in json.loads(patient.intake_json)["additional_notes"]
        sessions = db.query(SessionRecord).filter_by(patient_id=patient.id).order_by(SessionRecord.created_at).all()
        assert [s.id for s in sessions] == [f"lc8-demo-mini_a-s{i}" for i in range(1, 6)]
        consent = json.loads(sessions[0].consent_json)
        assert consent["research_demo"] and consent["dataset"] == "longcounsel-8" and consent["source_split"] == "val"
        texts = " ".join(t.text for t in db.query(TranscriptSegmentRecord).filter(TranscriptSegmentRecord.session_id.like("lc8-demo-mini_a-%")))
        assert "phq_score" not in texts and db.query(InstrumentScoreRecord).filter_by(patient_id=patient.id).count() == 0
        assert import_trajectories(db, rows, EDITION)["trajectories_skipped"] == 1     # never overwrites
        test_row = {**rows[0], "split": "test"}
        with pytest.raises(ValueError, match="blocked"):
            import_trajectories(db, [test_row], EDITION)


def test_demo_import_with_benchmark_results_populates_the_dashboard(env):
    from fastapi.testclient import TestClient
    from services.api.import_longcounsel_demo import attach_benchmark_results, run_trajectories
    from services.api.main import app
    from tests.progress.evaluation.test_benchmark import run
    assert run(env, "retrieval-current", "rc") == 0
    rows = run_trajectories("rc", env["runs"], EDITION, env["prepared"])
    with TestClient(app) as client:
        with session_factory() as db:
            import_trajectories(db, rows, EDITION)
            counts = attach_benchmark_results(db, "rc", env["runs"], env["prepared"])
        assert counts["sessions_with_results"] == 10 and counts["predictions_saved"] == 10 and counts["sessions_missing_patient"] == 0
        pid = "lc8-demo-patient-mini_a"
        sessions = client.get(f"/api/v1/patients/{pid}/sessions")                # the patient page's session list
        assert sessions.status_code == 200 and len(sessions.json()) == 5, sessions.text
        progress = client.get(f"/api/v1/patients/{pid}/progress").json()
        score = next(s for s in progress["instrument_scores"] if s["instrument_key"] == "phq9" and s["session_id"] == "lc8-demo-mini_a-s1")
        assert score["provenance"] == "AI_GENERATED" and score["review_status"] == "pending"
        item = next(i for i in score["items"] if i["item_id"] == "2")
        assert item["score"] == 2 and item["evidence_segment_ids"] and all(s.startswith("lc8-demo-") for s in item["evidence_segment_ids"])
        preds = client.get(f"/api/v1/patients/{pid}/condition-predictions").json()
        first = preds[0] if isinstance(preds, list) else preds
        assert "depressive_episode" in json.dumps(first) and "Imported from benchmark run rc" in json.dumps(preds)
        with session_factory() as db:                                            # rerun never overwrites
            again = attach_benchmark_results(db, "rc", env["runs"], env["prepared"])
        assert again["sessions_with_results"] == 0 and again["sessions_skipped_existing"] == 10 and again["predictions_saved"] == 0

"""Stage A → Stage B jobs end to end with a scripted runtime (fictional data)."""
import pytest
from fastapi.testclient import TestClient

from services.api.main import app
from services.progress import jobs
from tests.progress.fake_runtime import FakeRuntime, scripted
from tests.progress.helpers import add_segment, make_patient
from tests.progress.test_jobs_staleness import wait
from tests.progress.test_prediction import stage_b

API = "/api/v1"


@pytest.fixture
def runtime(monkeypatch):
    holder = {"a": scripted(), "b": stage_b({}), "sha": "f" * 64}
    def respond(system, data):
        return holder["b"](system, data) if "condition" in data else holder["a"](system, data)
    monkeypatch.setattr(jobs, "RUNTIME_FACTORY", lambda folder, cancel: FakeRuntime(respond, sha=holder["sha"]))
    return holder


def test_extraction_runs_stage_b_and_stores_only_cited_hypotheses(runtime):
    with TestClient(app) as client:
        pid, (s1, s2) = make_patient(client, sessions=2)
        add_segment(s1, "I have felt down and empty most days for weeks.")
        add_segment(s2, "Nothing interests me anymore and I stopped seeing friends.")
        runtime["a"] = scripted(domains={"interest": {"rating": 3, "refs_text": ["Nothing interests me"]}})
        runtime["b"] = stage_b({"mde.depressed_mood": ("met", ["felt down"]), "mde.anhedonia": ("met", ["Nothing interests me"])},
                               rationale=[("Low mood and loss of interest were reported.", ["felt down", "Nothing interests me"])], only="depressive_episode")
        r = client.post(f"{API}/sessions/{s2}/progress-extraction", json={"transcript_reviewed": True})
        job = wait(client, r.json()["job_id"])
        assert job["status"] == "completed", job
        # Conditions where the model cites nothing store no hypothesis (principle 2); the gap is a visible limitation.
        assert job["counts"]["predictions_saved"] == 1 and job["counts"]["predictions_without_evidence"] == 3
        assert any("gad: no cited evidence" in l for l in job["limitations"])
        progress = client.get(f"{API}/patients/{pid}/progress").json()
        pred = next(p for p in progress["predictions"] if p["condition_key"] == "depressive_episode")
        assert pred["prediction"] == "possible" and pred["review_status"] == "pending" and pred["as_of_session_id"] == s2
        assert pred["criteria_met_count"] == 2 and pred["criteria_required"] == 5
        assert pred["probability"] is None                                  # clinical patient: research-only calibration (Q3)
        detail = client.get(f"{API}/patients/{pid}/condition-predictions/{pred['id']}").json()
        met = {f["criterion_id"]: f for f in detail["findings"] if f["status"] == "met"}
        assert set(met) == {"mde.depressed_mood", "mde.anhedonia"}
        assert met["mde.depressed_mood"]["evidence"][0]["session_id"] == s1 and met["mde.depressed_mood"]["evidence"][0]["text"]
        assert detail["missing_information"] == ["Has the low mood lasted at least two weeks?"]
        assert detail["prompt_version"] == "condition-predict-v1" and detail["registry_version"].startswith("conditions@")
        timeline = next(t for t in progress["prediction_timeline"] if t["condition_key"] == "depressive_episode")
        assert [p.get("prediction") for p in timeline["points"]] == [None, "possible"]


def test_on_demand_prediction_supersedes_and_keeps_history(runtime):
    with TestClient(app) as client:
        pid, (s1,) = make_patient(client, sessions=1)
        add_segment(s1, "I have felt down and empty most days for weeks.")
        runtime["b"] = stage_b({"mde.depressed_mood": ("met", ["felt down"])})
        assert client.post(f"{API}/patients/{pid}/condition-predictions", json={"as_of_session_id": s1, "condition_keys": ["bulimia_nervosa"]}).status_code == 422
        other, (o1,) = make_patient(client, sessions=1, name="Other Fictional Patient")
        assert client.post(f"{API}/patients/{pid}/condition-predictions", json={"as_of_session_id": o1}).status_code == 404
        for sha in ("f" * 64, "e" * 64):
            runtime["sha"] = sha
            r = client.post(f"{API}/patients/{pid}/condition-predictions", json={"as_of_session_id": s1, "condition_keys": ["depressive_episode"]})
            assert r.status_code == 202
            assert wait(client, r.json()["job_id"])["status"] == "completed"
        listing = client.get(f"{API}/patients/{pid}/condition-predictions").json()
        assert len(listing["history"]) == 2 and len(listing["latest"]) == 1
        events = [e["event_type"] for e in client.get(f"{API}/patients/{pid}/audit-events").json()]
        assert events.count("PROGRESS_PREDICTION_SAVED") == 2

"""DSM jobs end to end through the API with a scripted runtime (fictional data)."""
import pytest
from fastapi.testclient import TestClient

from services.api.main import app
from services.progress import jobs
from tests.progress.fake_runtime import FakeRuntime
from tests.progress.helpers import add_ai_observation, add_segment, decide_note, make_patient
from tests.progress.test_jobs_staleness import wait

from .fake_dsm import combined, stage_a, stage_b

API = "/api/v1"
A = {"feat.depressed_mood": {"refs_text": ["low and empty"], "contexts": ["low_mood"]},
     "feat.anhedonia": {"refs_text": ["Nothing interests me"]},
     "feat.social_fear_scrutiny": {"status": "explicit_denial", "refs_text": ["not scared of people"]}}
B = {"feat.depressed_mood": {"refs_text": ["low and empty"], "contexts": ["low_mood"]},
     "feat.anhedonia": {"refs_text": ["Nothing interests me"]},
     "feat.social_fear_scrutiny": {"status": "explicit_denial", "refs_text": ["not scared of people"]}}


@pytest.fixture
def runtime(monkeypatch):
    monkeypatch.setenv("PROGRESS_DSM_PIPELINE", "dsm-v1")  # these scripts answer dsm-v1 prompts; dsm-fast has its own tests
    holder = {"respond": combined(stage_a(A, [{"category": "suicidal_ideation_passive", "refs_text": ["better off without me"]}],
                                          items={"phq9:2": {"score": 2, "refs_text": ["low and empty"]}}), stage_b(B, []))}
    monkeypatch.setattr(jobs, "RUNTIME_FACTORY", lambda folder, cancel: FakeRuntime(lambda s, d: holder["respond"](s, d)))
    return holder


def seed(client):
    pid, (s1, s2, s3) = make_patient(client)
    add_segment(s1, "I feel low and empty most days.")
    add_segment(s1, "Nothing interests me any more.", start_ms=2000)
    add_segment(s2, "I'm not scared of people, I'm just tired.")
    add_segment(s2, "Sometimes I think everyone would be better off without me.", start_ms=3000)
    add_segment(s3, "Things are a bit better.")
    return pid, s1, s2, s3


def test_dsm_extraction_saves_findings_risks_questionnaires_and_hypotheses(runtime):
    with TestClient(app) as client:
        pid, s1, s2, _ = seed(client)
        r = client.post(f"{API}/sessions/{s1}/progress-extraction", json={"transcript_reviewed": True})
        job = wait(client, r.json()["job_id"])
        assert job["status"] == "completed", job
        assert job["request"]["registry"] == "dsm" and job["counts"]["findings_saved"] == 46 and job["counts"]["scores_saved"] == 2
        findings = client.get(f"{API}/sessions/{s1}/dsm-findings").json()
        assert {f["feature_key"]: f["status"] for f in findings}["feat.anhedonia"] == "present"
        data = client.get(f"{API}/patients/{pid}/progress").json()
        cards = {p["condition_key"]: p for p in data["dsm"]["predictions"]}
        assert cards["mdd"]["label"] == "possible" and cards["mdd"]["review_status"] == "pending"
        assert cards["mdd"]["footer"] == "Hypothesis for clinician review; not a diagnosis."
        a1 = next(e for e in cards["mde"]["elements"] if e["id"] == "MDE.A")
        cited = a1["items"][0]["evidence"][0]
        assert cited["text"] and cited["session_id"] == s1  # resolved for the two-click path to the turn and audio
        phq = [s for s in data["instrument_scores"] if s["instrument_key"] == "phq9" and s["provenance"] == "AI_GENERATED"]
        assert phq and phq[0]["items"][1]["score"] == 2
        detail = client.get(f"{API}/patients/{pid}/dsm-predictions/{cards['mdd']['id']}").json()
        assert detail["label_text"].startswith("Criteria pattern consistent with major depressive disorder")
        reviewed = client.post(f"{API}/patients/{pid}/dsm-predictions/{cards['mdd']['id']}/review",
                               json={"actor": "Dr Demo", "action": "edit", "value": {"label": "likely"}, "comment": "Confirmed in interview"}).json()
        assert reviewed["label"] == "likely" and reviewed["review_status"] == "edited" and reviewed["original_ai"]["label"] == "possible"


def test_reanalysis_skips_decided_sessions_retires_v1_and_reverts(runtime):
    with TestClient(app) as client:
        pid, s1, s2, s3 = seed(client)
        old = add_ai_observation(pid, s2, "low_mood", 2, [])
        decide_note(s3)
        r = client.post(f"{API}/patients/{pid}/dsm-reanalysis", json={"transcript_reviewed": True})
        assert r.status_code == 202, r.text
        job = wait(client, r.json()["job_id"])
        assert job["status"] == "completed", job
        assert [x["session_id"] for x in job["skipped"]] == [s3]
        data = client.get(f"{API}/patients/{pid}/progress").json()
        assert next(d for d in data["domains"] if d["domain_key"] == "low_mood")["cells"][1]["record_id"] is None
        flags = [x for x in data["risk_statements"] if x["registry_kind"] == "dsm"]
        assert flags and flags[0]["categories"] == ["suicidal_ideation_passive"]
        batch = data["dsm"]["batches"][0]
        assert batch["kind"] == "reanalysis" and batch["session_ids"] == [s1, s2]
        rev = client.post(f"{API}/patients/{pid}/dsm-batches/{batch['id']}/revert", json={"actor": "Dr Demo"}).json()
        assert rev["restored"] >= 1
        data = client.get(f"{API}/patients/{pid}/progress").json()
        assert next(d for d in data["domains"] if d["domain_key"] == "low_mood")["cells"][1]["record_id"] == old
        assert data["dsm"]["predictions"] == [] and client.get(f"{API}/sessions/{s1}/dsm-findings").json() == []


def test_reanalysis_needs_transcript_review_acknowledgement(runtime):
    with TestClient(app) as client:
        pid, *_ = seed(client)
        assert client.post(f"{API}/patients/{pid}/dsm-reanalysis", json={}).status_code == 422

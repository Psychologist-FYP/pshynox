"""dsm-fast through the clinical job API with a scripted runtime (fictional data): persistence, badges, staleness."""
import pytest
from fastapi.testclient import TestClient

from services.api.main import app
from services.progress import jobs
from services.progress.dsm.fast.prompts import group_task
from tests.progress.fake_runtime import FakeRuntime
from tests.progress.helpers import add_segment, make_patient
from tests.progress.test_jobs_staleness import wait

API = "/api/v1"


def find(data, needle):
    return next((int(l.split(" ", 1)[0]) for l in data["turns"] if needle.lower() in l.lower()), None)


def respond(system, data):
    task = data["task"]
    if task.startswith("RISK"):
        i = find(data, "better off without me")
        return {"r": [{"c": "passive", "e": [i], "b": "wishes others were better off"}] if i is not None else []}
    rows = []
    if task.startswith(group_task("depressive").split(":")[0]):
        for code, needle in (("DM", "low and empty"), ("AN", "Nothing interests me")):
            i = find(data, needle)
            if i is not None:
                rows.append({"k": code, "s": "P", "q": "ned", "t": "c", "c": ["m"], "e": [i], "x": [], "a": [], "b": "reported most days"})
    if task.startswith(group_task("social").split(":")[0]):
        i = find(data, "not scared of people")
        if i is not None:
            rows.append({"k": "SF", "s": "D", "q": "-", "t": "c", "c": [], "e": [i], "x": [i], "a": [], "b": "denies fear of people"})
    return {"f": rows}


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setenv("PROGRESS_DSM_PIPELINE", "dsm-fast")
    rt = {}

    def factory(folder, cancel):
        from tests.progress.dsm.fast.test_fast_extract import answered
        rt["runtime"] = FakeRuntime(answered(respond))
        return rt["runtime"]
    monkeypatch.setattr(jobs, "RUNTIME_FACTORY", factory)
    return rt


def test_fast_extraction_saves_findings_risks_derived_questionnaires_and_rule_hypotheses(fast):
    with TestClient(app) as client:
        pid, (s1, s2, _) = make_patient(client)
        add_segment(s1, "I feel low and empty most days.")
        add_segment(s1, "Nothing interests me any more.", start_ms=2000)
        add_segment(s1, "I'm not scared of people, I'm just tired.", start_ms=4000)
        add_segment(s1, "Sometimes I think everyone would be better off without me.", start_ms=6000)
        r = client.post(f"{API}/sessions/{s1}/progress-extraction", json={"transcript_reviewed": True})
        job = wait(client, r.json()["job_id"])
        assert job["status"] == "completed", job
        assert job["request"]["pipeline"] == "dsm-fast" and job["counts"]["findings_saved"] == 46
        assert job["counts"]["stage_b_model_calls"] == 0  # Stage B in code
        tasks = [r["data"]["task"] for r in fast["runtime"].requests]
        assert tasks[0].startswith("RISK")  # risk first, always
        assert any(t.startswith(group_task("elevated").split(":")[0]) for t in tasks)  # a 4-turn session is never gated
        findings = {f["feature_key"]: f for f in client.get(f"{API}/sessions/{s1}/dsm-findings").json()}
        assert findings["feat.anhedonia"]["status"] == "present" and findings["feat.social_fear_scrutiny"]["status"] == "explicit_denial"
        assert findings["feat.elevated_mood"]["status"] == "insufficient_evidence"  # a gap, never "absent"
        assert findings["feat.anhedonia"]["review_status"] == "pending" and findings["feat.anhedonia"]["provenance"] == "AI_GENERATED"
        data = client.get(f"{API}/patients/{pid}/progress").json()
        cards = {p["condition_key"]: p for p in data["dsm"]["predictions"]}
        assert cards["sad"]["label"] == "not_met" and cards["mde"]["prompt_version"] == "condition-predict-dsm-rules-v1"
        assert cards["mde"]["footer"] == "Hypothesis for clinician review; not a diagnosis."
        flags = [x for x in data["risk_statements"] if x["registry_kind"] == "dsm"]
        assert flags and flags[0]["categories"] == ["suicidal_ideation_passive"]
        phq = [s for s in data["instrument_scores"] if s["instrument_key"] == "phq9" and s["provenance"] == "AI_GENERATED"]
        assert phq and phq[0]["items"][0]["score"] == 3 and phq[0]["items"][1]["score"] == 3  # derived from findings
        assert phq[0]["items"][8]["score"] is None  # item 9 never derived


def test_default_pipeline_is_dsm_fast_and_dsm_v1_stays_selectable(monkeypatch):
    monkeypatch.delenv("PROGRESS_DSM_PIPELINE", raising=False)
    from services.progress.dsm.fast.config import dsm_pipeline
    assert dsm_pipeline() == "dsm-fast"
    monkeypatch.setenv("PROGRESS_DSM_PIPELINE", "dsm-v1")
    assert dsm_pipeline() == "dsm-v1"


def test_fast_reanalysis_runs_every_undecided_session_and_reverts(fast):
    from tests.progress.helpers import decide_note
    with TestClient(app) as client:
        pid, (s1, s2, s3) = make_patient(client)
        add_segment(s1, "I feel low and empty most days.")
        add_segment(s2, "Sometimes I think everyone would be better off without me.")
        add_segment(s3, "Things are a bit better.")
        decide_note(s3)
        r = client.post(f"{API}/patients/{pid}/dsm-reanalysis", json={"transcript_reviewed": True})
        job = wait(client, r.json()["job_id"])
        assert job["status"] == "completed", job
        assert job["request"]["pipeline"] == "dsm-fast" and [x["session_id"] for x in job["skipped"]] == [s3]
        data = client.get(f"{API}/patients/{pid}/progress").json()
        assert [x["categories"] for x in data["risk_statements"] if x["registry_kind"] == "dsm"] == [["suicidal_ideation_passive"]]
        assert {p["prompt_version"] for p in data["dsm"]["predictions"]} == {"condition-predict-dsm-rules-v1"}
        batch = data["dsm"]["batches"][0]
        client.post(f"{API}/patients/{pid}/dsm-batches/{batch['id']}/revert", json={"actor": "Dr Demo"})
        assert client.get(f"{API}/sessions/{s1}/dsm-findings").json() == []

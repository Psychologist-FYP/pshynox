"""Stage A jobs end to end with a scripted runtime: persistence rules, locks, split blocking, staleness."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from services.api.db import SessionRecord
from services.api.main import app, session_factory
from services.progress import jobs
from tests.progress.fake_runtime import FakeRuntime, scripted
from tests.progress.helpers import add_segment, decide_note, make_patient

API = "/api/v1"


@pytest.fixture
def responder(monkeypatch):
    holder = {"fn": scripted(), "sha": "f" * 64}
    monkeypatch.setattr(jobs, "RUNTIME_FACTORY", lambda folder, cancel: FakeRuntime(lambda s, d: holder["fn"](s, d), sha=holder["sha"]))
    return holder


def wait(client, job_id, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"{API}/progress-jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def analyse(client, sid, **body):
    r = client.post(f"{API}/sessions/{sid}/progress-extraction", json={"transcript_reviewed": True, "predict": False, **body})
    assert r.status_code == 202, r.text
    return wait(client, r.json()["job_id"])


def test_extraction_requires_review_ack_and_saves_pending_ai_values(responder):
    with TestClient(app) as client:
        pid, (s1, s2, _) = make_patient(client)
        add_segment(s1, "How did you sleep?", role="CLINICIAN")
        add_segment(s1, "I barely sleep, three hours a night.", start_ms=2000)
        add_segment(s1, "Sometimes I think everyone would be better off without me.", start_ms=4000)
        r = client.post(f"{API}/sessions/{s1}/progress-extraction", json={"transcript_reviewed": False})
        assert r.status_code == 422 and r.json()["error"]["code"] == "TRANSCRIPT_REVIEW_REQUIRED"
        responder["fn"] = scripted(domains={"sleep": {"rating": 3, "direction": "reduced", "refs_text": ["How did you sleep", "I barely sleep"]}},
                                   items={"phq9:3": {"score": 3, "refs_text": ["I barely sleep"]}},
                                   risks=[{"category": "suicidal_ideation", "refs_text": ["better off without me"]}])
        job = analyse(client, s1)
        assert job["status"] == "completed", job
        assert job["counts"]["domains_saved"] == 20 and job["counts"]["risk_saved"] == 1
        obs = {o["domain_key"]: o for o in client.get(f"{API}/sessions/{s1}/domain-observations").json()}
        assert obs["sleep"]["provenance"] == "AI_GENERATED" and obs["sleep"]["review_status"] == "pending"
        assert obs["sleep"]["rating"] == 3 and obs["sleep"]["extraction_job_id"] == job["id"]
        assert obs["low_mood"]["status_value"] == "insufficient_evidence"
        progress = client.get(f"{API}/patients/{pid}/progress").json()
        phq = next(i for i in progress["instruments"] if i["instrument_key"] == "phq9")
        ai = next(s for s in phq["series"] if s["series"] == "ai_estimate")
        assert ai["points"][0]["value"] == 3 and ai["points"][0]["partial"] is True        # pending AI value counts
        score = next(s for s in progress["instrument_scores"] if s["provenance"] == "AI_GENERATED")
        assert next(i for i in score["items"] if i["item_id"] == "9")["score"] is None
        risks = client.get(f"{API}/patients/{pid}/risk-statements").json()
        assert risks[0]["category"] == "suicidal_ideation" and risks[0]["evidence"][0]["start_ms"] == 4000
        assert not any(k in risks[0] for k in ("score", "risk_level"))


def test_manual_and_reviewed_values_are_never_overwritten(responder):
    with TestClient(app) as client:
        pid, (s1,) = make_patient(client, sessions=1)
        a = add_segment(s1, "I barely sleep at all.")
        add_segment(s1, "I am tired all the time.", start_ms=2000)
        client.put(f"{API}/sessions/{s1}/domain-observations/sleep", json={"actor": "Dr Demo", "status_value": "rated", "rating": 1, "evidence_segment_ids": [a]})
        responder["fn"] = scripted(domains={"sleep": {"rating": 3, "refs_text": ["I barely sleep"]}, "energy": {"rating": 2, "refs_text": ["tired all the time"]}})
        analyse(client, s1)
        obs = {o["domain_key"]: o for o in client.get(f"{API}/sessions/{s1}/domain-observations").json()}
        assert obs["sleep"]["provenance"] == "MANUAL" and obs["sleep"]["rating"] == 1
        assert obs["sleep"]["alternatives"][0]["rating"] == 3
        energy = obs["energy"]
        client.post(f"{API}/sessions/{s1}/domain-observations/{energy['id']}/review", json={"actor": "Dr Demo", "action": "accept"})
        responder.update(fn=scripted(domains={"energy": {"rating": 0, "refs_text": ["tired all the time"]}}), sha="e" * 64)
        analyse(client, s1)
        energy = next(o for o in client.get(f"{API}/sessions/{s1}/domain-observations").json() if o["domain_key"] == "energy")
        assert energy["review_status"] == "accepted" and energy["rating"] == 2 and energy["alternatives"][0]["rating"] == 0


def test_rerun_replaces_pending_ai_and_supersedes_scores(responder):
    with TestClient(app) as client:
        pid, (s1,) = make_patient(client, sessions=1)
        add_segment(s1, "I barely sleep.")
        responder["fn"] = scripted(domains={"sleep": {"rating": 3, "refs_text": ["I barely sleep"]}}, items={"phq9:3": {"score": 3, "refs_text": ["I barely sleep"]}})
        analyse(client, s1)
        responder.update(fn=scripted(domains={"sleep": {"rating": 2, "refs_text": ["I barely sleep"]}}, items={"phq9:3": {"score": 2, "refs_text": ["I barely sleep"]}}), sha="e" * 64)
        analyse(client, s1)
        sleep = next(o for o in client.get(f"{API}/sessions/{s1}/domain-observations").json() if o["domain_key"] == "sleep")
        assert sleep["rating"] == 2 and sleep["history"][-1]["rating"] == 3
        active = [s for s in client.get(f"{API}/patients/{pid}/instrument-scores").json() if s["instrument_key"] == "phq9"]
        assert len(active) == 1 and active[0]["total"] == 2
        assert len(client.get(f"{API}/patients/{pid}/instrument-scores?include_inactive=true").json()) == 4


def test_decided_note_test_split_and_busy_lock_block_runs(responder):
    with TestClient(app) as client:
        pid, (s1, s2) = make_patient(client, sessions=2)
        add_segment(s1, "Fictional turn.")
        add_segment(s2, "Fictional turn.")
        decide_note(s1)
        r = client.post(f"{API}/sessions/{s1}/progress-extraction", json={"transcript_reviewed": True})
        assert r.status_code == 409 and r.json()["error"]["code"] == "NOTE_TERMINAL"
        with session_factory() as db:
            db.get(SessionRecord, s2).consent_json = json.dumps({"research_demo": True, "dataset": "longcounsel-8", "source_split": "test"})
            db.commit()
        r = client.post(f"{API}/sessions/{s2}/progress-extraction", json={"transcript_reviewed": True})
        assert r.status_code == 403 and r.json()["error"]["code"] == "HELD_OUT_TEST_SPLIT"
        pid3, (s3,) = make_patient(client, sessions=1)
        add_segment(s3, "Fictional turn.")
        lock = jobs._lock()
        assert lock.acquire(False)
        try:
            r = client.post(f"{API}/sessions/{s3}/progress-extraction", json={"transcript_reviewed": True})
            assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_BUSY"
        finally:
            lock.release()


def test_failed_job_can_be_retried(responder, monkeypatch):
    with TestClient(app) as client:
        pid, (s1,) = make_patient(client, sessions=1)
        add_segment(s1, "I barely sleep.")
        monkeypatch.setattr(jobs, "RUNTIME_FACTORY", lambda folder, cancel: FakeRuntime(scripted(), fail_calls=100))
        first = analyse(client, s1)
        assert first["status"] == "completed" and first["limitations"]       # groups failed, recorded as limitations
        assert any("extraction incomplete" in l for l in first["limitations"])
        monkeypatch.setattr(jobs, "RUNTIME_FACTORY", lambda folder, cancel: FakeRuntime(scripted(domains={"sleep": {"rating": 2, "refs_text": ["I barely sleep"]}})))
        r = client.post(f"{API}/progress-jobs/{first['id']}/retry")
        second = wait(client, r.json()["job_id"])
        assert second["status"] == "completed" and second["counts"]["domains_saved"] == 20
        assert [j["id"] for j in client.get(f"{API}/sessions/{s1}/progress-jobs").json()][:2] == [second["id"], first["id"]]


def test_editing_a_cited_segment_marks_dependants_stale(responder):
    from tests.progress.helpers import add_prediction
    with TestClient(app) as client:
        pid, (s1, s2) = make_patient(client, sessions=2)
        seg = add_segment(s1, "I barely sleep.")
        other = add_segment(s1, "I like walking.", start_ms=3000)
        responder["fn"] = scripted(domains={"sleep": {"rating": 3, "refs_text": ["I barely sleep"]}, "interest": {"rating": 0, "refs_text": ["I like walking"]}})
        analyse(client, s1)
        pred = add_prediction(pid, s2, segment_ids=[seg])
        r = client.patch(f"{API}/sessions/{s1}/transcript/{seg}", json={"text": "I sleep badly most nights."})
        assert r.status_code == 200
        obs = {o["domain_key"]: o for o in client.get(f"{API}/sessions/{s1}/domain-observations").json()}
        assert obs["sleep"]["review_status"] == "stale" and obs["interest"]["review_status"] == "pending"
        assert client.get(f"{API}/patients/{pid}/condition-predictions/{pred}").json()["review_status"] == "stale"
        progress = client.get(f"{API}/patients/{pid}/progress").json()
        cell = next(d for d in progress["domains"] if d["domain_key"] == "sleep")["cells"][0]
        assert cell["value"] is None and cell["gap_reason"] == "stale"
        assert progress["pending_review_counts"]["domain_observations"] >= 1
        # Re-review against the current text: accepted, links refreshed, back in the active series.
        reviewed = client.post(f"{API}/sessions/{s1}/domain-observations/{obs['sleep']['id']}/review", json={"actor": "Dr Demo", "action": "accept"}).json()
        assert reviewed["review_status"] == "accepted"
        cell = next(d for d in client.get(f"{API}/patients/{pid}/progress").json()["domains"] if d["domain_key"] == "sleep")["cells"][0]
        assert cell["value"] == 3
        events = [e["event_type"] for e in client.get(f"{API}/patients/{pid}/audit-events").json()]
        assert "PROGRESS_SOURCES_CHANGED" in events and "PROGRESS_EXTRACTION_SAVED" in events

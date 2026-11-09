import json

from fastapi.testclient import TestClient

from services.api.main import app, session_factory
from services.api.db import DomainObservationRecord, InstrumentScoreRecord
from tests.progress.helpers import add_ai_observation, add_ai_score, add_prediction, add_risk, add_segment, decide_note, make_patient

API = "/api/v1"


def test_manual_domain_entry_validates_evidence_and_audits():
    with TestClient(app) as client:
        pid, (s1, s2, _) = make_patient(client)
        other_pid, (o1,) = make_patient(client, sessions=1, name="Other Fictional Patient")
        own = add_segment(s1, "I have barely slept this week.")
        later = add_segment(s2, "Sleep is a little better.")
        foreign = add_segment(o1, "Fictional other patient text.")
        body = {"actor": "Dr Demo", "status_value": "rated", "rating": 2, "direction": "reduced", "evidence_segment_ids": [own]}
        response = client.put(f"{API}/sessions/{s1}/domain-observations/sleep", json=body)
        assert response.status_code == 200, response.text
        obs = response.json()
        assert obs["provenance"] == "MANUAL" and obs["review_status"] == "accepted" and obs["reviewer_name"] == "Dr Demo"
        assert obs["evidence"][0]["segment_id"] == own and obs["evidence"][0]["speaker_role"] == "CLIENT"
        # Evidence from another patient, or another session of the same patient, is rejected for session-level rows.
        for bad in (foreign, later):
            r = client.put(f"{API}/sessions/{s1}/domain-observations/sleep", json={**body, "evidence_segment_ids": [bad]})
            assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_EVIDENCE_REFERENCE"
        r = client.put(f"{API}/sessions/{s1}/domain-observations/sleep", json={**body, "direction": "sideways"})
        assert r.status_code == 422
        assert client.put(f"{API}/sessions/{s1}/domain-observations/not_a_domain", json=body).status_code == 404
        # Editing keeps history.
        again = client.put(f"{API}/sessions/{s1}/domain-observations/sleep", json={**body, "rating": 1}).json()
        assert again["rating"] == 1 and again["history"][0]["rating"] == 2
        events = client.get(f"{API}/patients/{pid}/audit-events").json()
        assert [e["event_type"] for e in events].count("PROGRESS_DOMAIN_ENTERED") == 2
        assert all(e["patient_id"] == pid for e in events)
        assert other_pid != pid


def test_review_badge_states_original_ai_immutable_and_rejected_kept():
    with TestClient(app) as client:
        pid, (s1, s2, s3) = make_patient(client)
        seg = add_segment(s1, "I feel down most days.")
        obs_id = add_ai_observation(pid, s1, "low_mood", 3, [seg])
        url = f"{API}/sessions/{s1}/domain-observations/{obs_id}/review"
        assert client.post(url, json={"actor": "Dr Demo", "action": "reject"}).status_code == 422   # reason required
        edited = client.post(url, json={"actor": "Dr Demo", "action": "edit", "value": {"rating": 2}, "comment": "Moderate"}).json()
        assert edited["review_status"] == "edited" and edited["rating"] == 2 and edited["reviewer_name"] == "Dr Demo"
        assert edited["original_ai"] == {"rating": 3, "status_value": "rated"}
        assert client.post(url, json={"actor": "Dr Demo", "action": "accept"}).status_code == 409    # already reviewed
        rejected = client.post(url, json={"actor": "Dr Two", "action": "reject", "reason": "Not supported"}).json()
        assert rejected["review_status"] == "rejected" and rejected["original_ai"]["rating"] == 3
        assert len(rejected["history"]) == 2
        assert client.post(url, json={"actor": "Dr Demo", "action": "edit", "value": {"rating": 1}}).status_code == 409
        types = [e["event_type"] for e in client.get(f"{API}/sessions/{s1}/audit-events").json()]
        assert "PROGRESS_DOMAIN_OBSERVATION_EDITED" in types and "PROGRESS_DOMAIN_OBSERVATION_REJECTED" in types


def test_progress_includes_pending_ai_values_and_excludes_rejected():
    with TestClient(app) as client:
        pid, (s1, s2, s3) = make_patient(client)
        segs = [add_segment(s, f"Fictional turn {i}.") for i, s in enumerate((s1, s2, s3))]
        add_ai_score(pid, s1, {str(i): 0 for i in range(1, 9)}, [segs[0]])            # PHQ-8 = 0
        mid = add_ai_score(pid, s2, {str(i): 1 for i in range(1, 9)}, [segs[1]])       # PHQ-8 = 8 (pending AI)
        add_ai_score(pid, s3, {str(i): 2 for i in range(1, 9)}, [segs[2]])             # PHQ-8 = 16
        progress = client.get(f"{API}/patients/{pid}/progress")
        assert progress.status_code == 200, progress.text
        data = progress.json()
        phq = next(i for i in data["instruments"] if i["instrument_key"] == "phq9")
        assert phq["metric"] == "phq8"
        ai = next(s for s in phq["series"] if s["series"] == "ai_estimate")
        assert [p["value"] for p in ai["points"]] == [0, 8, 16]
        assert all(p["review_status"] == "pending" and p["provenance"] == "AI_GENERATED" for p in ai["points"])
        assert ai["trajectory"] == "increasing"
        administered = next(s for s in phq["series"] if s["series"] == "administered")
        assert [p["value"] for p in administered["points"]] == [None, None, None]
        events = [e for e in data["change_events"] if e["measure_kind"] == "instrument"]
        assert {(e["from_session_id"], e["to_session_id"]) for e in events} == {(s1, s2), (s2, s3)}
        assert all(e["provenance_of_inputs"] == "ai" for e in events)
        assert data["pending_review_counts"]["instrument_scores"] == 3
        # Rejecting the middle value turns it into a gap; changes are recomputed across it.
        r = client.post(f"{API}/patients/{pid}/instrument-scores/{mid}/review", json={"actor": "Dr Demo", "action": "reject", "reason": "Wrong"})
        assert r.status_code == 200
        data = client.get(f"{API}/patients/{pid}/progress").json()
        ai = next(s for s in next(i for i in data["instruments"] if i["instrument_key"] == "phq9")["series"] if s["series"] == "ai_estimate")
        assert [p["value"] for p in ai["points"]] == [0, None, 16] and ai["points"][1]["gap_reason"] == "rejected"
        assert {(e["from_session_id"], e["to_session_id"]) for e in data["change_events"] if e["measure_kind"] == "instrument"} == {(s1, s3)}


def test_administered_and_ai_series_coexist_and_item_9_rules():
    with TestClient(app) as client:
        pid, (s1, s2, _) = make_patient(client)
        items = {str(i): 2 for i in range(1, 10)}
        r = client.post(f"{API}/patients/{pid}/instrument-scores", json={"actor": "Dr Demo", "instrument_key": "phq9", "session_id": s1, "items": items})
        assert r.status_code == 201, r.text
        score = r.json()
        assert score["total"] == 18 and score["subtotals"]["phq8"]["total"] == 16 and score["band"] == "moderately severe"
        partial = client.post(f"{API}/patients/{pid}/instrument-scores", json={"actor": "Dr Demo", "instrument_key": "gad7", "administered_at": "2026-10-01", "items": {"1": 1}}).json()
        assert partial["total_is_partial"] and partial["band"] is None and partial["session_id"] is None
        add_ai_score(pid, s1, {str(i): 1 for i in range(1, 9)})
        ai_id = add_ai_score(pid, s2, {str(i): 1 for i in range(1, 9)})
        r = client.post(f"{API}/patients/{pid}/instrument-scores/{ai_id}/review", json={"actor": "Dr Demo", "action": "edit", "value": {"items": {"9": 1}}})
        assert r.status_code == 422 and r.json()["error"]["code"] == "CLINICIAN_ONLY_ITEM"
        data = client.get(f"{API}/patients/{pid}/progress").json()
        phq = next(i for i in data["instruments"] if i["instrument_key"] == "phq9")
        values = {s["series"]: [p["value"] for p in s["points"]] for s in phq["series"]}
        assert values["administered"][0] == 16 and values["ai_estimate"][:2] == [8, 8]
        gad = next(i for i in data["instruments"] if i["instrument_key"] == "gad7")
        administered = next(s for s in gad["series"] if s["series"] == "administered")
        assert any(p["slot"].startswith("date:2026-10-01") for p in administered["points"])
        session_less = [e for e in client.get(f"{API}/patients/{pid}/audit-events").json() if e["session_id"] is None]
        assert session_less and session_less[0]["event_type"] == "PROGRESS_INSTRUMENT_ADMINISTERED"


def test_decided_note_locks_session_values_but_not_predictions():
    with TestClient(app) as client:
        pid, (s1, s2, _) = make_patient(client)
        seg = add_segment(s1, "I worry constantly.")
        obs = add_ai_observation(pid, s1, "worry", 2, [seg])
        risk = add_risk(pid, s1, [seg])
        pred = add_prediction(pid, s1, segment_ids=[seg])
        decide_note(s1)
        body = {"actor": "Dr Demo", "status_value": "rated", "rating": 1}
        assert client.put(f"{API}/sessions/{s1}/domain-observations/worry", json=body).json()["error"]["code"] == "NOTE_TERMINAL"
        assert client.post(f"{API}/sessions/{s1}/domain-observations/{obs}/review", json={"actor": "Dr", "action": "accept"}).status_code == 409
        assert client.post(f"{API}/patients/{pid}/risk-statements/{risk}/review", json={"actor": "Dr", "action": "accept"}).status_code == 409
        assert client.post(f"{API}/patients/{pid}/instrument-scores", json={"actor": "Dr", "instrument_key": "phq9", "session_id": s1, "items": {"1": 1}}).status_code == 409
        assert client.get(f"{API}/sessions/{s1}/domain-observations").json()[0]["locked"] is True
        r = client.post(f"{API}/patients/{pid}/condition-predictions/{pred}/review", json={"actor": "Dr", "action": "accept"})
        assert r.status_code == 200 and r.json()["review_status"] == "accepted"
        assert client.put(f"{API}/sessions/{s2}/domain-observations/worry", json=body).status_code == 200


def test_prediction_detail_review_and_change_acknowledgement():
    with TestClient(app) as client:
        pid, (s1, s2, _) = make_patient(client)
        seg = add_segment(s1, "I have felt low for weeks.", start_ms=5000)
        p1 = add_prediction(pid, s1, label="possible", segment_ids=[seg], probability=0.4)
        p2 = add_prediction(pid, s2, label="likely", segment_ids=[seg], probability=0.7)
        detail = client.get(f"{API}/patients/{pid}/condition-predictions/{p2}").json()
        finding = detail["findings"][0]
        assert finding["evidence"][0]["segment_id"] == seg and finding["evidence"][0]["start_ms"] == 5000
        assert finding["summary"]                                       # registry criterion summary attached
        assert detail["probability"] is None and "research-only" in detail["probability_note"]   # clinical patient (Q3)
        edited = client.post(f"{API}/patients/{pid}/condition-predictions/{p1}/review",
                             json={"actor": "Dr Demo", "action": "edit", "value": {"prediction": "insufficient_evidence"}, "comment": "Too early"}).json()
        assert edited["prediction"] == "insufficient_evidence" and edited["original_ai"] == {"prediction": "possible"}
        data = client.get(f"{API}/patients/{pid}/progress").json()
        change = next(e for e in data["change_events"] if e["measure_kind"] == "prediction" and e["rule_id"] == "prediction.label_change")
        assert change["provenance_of_inputs"] == "mixed"
        ack = client.post(f"{API}/patients/{pid}/change-events/{change['id']}/acknowledge", json={"actor": "Dr Demo", "comment": "Discussed"})
        assert ack.status_code == 200 and ack.json()["acknowledged_by"] == "Dr Demo"
        assert client.post(f"{API}/patients/{pid}/change-events/{change['id']}/acknowledge", json={"actor": "Dr Demo"}).status_code == 409
        again = client.get(f"{API}/patients/{pid}/progress").json()
        assert next(e for e in again["change_events"] if e["id"] == change["id"])["acknowledged_by"] == "Dr Demo"
        assert again["predictions"][0]["as_of_session_id"] == s2


def test_empty_patient_progress_and_past_timeframe_not_plotted():
    with TestClient(app) as client:
        pid, _ = make_patient(client, sessions=0, name="Empty Fictional Patient")
        data = client.get(f"{API}/patients/{pid}/progress").json()
        assert data["sessions"] == [] and data["change_events"] == [] and data["predictions"] == []
        pid, (s1,) = make_patient(client, sessions=1)
        seg = add_segment(s1, "Two years ago I barely slept for a week.")
        add_ai_observation(pid, s1, "sleep", 3, [seg], timeframe="past")
        cells = next(d for d in client.get(f"{API}/patients/{pid}/progress").json()["domains"] if d["domain_key"] == "sleep")["cells"]
        assert cells[0]["value"] is None and cells[0]["raw_value"] is None and cells[0]["timeframe"] == "past" and cells[0]["rating"] == 3
    with session_factory() as db:
        assert db.query(DomainObservationRecord).count() > 0 and db.query(InstrumentScoreRecord).count() >= 0
        assert json.loads(db.query(DomainObservationRecord).first().source_revisions_json) is not None

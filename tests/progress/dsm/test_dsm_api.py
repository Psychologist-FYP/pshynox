"""Registry v2 API: findings, review badges, locks, staleness, registry review export/import, dashboard and revert."""
import csv
import io

from fastapi.testclient import TestClient

from services.api.db import DomainObservationRecord, DsmBatchRecord, utc_now
from services.api.main import app, session_factory
from services.progress.dsm import store as dsm_store
from tests.progress.helpers import add_ai_observation, add_segment, decide_note, make_patient

API = "/api/v1"


def ai_finding(session_id, key, status, segs, **extra):
    with session_factory() as db:
        from services.api.db import SessionRecord
        session = db.get(SessionRecord, session_id)
        item = {"status": status, "frequency": extra.get("frequency", "nearly every day"), "onset_or_duration_text": extra.get("onset", ""),
                "time_frame": "current_episode", "period_label": "", "qualifier": None, "degree": extra.get("degree"),
                "contexts": extra.get("contexts", []), "evidence_segment_ids": segs, "counter_evidence": extra.get("counter", []),
                "counter_check": {"searched": True, "found": [], "alternative_explanation": ""}, "basis": "Client described it."}
        dsm_store.persist_findings(db, session, {key: item}, job_id="job-test", batch_id=extra.get("batch"))
        db.commit()
        return dsm_store.active_finding(db, session_id, key).id


def test_manual_finding_review_badges_and_history():
    with TestClient(app) as client:
        pid, (s1, s2, _) = make_patient(client)
        seg = add_segment(s1, "I have no energy at all these days.")
        body = {"actor": "Dr Demo", "status": "present", "frequency": "nearly every day", "evidence_segment_ids": [seg], "clinician_intensity": 2}
        r = client.put(f"{API}/sessions/{s1}/dsm-findings/feat.fatigue", json=body)
        assert r.status_code == 200, r.text
        f = r.json()
        assert f["provenance"] == "MANUAL" and f["clinician_intensity"] == 2 and set(f["linked_items"]) == {"MDE.A6", "PDD.B3", "GAD.C2"}
        assert client.put(f"{API}/sessions/{s1}/dsm-findings/feat.nope", json=body).status_code == 404
        assert client.put(f"{API}/sessions/{s1}/dsm-findings/feat.fatigue", json={**body, "qualifier": "sideways"}).status_code == 422
        # AI finding: pending badge, then edit keeps the original AI output immutable.
        seg2 = add_segment(s2, "I barely sleep, maybe three hours, and I'm exhausted.")
        fid = ai_finding(s2, "feat.sleep_insomnia_hypersomnia", "present", [seg2])
        url = f"{API}/sessions/{s2}/dsm-findings/{fid}/review"
        edited = client.post(url, json={"actor": "Dr Demo", "action": "edit", "value": {"qualifier": "insomnia", "frequency": "more days than not"}}).json()
        assert edited["review_status"] == "edited" and edited["qualifier"] == "insomnia" and edited["original_ai"]["status"] == "present"
        assert edited["history"] and client.post(url, json={"actor": "Dr Demo", "action": "accept"}).status_code == 409
        # clinician_intensity is clinician-entered only: AI persistence never sets it.
        assert ai_finding(s2, "feat.fatigue", "present", [seg2]) and client.get(f"{API}/sessions/{s2}/dsm-findings").json()[-1]["clinician_intensity"] is None


def test_decided_note_lock_and_staleness():
    with TestClient(app) as client:
        pid, (s1, s2, _) = make_patient(client)
        seg = add_segment(s1, "Most days I feel low and empty.")
        fid = ai_finding(s1, "feat.depressed_mood", "present", [seg])
        r = client.patch(f"{API}/sessions/{s1}/transcript/{seg}", json={"text": "Most days I feel low."})
        assert r.status_code == 200, r.text
        rows = client.get(f"{API}/sessions/{s1}/dsm-findings").json()
        assert rows[0]["review_status"] == "stale"
        grid = client.get(f"{API}/patients/{pid}/progress").json()["dsm"]["grid"][0]["rows"]
        a1 = next(r for r in grid if r["id"] == "MDE.A1")
        assert a1["cells"][0]["status"] == "stale"
        reviewed = client.post(f"{API}/sessions/{s1}/dsm-findings/{fid}/review", json={"actor": "Dr Demo", "action": "accept"}).json()
        assert reviewed["review_status"] == "accepted"
        decide_note(s1)
        body = {"actor": "Dr Demo", "status": "absent"}
        assert client.put(f"{API}/sessions/{s1}/dsm-findings/feat.fatigue", json=body).status_code == 409


def test_dashboard_grid_shows_statuses_gaps_and_change_events():
    with TestClient(app) as client:
        pid, (s1, s2, s3) = make_patient(client)
        a = add_segment(s1, "Nothing interests me anymore.")
        b = add_segment(s2, "I enjoy my music again, things are fine.")
        ai_finding(s1, "feat.anhedonia", "present", [a])
        ai_finding(s2, "feat.anhedonia", "explicit_denial", [b])
        data = client.get(f"{API}/patients/{pid}/progress").json()
        assert data["registry_kind"] == "dsm"
        dsm = data["dsm"]
        a2 = next(r for g in dsm["grid"] for r in g["rows"] if r["id"] == "MDE.A2")
        assert [c["status"] for c in a2["cells"]] == ["present", "explicit_denial", None]  # session 3: a gap, never "absent"
        assert a2["cells"][0]["review_status"] == "pending" and a2["verification"] == "pending"
        assert dsm["verification_note"] == "Paraphrased from DSM-5-TR · pending clinical verification"
        events = [e for e in data["change_events"] if e["measure_kind"] == "criterion"]
        assert any(e["measure_key"] == "MDE.A2" and e["from_label"] == "present" and e["to_label"] == "explicit_denial" for e in events)
        assert data["pending_review_counts"]["dsm_findings"] == 2


def test_registry_export_and_import_round_trip_keeps_ids():
    with TestClient(app) as client:
        view = client.get(f"{API}/progress/registry/dsm").json()
        assert view["active"] == "dsm" and len(view["items"]) == 93
        text = client.get(f"{API}/progress/registry/dsm/export.csv").text
        rows = list(csv.DictReader(io.StringIO(text)))
        assert {r["entity_kind"] for r in rows} == {"item", "condition", "feature"} and all(r["current_status"] == "pending" for r in rows)
        html = client.get(f"{API}/progress/registry/dsm/export.html").text
        assert "not APA text" in html and "MDE.A4" in html
        a1 = next(r for r in rows if r["id"] == "MDE.A1")
        a2 = next(r for r in rows if r["id"] == "MDE.A2")
        a3 = next(r for r in rows if r["id"] == "MDE.A3")
        a1.update(decision="verified", verified_by="Clinical adviser", verified_on="2026-11-09")
        a2.update(decision="changed", verified_by="Clinical adviser")             # changed without a comment: rejected
        a3.update(decision="verified", content_sha256="0" * 64)                     # stale hash: rejected
        bogus = {**a1, "id": "MDE.Z1"}
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows([a1, a2, a3, bogus])
        result = client.post(f"{API}/progress/registry/dsm/verifications/import", json={"actor": "Dr Demo", "csv": out.getvalue()}).json()
        assert result["recorded"] == 1 and {r["id"]: r["reason"][:7] for r in result["rejected"]} == {"MDE.A2": "a 'chan", "MDE.A3": "content", "MDE.Z1": "unknown"}
        view = client.get(f"{API}/progress/registry/dsm").json()
        assert next(i for i in view["items"] if i["id"] == "MDE.A1")["verification"]["status"] == "verified"
        assert next(i for i in view["items"] if i["id"] == "MDE.A2")["verification"]["status"] == "pending"
        assert [i["id"] for i in view["items"]][:3] == ["MDE.A", "MDE.A1", "MDE.A2"]  # ids unchanged
        bad = client.post(f"{API}/progress/registry/dsm/verifications/import", json={"actor": "Dr Demo", "csv": "a,b\n1,2\n"})
        assert bad.status_code == 422


def test_revert_restores_retired_v1_values_and_deactivates_dsm_rows():
    with TestClient(app) as client:
        pid, (s1, _, _) = make_patient(client)
        seg = add_segment(s1, "I feel down most days.")
        obs = add_ai_observation(pid, s1, "low_mood", 2, [seg])
        with session_factory() as db:
            batch = dsm_store.create_batch(db, pid, "reanalysis", "job-test", [s1], [])
            counts = dsm_store.retire_v1_for_session(db, s1, batch.id)
            db.commit()
            batch_id = batch.id
        assert counts["domains"] == 1
        ai_finding(s1, "feat.depressed_mood", "present", [seg], batch=batch_id)
        data = client.get(f"{API}/patients/{pid}/progress").json()
        low = next(d for d in data["domains"] if d["domain_key"] == "low_mood")
        assert low["cells"][0]["record_id"] is None  # retired from the earlier-tracking grid
        r = client.post(f"{API}/patients/{pid}/dsm-batches/{batch_id}/revert", json={"actor": "Dr Demo"})
        assert r.status_code == 200 and r.json()["restored"] == 1 and r.json()["deactivated"] == 1
        assert client.get(f"{API}/sessions/{s1}/dsm-findings").json() == []
        with session_factory() as db:
            assert db.get(DomainObservationRecord, obs).retired_batch_id is None
            assert db.get(DsmBatchRecord, batch_id).status == "reverted"
        assert client.post(f"{API}/patients/{pid}/dsm-batches/{batch_id}/revert", json={"actor": "Dr Demo"}).status_code == 409
        assert utc_now()

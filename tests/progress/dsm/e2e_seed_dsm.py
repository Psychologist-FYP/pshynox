"""Seed a temporary database with one fictional patient for the DSM Playwright suite (no model is run).

    python -m tests.progress.dsm.e2e_seed_dsm <output.json>
"""
import json
import sys

from fastapi.testclient import TestClient

from services.api.db import SessionRecord
from services.api.main import app, session_factory
from services.progress.dsm import rules
from services.progress.dsm import store as dsm_store
from services.progress.dsm.registry import dsm_registry
from tests.progress.helpers import add_ai_observation, add_segment, make_patient


def finding(status, segs, contexts=(), **kw):
    return {"status": status, "frequency": kw.get("frequency", "nearly every day"), "onset_or_duration_text": kw.get("onset", ""),
            "time_frame": "current_episode", "period_label": "", "qualifier": kw.get("qualifier"), "degree": kw.get("degree"),
            "contexts": list(contexts), "evidence_segment_ids": segs, "counter_evidence": [], "basis": kw.get("basis", "Paraphrased from the cited turn."),
            "counter_check": {"searched": True, "found": [], "alternative_explanation": ""}}


def main(output: str) -> None:
    with TestClient(app) as client:
        pid, (s1, s2) = make_patient(client, sessions=2, name="Fictional DSM Patient")
    low = add_segment(s1, "For about six weeks I have felt low and empty nearly every day.", start_ms=65_000)
    interest = add_segment(s1, "Nothing I used to enjoy feels like anything now.", start_ms=80_000)
    sleep = add_segment(s1, "I lie awake until three most nights and I am exhausted.", start_ms=95_000)
    guilt = add_segment(s1, "I feel like a failure and guilty all the time.", start_ms=110_000)
    focus = add_segment(s1, "I cannot concentrate on my reading at all.", start_ms=125_000)
    friends = add_segment(s1, "It's not that I'm scared of people, I just have no energy to see them.", start_ms=140_000)
    classes = add_segment(s1, "I have missed most of my classes this month.", start_ms=155_000)
    drink = add_segment(s1, "No alcohol or drugs, and my blood tests were normal.", start_ms=170_000)
    add_segment(s2, "This week was a bit lighter.", start_ms=10_000)
    LOW = ["low_mood"]
    findings = {"feat.depressed_mood": finding("present", [low], LOW, onset="about six weeks"), "feat.anhedonia": finding("present", [interest]),
                "feat.sleep_insomnia_hypersomnia": finding("present", [sleep], qualifier="insomnia"), "feat.fatigue": finding("present", [sleep], LOW),
                "feat.worthlessness_guilt": finding("present", [guilt]), "feat.concentration": finding("present", [focus], LOW),
                "feat.social_avoidance": finding("present", [friends], LOW), "feat.social_fear_scrutiny": finding("explicit_denial", [friends]),
                "imp.occupational": finding("present", [classes], LOW, degree="clinically_significant"),
                "excl.substance": finding("absent", [drink]), "excl.medical": finding("absent", [drink])}
    add_ai_observation(pid, s1, "low_mood", 3, [low])  # earlier (v1) tracking
    with session_factory() as db:
        session = db.get(SessionRecord, s1)
        batch = dsm_store.create_batch(db, pid, "extraction", None, [s1], [])
        dsm_store.persist_findings(db, session, findings, job_id="seed", batch_id=batch.id)
        dsm_store.persist_risks(db, session, [{"categories": ["suicidal_ideation_passive"], "note_kind": "flag", "segment_ids": [guilt],
                                               "basis": "Seeded fictional flag for the review panel."}], job_id="seed")
        db.flush()
        segments = dsm_store.roles_of(db, {s for f in findings.values() for s in f["evidence_segment_ids"]})
        props = {r.feature_key: dsm_store.proposal_of(r, segments) for r in dsm_store.session_findings(db, [s1])[s1]}
        period = {"id": "P1", "label": "Current low period", "time_frame": "current", "duration_days": 42, "hospitalised": False,
                  "duration_evidence": [segments[low]], "proposals": props}
        reg = dsm_registry()
        results = rules.evaluate_all(reg, [period], {"global": {k: v for k, v in props.items() if k.startswith("excl.")}})
        ids = {}
        for key, res in results.items():
            if res["has_evidence"]:
                res.update(as_of_session_id=s1, periods=[{**{k: v for k, v in period.items() if k != "proposals"}, "features": list(props)}],
                           postprocess={"model_label": "likely", "notes": []}, prompt_version="seed", model_version="seed")
                ids[key] = dsm_store.persist_dsm_prediction(db, pid, res, job_id="seed", batch_id=batch.id).id
        db.commit()
    with open(output, "w", encoding="utf-8") as handle:
        json.dump({"patient_id": pid, "sessions": [s1, s2], "segments": {"low": low, "friends": friends}, "predictions": ids}, handle)


if __name__ == "__main__":
    main(sys.argv[1])

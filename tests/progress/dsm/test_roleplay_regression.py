"""Regression on the fictional two-session role-play patient (saved transcripts; mocked model outputs).

Expected (task brief): depression pattern likely; social anxiety NOT possible (the client said it is not that they are
scared of others); bipolar not met / not assessable; risk statements categorised and de-duplicated.
"""
import json
from pathlib import Path

from services.progress.dsm.extraction import extract_session_dsm
from services.progress.dsm.prediction import predict_dsm
from services.retrieval.engine import digest
from tests.progress.fake_runtime import FakeRuntime

from .fake_dsm import combined, stage_a, stage_b

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "roleplay_dsm"


def load_sessions():
    out = []
    for n in (1, 2):
        data = json.loads((FIXTURE / f"s{n}.json").read_text())
        segs = [{"id": f"rp{n}-{t['turn_id']}", "session_id": f"rp-s{n}", "speaker_role": t["speaker"], "start_ms": t["start_ms"],
                 "end_ms": t["end_ms"], "text": t["text"], "revision": 1} for t in data["turns"]]
        out.append({"session_id": f"rp-s{n}", "index": n, "segments": segs})
    return out


LOW = ["low_mood"]
STAGE_A = {
    "feat.depressed_mood": {"refs_text": ["feeling very low", "I just feel empty"], "contexts": LOW, "onset": "about two months"},
    "feat.anhedonia": {"refs_text": ["Even watching matches feel like nothing"]},
    "feat.appetite_weight": {"refs_text": ["i skip meals a lot", "noticed i have lost weight"], "qualifier": "decreased"},
    "feat.sleep_insomnia_hypersomnia": {"refs_text": ["lie awake till 3 or 4"], "qualifier": "insomnia"},
    "feat.psychomotor_observable": {"refs_text": ["speaking quite slowly"], "who": "clinician_observation", "qualifier": "slowed"},
    "feat.fatigue": {"refs_text": ["always tired"], "contexts": LOW},
    "feat.worthlessness_guilt": {"refs_text": ["I feel guilty all the time"]},
    "feat.concentration": {"refs_text": ["can't concentrate"], "contexts": LOW},
    "feat.social_avoidance": {"refs_text": ["don't have the energy to talk"], "contexts": LOW},
    "feat.social_fear_scrutiny": {"status": "explicit_denial", "refs_text": ["not that I'm scared of them"]},
    "feat.worry_excessive": {"status": "explicit_denial", "refs_text": ["Not really worrying"]},
    "feat.elevated_mood": {"status": "explicit_denial", "refs_text": ["No, never, I wish"]},
    "imp.occupational": {"refs_text": ["missed about six"], "contexts": LOW, "degree": "clinically_significant"},
    "excl.substance": {"status": "absent", "refs_text": ["No alcohol or drugs"]},
    "excl.medical": {"status": "absent", "refs_text": ["thyroid and everything was normal"]},
}
RISKS = [
    {"category": "suicidal_ideation_passive", "refs_text": ["better off without me"]},
    {"category": "suicidal_ideation_passive", "refs_text": ["better off without me", "I just want everything to stop"]},  # flagged again: merged
    {"category": "self_harm_recent", "refs_text": ["about three weeks ago"]},
    {"category": "denial_of_plan_or_intent", "refs_text": ["No, no plan"]},
    {"category": "protective_factor", "refs_text": ["No, no plan"]},
]
STAGE_B = {k: {**v, "time_frame": "current"} for k, v in STAGE_A.items() if k not in {"feat.psychomotor_observable"}}
STAGE_B["feat.psychomotor_observable"] = {"refs_text": ["speaking quite slowly"], "qualifier": "slowed"}
PERIODS = [{"label": "Current low period", "time_frame": "current", "duration_days": 60, "duration_text": ["two months ago"],
            "features": list(STAGE_B)}]


def test_stage_a_roleplay_findings_and_risk_dedupe(tmp_path):
    s1 = load_sessions()[0]
    snap = {"session_id": s1["session_id"], "revision": digest(s1["segments"]), "segments": s1["segments"]}
    runtime = FakeRuntime(stage_a(STAGE_A, RISKS, items={"phq9:1": {"score": 3, "refs_text": ["feel like nothing"]}}))
    result = extract_session_dsm(snap, runtime, tmp_path / "job", evidence_mode="retrieval")
    f = result["findings"]
    assert f["feat.social_fear_scrutiny"]["status"] == "explicit_denial"
    assert f["feat.social_avoidance"]["contexts"] == ["low_mood"] and f["feat.fatigue"]["status"] == "present"
    assert f["feat.depressed_mood"]["counter_check"]["searched"] is True
    flags = result["risk_statements"]
    passive = [r for r in flags if "suicidal_ideation_passive" in r["categories"]]
    assert len(passive) == 1 and len(passive[0]["segment_ids"]) == 2  # de-duplicated by segment
    assert any(r["categories"] == ["self_harm_recent"] for r in flags)
    support = [r for r in flags if r["note_kind"] == "supporting"]
    assert len(support) == 1 and support[0]["categories"] == ["protective_factor", "denial_of_plan_or_intent"]
    # PHQ-9 / GAD-7 stay separate calls with the unchanged v2 item prompt.
    assert result["phq_items"]["1"]["score"] == 3 and "9" not in result["phq_items"]
    assert result["groups"]["questionnaires"]["version"] == "progress-extract-v2"


def stage_a_inputs(sessions, tmp_path):
    """Run mocked Stage A on each session and attach its findings, as the clinical job does."""
    for s in sessions:
        snap = {"session_id": s["session_id"], "revision": digest(s["segments"]), "segments": s["segments"]}
        result = extract_session_dsm(snap, FakeRuntime(stage_a(STAGE_A, RISKS)), tmp_path / f"a-{s['index']}", evidence_mode="retrieval")
        s["findings"] = [{"feature_key": k, "review_status": "pending", **v} for k, v in result["findings"].items()]
    return sessions


def test_roleplay_labels(tmp_path):
    sessions = stage_a_inputs(load_sessions(), tmp_path)
    runtime = FakeRuntime(stage_b(STAGE_B, PERIODS, model_labels={"sad": "possible"}))
    out = predict_dsm(sessions, runtime, tmp_path / "pred")
    r = out["results"]
    assert r["mde"]["label"] == "likely" and r["mdd"]["label"] == "likely"
    mde_a = next(e for e in r["mde"]["elements"] if e["id"] == "MDE.A")
    assert mde_a["line"].startswith("A: 8 of 9 present") and "includes A1, A2" in mde_a["line"]
    assert next(e for e in r["mde"]["elements"] if e["id"] == "MDE.C")["status"] == "met"  # thyroid normal and no substances cited
    assert r["sad"]["label"] == "not_met"           # never "possible": avoidance is explained by low energy, and fear is denied
    assert r["sad"]["postprocess"]["model_label"] == "possible"  # the model's own label is logged only
    assert r["gad"]["label"] == "not_met"
    assert r["bipolar_i"]["label"] == "insufficient_evidence" and r["bipolar_ii"]["label"] in {"insufficient_evidence", "not_met"}
    assert any("high or irritable mood" in m for m in r["mdd"]["missing_information"])  # MDD.E history goes to information needed
    assert {s["key"] for s in r["mdd"]["specifiers"]} == {"severity"}
    assert r["mdd"]["footer"] == "Hypothesis for clinician review; not a diagnosis."


def test_stage_a_denial_contradicts_a_present_proposal(tmp_path):
    sessions = load_sessions()[:1]
    sessions[0]["findings"] = [{"feature_key": "feat.worry_excessive", "status": "explicit_denial", "review_status": "pending",
                                "evidence_segment_ids": [next(s["id"] for s in sessions[0]["segments"] if "Not really worrying" in s["text"])],
                                "counter_evidence": [], "basis": "Client denied worrying."}]
    proposals = {"feat.worry_excessive": {"refs_text": ["worrying a lot about many"], "contexts": ["worry_anxiety"]}}
    out = predict_dsm(sessions, FakeRuntime(stage_b(proposals, [])), tmp_path / "p")
    worry = out["proposals"]["feat.worry_excessive"]
    assert worry["status"] == "present"
    gad_a = next(e for e in out["results"]["gad"]["elements"] if e["id"] == "GAD.A")
    assert gad_a["items"][0]["status"] == "conflicting" and "denial" in gad_a["detail"]


def test_uncited_absence_is_a_gap_and_present_needs_the_counter_check():
    from services.progress.dsm.extraction import EMPTY_CHECK, validate_findings
    from services.progress.validation import Checker
    rows = {"T0": {"id": "x0", "speaker_role": "CLIENT", "text": "I sleep fine.", "alias": "T0"}}
    data = {"requested": {"features": [{"key": "feat.fatigue"}, {"key": "feat.grandiosity"}, {"key": "feat.depressed_mood"}]}}
    base = {"who": "client_report", "frequency": "unclear", "onset_or_duration": "", "time_frame": "unclear", "period_label": "",
            "qualifier": None, "degree": None, "contexts": [], "counter_refs": [], "basis": ""}
    value = {"features": {"feat.fatigue": {**base, "status": "absent", "refs": [], "counter_check": EMPTY_CHECK},
                          "feat.grandiosity": {**base, "status": "explicit_denial", "refs": [], "counter_check": EMPTY_CHECK},
                          "feat.depressed_mood": {**base, "status": "present", "refs": ["T0"], "counter_check": {"searched": False, "alternative_explanation": ""}}},
             "limitations": []}
    checker = Checker(rows)
    out = validate_findings(value, data, checker)["findings"]
    assert out["feat.fatigue"]["status"] == "insufficient_evidence" and out["feat.grandiosity"]["status"] == "insufficient_evidence"
    assert "feat.depressed_mood" not in out and any("counter-evidence check" in w["reason"] for w in checker.withheld)

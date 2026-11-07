"""Stage B: deterministic post-processing and the prediction core with a scripted runtime (fictional data)."""
import pytest

from services.note_generation.runtime import GenerationError
from services.progress import postprocess
from services.progress.prediction import predict_condition
from services.progress.registry import registry
from tests.progress.fake_runtime import FakeRuntime, alias_for

EV = [{"segment_id": "s1", "session_id": "x", "start_ms": 0, "end_ms": 1, "speaker_role": "CLIENT", "basis": "Client reported it."}]


def finding(cid, status="met", evidence=EV, counter=(), duration=""):
    return {"criterion_id": cid, "status": status, "evidence": list(evidence) if status in {"met", "conflicting"} else [],
            "counter_evidence": list(counter), "timeframe": "current", "duration_note": duration}


def model(findings, label="likely", duration="met", impairment="met", subtype=None, rationale=()):
    return {"findings": findings, "prediction": label, "duration_status": duration, "impairment_status": impairment, "subtype": subtype,
            "rationale": list(rationale)}


MDE = registry().conditions.get("depressive_episode")
BIPOLAR = registry().conditions.get("bipolar")
FIVE = ["mde.depressed_mood", "mde.anhedonia", "mde.sleep", "mde.fatigue", "mde.concentration"]


def test_likely_kept_only_when_counts_duration_and_impairment_are_cited():
    rows = [finding(c, duration="most days for a month" if c == "mde.depressed_mood" else "") for c in FIVE] + [finding("mde.impairment")]
    out = postprocess.apply(MDE, model(rows))
    assert out["prediction"] == "likely" and out["criteria_met_count"] == 5 and out["criteria_required"] == 5
    assert out["impairment_status"] == "met" and out["duration_status"] == "met"


def test_likely_downgraded_when_evidence_missing_and_counts_recomputed():
    rows = [finding(c, duration="a month") for c in FIVE[:4]] + [finding("mde.concentration", evidence=[])] + [finding("mde.impairment")]
    out = postprocess.apply(MDE, model(rows))
    assert out["criteria_met_count"] == 4                                     # recount ignores uncited 'met'
    assert out["prediction"] == "possible"
    assert any("downgraded to 'possible'" in n for n in out["postprocess"]["notes"])
    assert any("mde.concentration: 'met' without cited evidence" in n for n in out["postprocess"]["notes"])


def test_likely_downgraded_without_cited_duration_or_impairment():
    rows = [finding(c) for c in FIVE] + [finding("mde.impairment")]
    out = postprocess.apply(MDE, model(rows))                                   # model says duration met, but no duration note
    assert out["duration_status"] == "unknown" and out["prediction"] == "possible"
    rows = [finding(c, duration="six weeks") for c in FIVE]
    assert postprocess.apply(MDE, model(rows))["prediction"] == "possible"     # impairment required, not cited


def test_possible_needs_core_and_unlikely_needs_counter_evidence():
    out = postprocess.apply(MDE, model([finding("mde.sleep"), finding("mde.fatigue")], label="possible"))
    assert out["prediction"] == "insufficient_evidence"                         # no core criterion met
    no_counter = postprocess.apply(MDE, model([finding("mde.depressed_mood", "not_met"), finding("mde.anhedonia", "not_met")], label="unlikely"))
    assert no_counter["prediction"] == "insufficient_evidence"
    denied = [finding("mde.depressed_mood", "not_met", counter=EV), finding("mde.anhedonia", "not_met", counter=EV)]
    assert postprocess.apply(MDE, model(denied, label="unlikely"))["prediction"] == "unlikely"


def test_labels_are_never_upgraded():
    rows = [finding(c, duration="a month") for c in FIVE] + [finding("mde.impairment")]
    assert postprocess.apply(MDE, model(rows, label="insufficient_evidence"))["prediction"] == "insufficient_evidence"


MANIA = ["mania.elevated_mood", "mania.grandiosity", "mania.decreased_sleep_need", "mania.racing_thoughts"]


def test_bipolar_i_requires_cited_manic_period_with_duration():
    rows = [finding(c) for c in MANIA] + [finding("bipolar.manic_period", duration="about ten days, admitted to hospital")]
    out = postprocess.apply(BIPOLAR, model(rows, subtype="bipolar_ii_pattern"))
    assert out["subtype_key"] == "bipolar_i_pattern" and out["prediction"] == "likely"
    assert any("replaced by 'bipolar_i_pattern'" in n for n in out["postprocess"]["notes"])
    no_duration = postprocess.apply(BIPOLAR, model([finding(c) for c in MANIA] + [finding("bipolar.manic_period")]))
    assert no_duration["subtype_key"] is None and no_duration["prediction"] == "possible"


def test_bipolar_ii_needs_hypomanic_and_depressive_periods():
    rows = [finding(c) for c in MANIA] + [finding("bipolar.hypomanic_period", duration="five days"), finding("bipolar.depressive_period", duration="a month")]
    assert postprocess.apply(BIPOLAR, model(rows))["subtype_key"] == "bipolar_ii_pattern"
    only_hypo = postprocess.apply(BIPOLAR, model(rows[:-1]))
    assert only_hypo["subtype_key"] is None and only_hypo["prediction"] == "possible"


def test_irritable_only_mood_needs_four_associated_symptoms():
    three = [finding("mania.irritable_mood"), finding("mania.grandiosity"), finding("mania.decreased_sleep_need"), finding("mania.racing_thoughts"),
             finding("bipolar.manic_period", duration="a week")]
    assert postprocess.apply(BIPOLAR, model(three))["subtype_key"] is None
    four = three + [finding("mania.risky_activities")]
    assert postprocess.apply(BIPOLAR, model(four))["subtype_key"] == "bipolar_i_pattern"


def test_depressive_features_only_is_not_assessable():
    out = postprocess.apply(BIPOLAR, model([finding("bipolar.depressive_period", duration="six weeks")], label="possible"))
    assert out["subtype_key"] == "depressive_features_only" and out["prediction"] == "insufficient_evidence"
    via_obs = postprocess.apply(BIPOLAR, model([], label="insufficient_evidence"), depressive_features=True)
    assert via_obs["subtype_key"] == "depressive_features_only"


def test_rationale_without_ids_removed_and_listed():
    out = postprocess.apply(MDE, model([finding("mde.depressed_mood")], label="possible",
                                       rationale=[{"sentence": "Low mood reported.", "segment_ids": ["s1"]}, {"sentence": "Probably depressed.", "segment_ids": []}]))
    assert [r["sentence"] for r in out["rationale"]] == ["Low mood reported."]
    assert out["postprocess"]["removed_sentences"] == ["Probably depressed."]


# ---------------------------------------------------------------- core with a scripted model

def seg(sid, i, text, role="CLIENT"):
    return {"id": f"{sid}-{i}", "session_id": sid, "start_ms": i * 10_000, "end_ms": i * 10_000 + 9_000, "speaker_role": role, "text": text, "revision": 1}


SESSIONS = [
    {"session_id": "p-s1", "index": 1, "segments": [seg("p-s1", 0, "I have felt down and empty most days for weeks."), seg("p-s1", 1, "My brother has been really depressed too.")],
     "observations": [{"domain_key": "low_mood", "status_value": "rated", "rating": 3, "timeframe": "current", "evidence_segment_ids": ["p-s1-0"], "review_status": "pending"}],
     "scores": [{"instrument_key": "phq9", "value": 14, "metric": "phq8", "provenance": "AI_GENERATED", "review_status": "pending"}]},
    {"session_id": "p-s2", "index": 2, "segments": [seg("p-s2", 0, "Nothing interests me anymore, I stopped seeing friends."), seg("p-s2", 1, "I sleep badly and I am exhausted.")],
     "observations": [], "scores": []},
]


def _maybe(data, needle):
    try:
        return alias_for(data, needle)
    except AssertionError:  # the turn was not retrieved for this condition's packet
        return None


def stage_b(criteria, label="possible", rationale=(), only=None):
    """Scripted Stage B: criteria calls (requested_criteria) and the synthesis call (findings)."""
    def respond(system, data):
        assert "condition" in data
        if only and data["condition"]["key"] != only:
            return stage_b({}, label="insufficient_evidence")(system, data)
        if "requested_criteria" in data:
            out = []
            for cid in data["requested_criteria"]:
                spec = criteria.get(cid)
                if spec is None:
                    out.append({"id": cid, "status": "insufficient_evidence", "evidence": [], "counter_evidence": [], "timeframe": "unclear", "duration_note": ""})
                else:
                    status, needles = spec
                    out.append({"id": cid, "status": status, "evidence": [{"ref": a, "basis": "Client reported it."} for a in (_maybe(data, n) for n in needles) if a],
                                "counter_evidence": [], "timeframe": "current", "duration_note": ""})
            return {"criteria": out}
        assert "findings" in data
        return {"duration_status": "unknown", "impairment_status": "unknown", "rule_outs": [], "differentials": [],
                "missing_information": ["Has the low mood lasted at least two weeks?"], "prediction": label, "subtype": None,
                "rationale": [{"sentence": s, "refs": [a for a in (_maybe(data, n) for n in needles) if a]} for s, needles in rationale]}
    return respond


def test_prediction_cites_patient_wide_sources_and_drops_other_person_citations(tmp_path):
    runtime = FakeRuntime(stage_b({"mde.depressed_mood": ("met", ["felt down", "brother has been really depressed"]),
                                   "mde.anhedonia": ("met", ["Nothing interests me"])},
                                  rationale=[("Low mood and loss of interest were reported across two sessions.", ["felt down", "Nothing interests me"]),
                                             ("Uncited speculation.", [])]))
    result = predict_condition(SESSIONS, "depressive_episode", runtime, tmp_path / "job")
    found = {f["criterion_id"]: f for f in result["findings"]}
    assert [e["segment_id"] for e in found["mde.depressed_mood"]["evidence"]] == ["p-s1-0"]       # brother citation dropped
    assert found["mde.anhedonia"]["evidence"][0]["session_id"] == "p-s2"
    assert result["prediction"] == "possible" and result["criteria_met_count"] == 2
    assert result["rationale"][0]["segment_ids"] == ["p-s1-0", "p-s2-0"]
    assert result["postprocess"]["removed_sentences"] == ["Uncited speculation."]
    assert result["has_evidence"] and result["as_of_session_id"] == "p-s2"
    sent = runtime.requests[0]["data"]
    assert {o["domain"] for o in sent["observations"]} == {"low_mood"} and sent["questionnaires"][0]["value"] == 14
    enum = runtime.requests[0]["schema"]["properties"]["criteria"]["items"]["properties"]["evidence"]["items"]["properties"]["ref"]["enum"]
    assert set(enum) == {r["id"] for r in sent["sources"]}


def test_history_current_uses_only_the_as_of_session(tmp_path):
    runtime = FakeRuntime(stage_b({"mde.anhedonia": ("met", ["Nothing interests me"])}))
    result = predict_condition(SESSIONS, "depressive_episode", runtime, tmp_path / "job", history="current")
    sent = runtime.requests[0]["data"]
    assert {r["session"] for r in sent["sources"]} == {2} and sent["observations"] == []
    assert result["history"] == "current"


def test_older_sources_dropped_first_to_fit_budget(tmp_path):
    big = [dict(s, segments=[seg(s["session_id"], 100 + i, f"Turn {i}: I felt sad and down and tired, sleep was bad, nothing to enjoy and work was hard. " * 10) for i in range(20)] + s["segments"])
           for s in SESSIONS]
    runtime = FakeRuntime(stage_b({"mde.anhedonia": ("met", ["Nothing interests me"])}), context=4096)
    result = predict_condition(big, "depressive_episode", runtime, tmp_path / "job")
    sent = runtime.requests[0]["data"]
    assert any("left out to fit the context budget" in n for n in result["postprocess"]["notes"])
    assert any(r["session"] == 2 for r in sent["sources"])
    tiny = FakeRuntime(stage_b({}), context=2048)
    with pytest.raises(GenerationError):
        predict_condition(big, "bipolar", tiny, tmp_path / "job2")


def test_criteria_are_chunked_to_fit_and_synthesis_runs_once(tmp_path):
    runtime = FakeRuntime(stage_b({"mania.elevated_mood": ("met", ["felt down"])}), context=3072)
    predict_condition(SESSIONS, "bipolar", runtime, tmp_path / "job")
    crit_calls = [r["data"]["requested_criteria"] for r in runtime.requests if "requested_criteria" in r["data"]]
    synth_calls = [r for r in runtime.requests if "findings" in r["data"]]
    assert len(crit_calls) > 1 and len(synth_calls) == 1
    assessed = [c.id for c in registry().conditions.get("bipolar").assessed_criteria]
    assert sorted(c for call in crit_calls for c in call) == sorted(assessed)      # every criterion exactly once
    cited = {s["id"] for s in synth_calls[0]["data"]["sources"]}
    assert cited <= {s["id"] for s in runtime.requests[0]["data"]["sources"]}       # synthesis sees only cited turns

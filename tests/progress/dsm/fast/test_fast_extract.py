"""Fast Stage A with a scripted model: config, windows and caching layout, schema mapping, guards, merge, risk, questionnaires."""
import json

import pytest

from services.note_generation.runtime import OutputLimitError
from services.progress.dsm.fast import config as fast_cfg
from services.progress.dsm.fast.config import fast_config, group_features
from services.progress.dsm.fast.extract import attach_questions, extract_session_fast, merge, plan_windows, turn_line
from services.progress.dsm.fast.prompts import group_schema, group_task, risk_schema, system_prompt
from services.progress.dsm.fast.questionnaires import derive, item_features
from services.progress.dsm.fast.stage_b import predict_rules
from services.progress.dsm.fast.triage import lexicon_hits
from services.progress.dsm.registry import dsm_registry
from services.retrieval.engine import digest
from tests.progress.dsm.test_roleplay_regression import load_sessions
from tests.progress.fake_runtime import FakeRuntime


def snap(session):
    return {"session_id": session["session_id"], "revision": digest(session["segments"]), "segments": session["segments"]}


def find(data, needle, role=None):
    for line in data["turns"]:
        i, rest = line.split(" ", 1)
        if needle.lower() in rest.lower() and (role is None or rest.startswith(role)):
            return int(i)
    return None


def scripted(rows=None, risks=None, plans=None):
    """rows: {group: [(code, status, needle, extra)]}; risks: [(code, needle)]."""
    rows, risks = rows or {}, risks or []

    def respond(system, data):
        task = data["task"]
        if task.startswith("RISK"):
            out = {"r": [{"c": c, "e": [find(data, n)], "b": "flagged statement"} for c, n in risks if find(data, n) is not None]}
            if "plans:" in task:
                out["pl"] = plans or []
            return out
        group = next((g for g in rows if task.startswith(group_task(g).split(":")[0] + ":")), None)
        out = []
        for code, status, needle, extra in rows.get(group, []):
            i = find(data, needle)
            if i is None:
                continue
            out.append({"k": code, "s": status, "q": "ned", "t": "c", "c": ["m"], "e": [i], "x": [i] if status == "D" else [], "a": [], "b": "short basis",
                        **extra})
        return {"f": out}
    return respond


def as_answers(data, value):
    """Scripted responders write rows as a list with "k"; the model answers every requested code ("N" or a row)."""
    if not isinstance(value, dict) or not isinstance(value.get("f"), list) or not data["task"].startswith("group "):
        return value
    codes = task_codes(data["task"])
    rows = {r["k"]: {k: v for k, v in r.items() if k != "k"} for r in value["f"] if r.get("k") in codes}
    return {"f": {c: rows.get(c, {"e": [], "s": "N"}) for c in codes}}


def task_codes(task):
    return [part.split(" = ")[0].strip() for part in task.split(": ", 1)[1].split(" | ")]


def test_tasks_point_to_candidate_turns_with_neighbours(tmp_path):
    result, rt = run(scripted(), tmp_path=tmp_path)
    elevated = next(r["data"]["task"] for r in rt.requests if r["data"]["task"].startswith("group ELEVATED") and "EM = " in r["data"]["task"])
    em = elevated.split("EM = ", 1)[1].split(" | ")[0]
    ids = [int(x) for x in em.split("[turns ", 1)[1].rstrip("]").split()]
    assert 74 in ids  # "No, never, I wish." answers the question split over turns 72 and 75


def answered(responder):
    return lambda system, data: as_answers(data, responder(system, data))


def run(responder, session=None, tmp_path=None, **kw):
    session = session or load_sessions()[0]
    rt = FakeRuntime(answered(responder), **{k: v for k, v in kw.items() if k in {"context"}})
    return extract_session_fast(snap(session), rt, tmp_path / "work", **{k: v for k, v in kw.items() if k not in {"context"}}), rt


# ---------------------------------------------------------------- config, prompt, schema

def test_config_covers_every_feature_with_unique_codes():
    cfg = fast_config()
    assert len(cfg["features"]) == len(dsm_registry().features.features) == 46
    assert sum(len(group_features(g["key"])) for g in cfg["groups"]) == 46


def test_system_prompt_is_static_and_holds_no_session_text():
    assert system_prompt() == system_prompt()
    assert "UNTRUSTED DATA" in system_prompt()
    assert group_task("social").startswith("group SOCIAL ANXIETY: SF = ") and "AV = avoiding social situations" in group_task("social")


def test_schema_uses_integer_turn_ranges_and_group_codes():
    s = group_schema("social", 10, 40)
    f = s["properties"]["f"]
    assert f["required"] == ["SF", "NE", "SC", "AV", "FD", "PO"]  # every requested code must be answered
    row = f["properties"]["SF"]["anyOf"][1]["properties"]
    assert f["properties"]["SF"]["anyOf"][0]["properties"]["s"]["enum"] == ["N"] and list(row)[0] == "e"  # cite first
    assert row["e"]["items"] == {"type": "integer", "minimum": 10, "maximum": 40} and row["e"]["minItems"] == 1
    assert "g" not in row and "v" not in row  # no social feature has a degree or qualifiers
    assert "g" in group_schema("impex", 0, 5)["properties"]["f"]["properties"]["IS"]["anyOf"][1]["properties"]
    r = risk_schema(0, 9, [{"id": "P1", "text": "walk"}])
    assert set(r["required"]) == {"r", "pl"} and r["properties"]["r"]["items"]["properties"]["e"]["minItems"] == 1


# ---------------------------------------------------------------- windows and the cache layout

def test_short_session_is_one_window_and_every_call_shares_the_prefix(tmp_path):
    result, rt = run(scripted(), tmp_path=tmp_path)
    assert result["windows"] == [[0, 100]]
    main = [r for r in rt.requests if ", check" not in r["data"]["task"]]
    assert len(main) == 12  # risk + 11 calls of at most 5 codes on one cached window, instead of 40 calls in dsm-v1
    turns = [json.dumps(r["data"]["turns"]) for r in rt.requests]
    assert len(set(turns)) == 1 and len({r["system"] for r in rt.requests}) == 1  # only the task line differs
    assert list(rt.requests[0]["data"]) == ["turns", "task"]  # turns first, so the prompt prefix is stable
    assert rt.requests[0]["data"]["task"].startswith("RISK")  # risk first, never gated


def test_long_session_windows_cover_every_turn_with_overlap(tmp_path):
    session = load_sessions()[0]
    result, rt = run(scripted(), session=session, tmp_path=tmp_path, context=2600)
    windows = result["windows"]
    assert len(windows) > 1 and windows[0][0] == 0 and windows[-1][1] == len(session["segments"])
    for (a0, a1), (b0, b1) in zip(windows, windows[1:]):
        assert b0 < a1 and a1 - b0 <= fast_config()["window"]["overlap_turns"]  # overlapping, never a gap
    risk_calls = [r for r in rt.requests if r["data"]["task"] == "RISK task"]
    assert len(risk_calls) == len(windows)  # the risk call runs on every window


def test_rerun_uses_the_checkpoint_cache(tmp_path):
    result, rt = run(scripted(), tmp_path=tmp_path)
    again, rt2 = run(scripted(), tmp_path=tmp_path)
    assert len(rt2.requests) == 0 and again["calls"]["cache_hits"] == len(rt.requests)
    assert again["findings"] == result["findings"]


def test_turn_lines_are_compact():
    assert turn_line(7, {"speaker_role": "CLIENT", "text": "  I  feel\nlow "}) == "7 C: I feel low"
    assert turn_line(8, {"speaker_role": "CLINICIAN", "text": "ok"}) == "8 T: ok"


# ---------------------------------------------------------------- mapping and guards

def test_rows_map_to_dsm_findings_and_unlisted_features_are_gaps(tmp_path):
    rows = {"depressive": [("DM", "P", "feeling very low", {"d": "about two months"}), ("SL", "P", "lie awake", {"v": "insomnia"})],
            "social": [("SF", "D", "not that I'm scared of them", {}), ("AV", "P", "don't have the energy to talk", {})]}
    result, _ = run(scripted(rows), tmp_path=tmp_path)
    f = result["findings"]
    assert f["feat.depressed_mood"]["status"] == "present" and f["feat.depressed_mood"]["onset_or_duration_text"] == "about two months"
    assert f["feat.depressed_mood"]["contexts"] == ["low_mood"] and f["feat.sleep_insomnia_hypersomnia"]["qualifier"] == "insomnia"
    assert f["feat.social_fear_scrutiny"]["status"] == "explicit_denial"
    assert f["feat.social_fear_scrutiny"]["counter_evidence"][0]["kind"] == "denial"
    assert f["feat.anhedonia"]["status"] == "insufficient_evidence" and f["feat.anhedonia"]["basis"] == "not found in the turns"
    assert len(f) == 46
    assert result["version"] == "progress-extract-dsm-fast-v1"


def test_denial_from_a_clinician_turn_is_withheld(tmp_path):
    rows = {"social": [("SF", "D", "Have you", {})]}  # a clinician question cannot be the client's denial

    def respond(system, data):
        if data["task"].startswith("RISK"):
            return {"r": []}
        if "SOCIAL" not in data["task"]:
            return {"f": []}
        i = next(int(l.split(" ", 1)[0]) for l in data["turns"] if " T: " in l)
        return {"f": [{"k": "SF", "s": "D", "q": "-", "t": "c", "c": [], "e": [i], "x": [], "a": [], "b": "denies"}]}
    result, _ = run(respond, tmp_path=tmp_path)
    assert result["findings"]["feat.social_fear_scrutiny"]["status"] == "insufficient_evidence"
    assert any("explicit denial needs a client turn" in w["reason"] for w in result["withheld"])


def test_other_person_guard_and_copy_guard(tmp_path):
    session = load_sessions()[0]
    session["segments"][5] = {**session["segments"][5], "speaker_role": "CLIENT", "text": "My sister cannot sleep at all and she cries every night."}
    long_quote = " ".join(session["segments"][4]["text"].split()[:16])

    def respond(system, data):
        if data["task"].startswith("RISK"):
            return {"r": []}
        if "DEPRESSIVE" not in data["task"]:
            return {"f": []}
        return {"f": [{"k": "SL", "s": "P", "q": "ned", "t": "c", "c": [], "e": [5], "x": [], "a": [], "b": "sleep problem"},
                      {"k": "DM", "s": "P", "q": "ned", "t": "c", "c": [], "e": [4], "x": [], "a": [], "b": long_quote}]}
    result, _ = run(respond, session=session, tmp_path=tmp_path)
    reasons = [w["reason"] for w in result["withheld"]]
    assert result["findings"]["feat.sleep_insomnia_hypersomnia"]["status"] == "insufficient_evidence"
    assert any("another person" in r for r in reasons)
    if len(long_quote.split()) > 15:
        assert any("copies more than" in r for r in reasons)


def test_foreign_ids_never_survive(tmp_path):
    def respond(system, data):
        if data["task"].startswith("RISK"):
            return {"r": [{"c": "passive", "e": [999], "b": "x"}]}
        return {"f": [{"k": "DM", "s": "P", "q": "ned", "t": "c", "c": [], "e": [999], "x": [], "a": [], "b": "x"}]} if "DEPRESSIVE" in data["task"] else {"f": []}
    result, _ = run(respond, tmp_path=tmp_path)
    assert result["findings"]["feat.depressed_mood"]["status"] == "insufficient_evidence"
    assert result["risk_statements"] == []
    assert sum("not supplied" in w["reason"] for w in result["withheld"]) == 2


def test_uncited_absence_is_a_gap():
    from services.progress.dsm.fast.extract import validate_rows
    from services.progress.evidence import aliases_for
    session = load_sessions()[0]
    aliases = aliases_for(session["segments"])
    allowed = {a: {**r, "alias": a} for a, r in aliases.items()}
    value = {"f": {"DM": {"s": "A", "q": "-", "t": "c", "c": [], "e": [], "b": "fine"}}}
    rows, checker = validate_rows(value, session["segments"], allowed, None, ["DM"])
    assert rows == [] or rows[0][1]["status"] == "insufficient_evidence"


# ---------------------------------------------------------------- merge

def f(status, ids, **kw):
    return {"status": status, "frequency": kw.get("frequency", "unclear"), "onset_or_duration_text": kw.get("onset", ""), "time_frame": kw.get("tf", "current_episode"),
            "period_label": "", "qualifier": None, "degree": kw.get("degree"), "contexts": kw.get("contexts", []), "who": "client_report",
            "evidence_segment_ids": ids, "counter_evidence": [], "counter_check": {"searched": True, "found": [], "alternative_explanation": ""}, "basis": "b"}


@pytest.mark.parametrize("a,b,want", [("present", "explicit_denial", "conflicting"), ("subthreshold", "explicit_denial", "conflicting"),
                                      ("present", "subthreshold", "present"), ("present", "absent", "present"), ("explicit_denial", "absent", "explicit_denial"),
                                      ("conflicting", "absent", "conflicting"), ("absent", "absent", "absent")])
def test_merge_rules(a, b, want):
    assert merge(f(a, ["s1"]), f(b, ["s2"]))["status"] == want
    assert merge(f(b, ["s2"]), f(a, ["s1"]))["status"] == want


def test_merge_unions_evidence_and_keeps_denials_as_counter_evidence():
    m = merge(f("present", ["s1"], frequency="occasional", contexts=["low_mood"]), f("explicit_denial", ["s9"]))
    assert m["status"] == "conflicting" and set(m["evidence_segment_ids"]) == {"s1", "s9"}
    assert {"segment_id": "s9", "kind": "denial", "basis": ""} in m["counter_evidence"]
    m2 = merge(f("present", ["s1"], frequency="occasional", degree="clinically_significant"), f("present", ["s2"], frequency="nearly every day", degree="marked"))
    assert m2["frequency"] == "nearly every day" and m2["degree"] == "marked"


# ---------------------------------------------------------------- risk

def test_risk_flags_categorised_and_deduplicated(tmp_path):
    risks = [("passive", "better off without me"), ("passive", "better off without me"), ("sh_recent", "about three weeks ago"), ("no_plan", "No, no plan")]
    result, _ = run(scripted(risks=risks), tmp_path=tmp_path)
    cats = [r["categories"] for r in result["risk_statements"]]
    assert ["suicidal_ideation_passive"] in cats and sum(c == ["suicidal_ideation_passive"] for c in cats) == 1
    assert any("self_harm_recent" in c for c in cats)
    assert all(r["note_kind"] in {"flag", "supporting"} for r in result["risk_statements"])


def test_risk_is_never_gated_even_when_every_group_is_skipped(tmp_path, monkeypatch):
    from services.progress.dsm.fast import extract as ex
    real = ex.triage

    def none_called(*a, **k):
        out = real(*a, **k)
        for g in out["groups"].values():
            g.update(call=False, reason="not discussed (no candidate turns found)")
        return out
    monkeypatch.setattr(ex, "triage", none_called)
    result, rt = run(scripted(risks=[("passive", "better off without me")]), tmp_path=tmp_path)
    assert {r["data"]["task"][:4] for r in rt.requests} == {"RISK"}
    assert result["risk_statements"] and result["findings"]["feat.depressed_mood"]["basis"] == "not discussed (no candidate turns found)"


def test_risk_lexicon_covers_english_roman_urdu_and_urdu():
    segs = [{"id": "a", "text": "Sometimes I think everyone is better off without me."}, {"id": "b", "text": "kabhi kabhi dil karta hai mar jaun"},
            {"id": "c", "text": "میں خودکشی کے بارے میں سوچتا ہوں"}, {"id": "d", "text": "I went on a diet and studied."}]
    assert lexicon_hits(segs) == ["a", "b", "c"]


def test_every_lexicon_turn_is_inside_a_risk_window(tmp_path):
    session = load_sessions()[0]
    result, rt = run(scripted(), session=session, tmp_path=tmp_path, context=2600)
    covered = {i for a, b in result["windows"] for i in range(a, b)}
    index = {s["id"]: i for i, s in enumerate(session["segments"])}
    assert result["triage_lexicon_turns"] and all(index[s] in covered for s in result["triage_lexicon_turns"])


def test_plans_follow_up_in_the_risk_call(tmp_path):
    plans = [{"plan_id": "note-item-1", "text": "Go for a short walk daily"}]

    def respond(system, data):
        if data["task"].startswith("RISK") and ", check" not in data["task"]:
            assert "P1 = Go for a short walk daily" in data["task"]
            return {"r": [], "pl": [{"id": "P1", "s": "partial", "e": [find(data, "walk") or 0], "b": "walked on some days"}]}
        return {"f": []}
    result, _ = run(respond, tmp_path=tmp_path, plans=plans)
    assert result["plan_followups"][0]["plan_id"] == "note-item-1" and result["plan_followups"][0]["status"] == "discussed_partial"


# ---------------------------------------------------------------- retries

def test_output_limit_splits_the_feature_list_not_the_transcript(tmp_path):
    seen = []

    class Limited(FakeRuntime):
        def generate(self, system, data, output=700):
            seen.append(data["task"])
            if "ELEVATED" in data["task"] and task_codes(data["task"]) == ["EM", "IP", "EN", "GR"]:
                self.requests.append({"data": data})
                raise OutputLimitError("Incomplete output")
            return super().generate(system, data, output)
    rt = Limited(answered(scripted()))
    result = extract_session_fast(snap(load_sessions()[0]), rt, tmp_path / "work")
    elevated = [t for t in seen if "ELEVATED" in t]
    assert len(elevated) == 5 and result["groups"]["elevated"]["status"] == "completed" and result["calls"]["splits"] == 1  # 1 failed + 2 halves + 2 chunks
    assert len({json.dumps(r["data"]["turns"]) for r in rt.requests}) == 1  # the same cached turns throughout


# ---------------------------------------------------------------- questionnaires in code

def test_questionnaire_mapping_and_item_9_never_derived():
    m = item_features()
    assert "9" not in m["phq9"] and m["phq9"]["3"] == ["feat.sleep_insomnia_hypersomnia"] and m["phq9"]["8"] == ["feat.psychomotor_observable"]
    phq, gad = derive({"feat.sleep_insomnia_hypersomnia": f("present", ["s1"], frequency="nearly every day"), "feat.fatigue": f("present", ["s2"], frequency="occasional"),
                       "feat.anhedonia": f("explicit_denial", ["s3"]), "feat.depressed_mood": f("subthreshold", ["s4"]), "feat.concentration": f("conflicting", ["s5"]),
                       "feat.worry_excessive": f("present", ["s6"], frequency="more days than not")})
    assert phq["3"]["score"] == 3 and phq["4"]["score"] == 1 and phq["1"] == {**phq["1"], "score": 0, "status": "explicit_denial"}
    assert phq["2"]["score"] == 1 and phq["7"]["score"] is None and phq["5"]["score"] is None  # conflicting and missing are gaps, never 0
    assert gad["3"]["score"] == 2 and gad["1"]["score"] == 2 and "9" not in phq
    assert phq["3"]["evidence_segment_ids"] == ["s1"] and phq["3"]["evidence_strength"] == "indirect"


# ---------------------------------------------------------------- end to end with Stage B in code

def test_roleplay_end_to_end_mocked(tmp_path):
    rows = {"depressive": [("DM", "P", "feeling very low", {"d": "about two months"}), ("AN", "P", "Even watching matches feel like nothing", {}),
                           ("SL", "P", "lie awake till 3 or 4", {}), ("FA", "P", "always tired", {}), ("WG", "P", "I feel guilty all the time", {}),
                           ("CO", "P", "can't concentrate", {}), ("AP", "P", "i skip meals a lot", {})],
            "social": [("SF", "D", "not that I'm scared of them", {}), ("AV", "P", "don't have the energy to talk", {})],
            "elevated": [("EM", "D", "No, never, I wish", {}), ("EN", "D", "No, never, I wish", {})],
            "impex": [("IO", "P", "missed about six", {"g": "c"}), ("XS", "A", "No alcohol or drugs", {}), ("XM", "A", "thyroid and everything was normal", {})]}
    sessions = load_sessions()
    for s in sessions:
        r, _ = run(scripted(rows, risks=[("passive", "better off without me")]), session=s, tmp_path=tmp_path / s["session_id"])
        s["findings"] = [{"feature_key": k, "review_status": "pending", **v} for k, v in r["findings"].items()]
    out = predict_rules(sessions)
    labels = {k: v["label"] for k, v in out["results"].items()}
    assert labels["mde"] == "likely" and labels["sad"] == "not_met" and labels["manic"] == "not_met" and out["model_calls"] == 0


def test_pipeline_selector(monkeypatch):
    monkeypatch.delenv("PROGRESS_DSM_PIPELINE", raising=False)
    assert fast_cfg.dsm_pipeline() == "dsm-v1"  # the default stays dsm-v1 until plan §8 passes
    monkeypatch.setenv("PROGRESS_DSM_PIPELINE", "dsm-fast")
    assert fast_cfg.dsm_pipeline() == "dsm-fast"
    monkeypatch.setenv("PROGRESS_DSM_PIPELINE", "fast")
    with pytest.raises(ValueError):
        fast_cfg.dsm_pipeline()


def test_unflagged_lexicon_turns_trigger_one_risk_recheck(tmp_path):
    asked = []

    def respond(system, data):
        if data["task"].startswith("RISK task, check"):
            asked.append(data["task"])
            i = find(data, "I just want everything to stop")
            return {"r": [{"c": "passive", "e": [i], "b": "wants everything to stop"}]}
        return {"r": []} if data["task"].startswith("RISK") else {"f": []}
    result, _ = run(respond, tmp_path=tmp_path)
    assert len(asked) == 1 and result["risk_rechecks"][0]["rows"] == 1
    assert any("suicidal_ideation_passive" in r["categories"] for r in result["risk_statements"])


def test_no_recheck_when_lexicon_turns_are_already_flagged(tmp_path):
    asked = []

    def respond(system, data):
        if data["task"].startswith("RISK task, check"):
            asked.append(1)
            return {"r": []}
        if data["task"].startswith("RISK"):
            ids = [int(l.split(" ", 1)[0]) for l in lexicon_hits([{"id": l, "text": l.split(": ", 1)[1]} for l in data["turns"]])]
            return {"r": [{"c": "passive", "e": [i], "b": "flag"} for i in ids]}
        return {"f": []}
    run(respond, tmp_path=tmp_path)
    assert asked == []


def test_a_missing_code_answer_is_a_schema_error_and_n_is_a_gap():
    from services.note_generation.runtime import GenerationError
    from services.progress.dsm.fast.extract import validate_rows
    from services.progress.evidence import aliases_for
    session = load_sessions()[0]
    allowed = {a: {**r, "alias": a} for a, r in aliases_for(session["segments"]).items()}
    with pytest.raises(GenerationError):
        validate_rows({"f": {"DM": "N"}}, session["segments"], allowed, None, ["DM", "AN"])
    rows, _ = validate_rows({"f": {"DM": {"e": [], "s": "N"}, "AN": {"e": [], "s": "N"}}}, session["segments"], allowed, None, ["DM", "AN"])
    assert rows == []


def test_flagged_answer_also_cites_the_risk_question_that_drew_it_out():
    segs = [{"id": f"s-t{i}", "speaker_role": role} for i, role in enumerate(["CLIENT", "CLINICIAN", "CLINICIAN", "CLIENT", "CLIENT"], 1)]
    rows = [{"categories": ["self_harm_recent"], "note_kind": "flag", "segment_ids": ["s-t4"], "basis": ""},
            {"categories": ["denial_of_plan_or_intent"], "note_kind": "supporting", "segment_ids": ["s-t5"], "basis": ""}]
    out = attach_questions(rows, segs, {"s-t1", "s-t2", "s-t3"})
    assert out[0]["segment_ids"] == ["s-t2", "s-t3", "s-t4"]  # both clinician risk questions before the answer, in order
    assert out[1]["segment_ids"] == ["s-t5"]  # the turn before is a client turn: nothing added
    assert attach_questions([{"categories": [], "note_kind": "flag", "segment_ids": ["s-t4"], "basis": ""}], segs, set())[0]["segment_ids"] == ["s-t4"]

"""Stage A validation with mocked model outputs (fictional transcripts)."""
from services.progress.extraction import extract_session
from services.progress.validation import Checker, copied_run, other_person_only, validate_extraction
from services.retrieval.engine import digest
from tests.progress.fake_runtime import FakeRuntime, scripted


def seg(i, text, role="CLIENT", sid="fx-s1"):
    return {"id": f"fx-{i}", "session_id": sid, "start_ms": i * 10_000, "end_ms": i * 10_000 + 9_000, "speaker_role": role, "text": text, "revision": 1}


TRANSCRIPT = [
    seg(0, "How have you been sleeping since we last met?", "CLINICIAN"),
    seg(1, "I barely sleep, maybe three hours, every night this week."),
    seg(2, "My sister can't sleep at all either, she is up all night."),
    seg(3, "Do you feel low?", "CLINICIAN"),
    seg(4, "No, my mood is fine actually, not low at all."),
    seg(5, "Two years ago I didn't sleep for a whole week and spent all my savings on a new car."),
    seg(6, "Some days I enjoy things, other days nothing interests me."),
    seg(7, "Honestly I enjoy most things these days, it is only work that is hard."),
    seg(8, "Sometimes I think everyone would be better off without me."),
]


def snapshot(rows=TRANSCRIPT):
    return {"session_id": "fx-s1", "revision": digest(rows), "segments": rows}


def run(tmp_path, responder, **kwargs):
    runtime = FakeRuntime(responder, limit_tasks=kwargs.pop("limit_tasks", None))
    return extract_session(snapshot(), runtime, tmp_path / "job", **kwargs), runtime


def test_other_person_guard_and_first_person_kept():
    assert other_person_only("My sister can't sleep at all either, she is up all night.")
    assert not other_person_only("I barely sleep and my sister worries about me.")
    assert not other_person_only("I feel low.")


def test_other_person_statement_is_not_attributed_to_client(tmp_path):
    result, _ = run(tmp_path, scripted(domains={"sleep": {"rating": 3, "refs_text": ["My sister can't sleep"]}}))
    assert "sleep" not in result["domains"]
    assert any("another person" in l for l in result["limitations"])


def test_rated_value_with_client_evidence_is_kept_and_mapped_to_original_ids(tmp_path):
    result, runtime = run(tmp_path, scripted(domains={"sleep": {"rating": 3, "direction": "reduced", "refs_text": ["How have you been sleeping", "I barely sleep"]}}))
    obs = result["domains"]["sleep"]
    assert obs["status_value"] == "rated" and obs["rating"] == 3 and obs["direction"] == "reduced"
    assert obs["evidence_segment_ids"] == ["fx-0", "fx-1"]                   # aliases mapped back to source ids
    schema = runtime.requests[0]["schema"]
    enum = schema["properties"]["domains"]["properties"]["sleep"]["properties"]["refs"]["items"]["enum"]
    assert set(enum) == {f"T{i}" for i in range(len(TRANSCRIPT))}           # grammar restricted to supplied aliases


def test_explicit_denial_needs_client_turn_and_drops_rating(tmp_path):
    ok, _ = run(tmp_path / "a", scripted(domains={"low_mood": {"status": "explicit_denial", "rating": 2, "refs_text": ["Do you feel low", "my mood is fine"]}}))
    assert ok["domains"]["low_mood"]["status_value"] == "explicit_denial" and ok["domains"]["low_mood"]["rating"] is None
    bad, _ = run(tmp_path / "b", scripted(domains={"low_mood": {"status": "explicit_denial", "refs_text": ["Do you feel low"]}}))
    assert "low_mood" not in bad["domains"]
    assert any("explicit denial needs a client turn" in l for l in bad["limitations"])


def test_conflicting_accounts_are_kept_without_rating(tmp_path):
    result, _ = run(tmp_path, scripted(domains={"interest": {"status": "conflicting", "rating": 1, "refs_text": ["other days nothing interests", "I enjoy most things"]}}))
    assert result["domains"]["interest"] == {**result["domains"]["interest"], "status_value": "conflicting", "rating": None}
    assert len(result["domains"]["interest"]["evidence_segment_ids"]) == 2


def test_past_episode_keeps_past_timeframe(tmp_path):
    result, _ = run(tmp_path, scripted(domains={"sleep": {"rating": 3, "timeframe": "past", "direction": "reduced_need_without_fatigue",
                                                          "duration": "about one week, two years ago", "refs_text": ["Two years ago"]}}))
    obs = result["domains"]["sleep"]
    assert obs["timeframe"] == "past" and obs["duration_text"] == "about one week, two years ago"


def test_foreign_ids_reject_the_item_only():
    checker = Checker({"T0": {**TRANSCRIPT[1], "alias": "T0"}})
    value = {"domains": {"sleep": {"status": "rated", "rating": 2, "refs": ["T99"], "who": "client_report", "basis": "x"},
                         "energy": {"status": "rated", "rating": 1, "refs": ["T0"], "who": "client_report", "basis": "Client reported tiredness."}},
             "limitations": []}
    tasks = [{"kind": "domain", "key": "sleep", "directions": []}, {"kind": "domain", "key": "energy", "directions": []}]
    out = validate_extraction(value, {"tasks": tasks}, checker)
    assert "sleep" not in out["domains"] and "energy" in out["domains"]
    assert any("not supplied" in l for l in checker.limitations)


def test_long_quotations_and_names_are_withheld(tmp_path):
    long_quote = "Two years ago I didn't sleep for a whole week and spent all my savings on a new car."
    assert copied_run(long_quote, [TRANSCRIPT[5]["text"]])
    result, _ = run(tmp_path, scripted(domains={"sleep": {"rating": 2, "basis": long_quote, "refs_text": ["I barely sleep"]},
                                                "energy": {"rating": 2, "basis": "Fictiona reported tiredness.", "refs_text": ["I barely sleep"]}}),
                    names=["Fictiona Person"])
    assert "sleep" not in result["domains"] and "energy" not in result["domains"]
    assert any("consecutive source words" in l for l in result["limitations"])
    assert any("personal name" in l for l in result["limitations"])


def test_phq_item_estimates_and_risk_statements(tmp_path):
    result, _ = run(tmp_path, scripted(items={"phq9:3": {"score": 3, "refs_text": ["I barely sleep"]}},
                                       risks=[{"category": "suicidal_ideation", "refs_text": ["better off without me"]}]))
    assert result["phq_items"]["3"]["score"] == 3 and result["phq_items"]["1"]["status"] == "insufficient_evidence"
    assert "9" not in result["phq_items"]                                    # item 9 is never requested
    assert result["risk_statements"] == [{"category": "suicidal_ideation", "basis": "Statement flagged for review.", "segment_ids": ["fx-8"]}]


def test_truncated_output_is_discarded_and_task_group_split(tmp_path):
    result, runtime = run(tmp_path, scripted(domains={"sleep": {"rating": 2, "refs_text": ["I barely sleep"]}}), limit_tasks=3)
    assert result["domains"]["sleep"]["rating"] == 2
    assert result["groups"]["depressive"]["status"] == "completed" and result["groups"]["depressive"]["stats"]["splits"] >= 1
    # Every accepted response requested at most three tasks; the over-long requests produced nothing.
    sizes = [len(r["data"]["requested"].get("domains", [])) + len(r["data"]["requested"].get("phq_items", [])) for r in runtime.requests]
    assert max(sizes) > 3 and len(result["domains"]) + sum(1 for _ in result["phq_items"]) >= 9


def test_checkpoint_cache_resumes_without_new_calls(tmp_path):
    responder = scripted(domains={"sleep": {"rating": 2, "refs_text": ["I barely sleep"]}})
    first, runtime_a = run(tmp_path, responder)
    second, runtime_b = run(tmp_path, responder)
    assert second["domains"] == first["domains"] and len(runtime_b.requests) == 0


def test_retrieval_mode_limits_sources_to_selected_turns(tmp_path):
    result, runtime = run(tmp_path, scripted(domains={"sleep": {"rating": 2, "refs_text": ["I barely sleep"]}}), evidence_mode="retrieval")
    assert result["groups"]["depressive"]["evidence"] == "retrieval"
    depressive = next(r for r in runtime.requests if "domains" in r["data"]["requested"] and r["data"]["requested"]["domains"][0]["key"] == "low_mood")
    assert 0 < len(depressive["data"]["sources"]) <= len(TRANSCRIPT)


def long_transcript(words_per_turn):
    filler = " ".join(["week"] * (words_per_turn - 6))
    rows = []
    for i in range(40):
        role = "CLINICIAN" if i % 2 == 0 else "CLIENT"
        text = f"How was sleep this {filler}?" if role == "CLINICIAN" else f"I barely sleep most nights {filler}."
        rows.append(seg(i, text, role))
    return rows


def test_full_transcript_is_planned_into_chunks_that_fit(tmp_path):
    rows = long_transcript(25)
    runtime = FakeRuntime(scripted(domains={"sleep": {"rating": 2, "refs_text": ["I barely sleep"]}}))
    result = extract_session({"session_id": "fx-s1", "revision": digest(rows), "segments": rows}, runtime, tmp_path / "job", evidence_mode="full")
    depressive = result["groups"]["depressive"]
    assert depressive["status"] == "completed" and depressive["evidence"] == "full" and depressive["planned_chunks"] > 1
    assert result["domains"]["sleep"]["rating"] == 2 and len(result["phq_items"]) == 8
    for request in runtime.requests:                                        # every call carried the whole transcript
        assert len(request["data"]["sources"]) == 40


def test_too_long_transcript_is_ineligible_in_full_mode_and_falls_back_in_auto(tmp_path):
    rows = long_transcript(75)
    snap = {"session_id": "fx-s1", "revision": digest(rows), "segments": rows}
    full = extract_session(snap, FakeRuntime(scripted()), tmp_path / "a", evidence_mode="full")
    assert full["groups"]["depressive"]["status"] == "ineligible" and full["domains"] == {}
    auto = extract_session(snap, FakeRuntime(scripted()), tmp_path / "b", evidence_mode="auto")
    assert auto["groups"]["depressive"]["evidence"] == "retrieval" and auto["groups"]["depressive"]["status"] == "completed"


# ---------------------------------------------------------------- progress-extract-v2

INDIRECT = [
    seg(0, "How have mornings been?", "CLINICIAN"),
    seg(1, "I keep dragging myself to class and sleeping through two alarms most days."),
    seg(2, "Is your appetite okay?", "CLINICIAN"),
    seg(3, "Eating is normal, same as always."),
    seg(4, "My sister cries every night and never eats."),
]


def v2(tmp_path, responder, prompt="v2", rows=INDIRECT):
    runtime = FakeRuntime(responder)
    return extract_session({"session_id": "fx-s1", "revision": digest(rows), "segments": rows}, runtime, tmp_path / prompt, prompt=prompt), runtime


def test_v2_schema_adds_denial_and_evidence_strength_and_v1_is_unchanged(tmp_path):
    from services.progress.prompts import EXTRACT, EXTRACT_V2
    _, rt2 = v2(tmp_path, scripted())
    _, rt1 = v2(tmp_path, scripted(), prompt="v1")
    item2 = rt2.requests[0]["schema"]["properties"]["phq_items"]["properties"]["1"]
    item1 = rt1.requests[0]["schema"]["properties"]["phq_items"]["properties"]["1"]
    assert item2["properties"]["status"]["enum"] == ["rated", "explicit_denial", "insufficient_evidence"] and "evidence_strength" in item2["required"]
    assert item1["properties"]["status"]["enum"] == ["rated", "insufficient_evidence"] and "evidence_strength" not in item1["properties"]
    assert rt2.requests[0]["system"].startswith(EXTRACT_V2[:200]) and rt1.requests[0]["system"].startswith(EXTRACT[:200])
    assert "prompt" not in rt2.requests[0]["data"]                          # version never sent to the model as data


def test_v2_indirect_rating_and_explicit_denial_scored_zero(tmp_path):
    result, _ = v2(tmp_path, scripted(items={
        "phq9:4": {"score": 2, "evidence_strength": "indirect", "refs_text": ["dragging myself"]},
        "phq9:5": {"status": "explicit_denial", "score": None, "evidence_strength": "direct", "refs_text": ["appetite okay", "Eating is normal"]},
        "phq9:3": {"score": 0, "evidence_strength": "indirect", "refs_text": ["dragging myself"]}}))
    items = result["phq_items"]
    assert items["4"] == {**items["4"], "score": 2, "status": "rated", "evidence_strength": "indirect"}
    assert items["5"]["status"] == "explicit_denial" and items["5"]["score"] == 0 and items["5"]["evidence_strength"] == "direct"
    assert items["3"]["status"] == "explicit_denial" and items["3"]["score"] == 0     # rated 0 = described as normal
    assert items["1"]["status"] == "insufficient_evidence" and items["1"]["score"] is None


def test_v2_keeps_safeguards(tmp_path):
    result, _ = v2(tmp_path, scripted(items={
        "phq9:5": {"score": 3, "evidence_strength": "indirect", "refs_text": ["My sister cries"]},          # other person
        "phq9:2": {"status": "explicit_denial", "score": None, "refs_text": ["How have mornings"]}}))      # clinician only
    assert "5" not in result["phq_items"] and "2" not in result["phq_items"]
    assert any("another person" in l for l in result["limitations"])
    assert any("explicit denial needs a client turn" in l for l in result["limitations"])
    assert "9" not in result["phq_items"]


def test_v2_denials_count_as_assessed_in_totals(tmp_path):
    from services.progress.registry import registry
    from services.progress.store import score_totals
    phq = registry().instruments.get("phq9")
    total, partial, sub = score_totals(phq, {**{str(i): 0 for i in range(1, 9)}, "4": 2})
    assert sub["phq8"] == {"total": 2, "partial": False, "band": "minimal"} and partial is True   # item 9 (clinician-only) missing

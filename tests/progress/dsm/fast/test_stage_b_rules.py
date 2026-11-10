"""Stage B in code: the duration parser and the findings → periods → rules path (no model unless a duration needs it)."""
from datetime import date

import pytest

from services.progress.dsm.fast.durations import parse
from services.progress.dsm.fast.stage_b import build_periods, predict_rules
from tests.progress.dsm.test_roleplay_regression import load_sessions, stage_a_inputs
from tests.progress.fake_runtime import FakeRuntime


@pytest.mark.parametrize("text,days,kind", [
    ("about two months", 61, "approximate"), ("around two months ago", 61, "approximate"), ("for 3 weeks", 21, "exact"),
    ("two weeks", 14, "exact"), ("six months", 183, "exact"), ("half a year", 183, "exact"), ("2 years", 730, "exact"),
    ("a week", 7, "exact"), ("4 days", 4, "exact"), ("more than 6 months", 183, "at_least"), ("past year", 365, "at_least"),
    ("for months", 61, "at_least"), ("for weeks", 14, "at_least"), ("a couple of weeks", 14, "approximate"),
    ("the past few weeks", 14, "approximate"), ("a year and a half", 548, "exact"),
    # Roman Urdu
    ("do hafte se", 14, "exact"), ("teen mahine", 91, "exact"), ("ek saal se zyada", 365, "at_least"), ("6 mahine", 183, "exact"),
    ("pichle saal se", 365, "at_least"),
    # Urdu script, including Urdu digits
    ("دو ہفتے", 14, "exact"), ("۳ مہینے", 91, "exact"), ("ایک سال", 365, "exact"),
])
def test_durations(text, days, kind):
    got = parse(text)
    assert got is not None and got["days"] == days and got["kind"] == kind


@pytest.mark.parametrize("text", ["current_episode", "ongoing", "most days", "since childhood", "", None, "this month I felt"])
def test_unparsed_is_unknown_never_zero(text):
    assert parse(text) is None


def test_vague_quantities_are_lower_bounds():
    assert parse("a few weeks")["days"] == 14 and parse("kuch din")["days"] == 2  # never inflated past a threshold


def test_date_relative_phrases_need_the_session_date():
    assert parse("since summer") is None
    assert parse("since summer", date(2026, 11, 9))["days"] == (date(2026, 11, 9) - date(2026, 6, 1)).days
    assert parse("since March", date(2026, 2, 1))["days"] == (date(2026, 2, 1) - date(2025, 3, 1)).days


# ---------------------------------------------------------------- findings → periods

def seg(sid, role="CLIENT", text="text"):
    return {"id": sid, "speaker_role": role, "start_ms": 0, "end_ms": 1, "text": text}


def finding(key, status="present", tf="current_episode", sid=None, **kw):
    return {"feature_key": key, "status": status, "time_frame": tf, "frequency": kw.pop("frequency", "nearly every day"),
            "evidence_segment_ids": [sid or f"{key}-1"], "counter_evidence": kw.pop("counter", []), "contexts": kw.pop("contexts", ["low_mood"]),
            "review_status": kw.pop("review", "pending"), **kw}


def session(sid, findings, d=None, extra_segments=()):
    segs = [seg(f["evidence_segment_ids"][0]) for f in findings] + list(extra_segments)
    return {"session_id": sid, "index": 0, "segments": segs, "findings": findings, **({"date": d} if d else {})}


DEP = ["feat.depressed_mood", "feat.anhedonia", "feat.sleep_insomnia_hypersomnia", "feat.fatigue", "feat.worthlessness_guilt", "feat.concentration"]


def depressive_session(sid, onset="about three months", d=None, status="present"):
    fs = [finding(k, status, sid=f"{sid}-{k}", **({"onset_or_duration_text": onset} if k == "feat.depressed_mood" else {})) for k in DEP]
    fs.append(finding("imp.occupational", sid=f"{sid}-imp", contexts=["low_mood"]))
    return session(sid, fs, d)


def test_depression_likely_from_findings_with_parsed_duration():
    out = predict_rules([depressive_session("s1")])
    r = out["results"]
    assert out["model_calls"] == 0 and r["mde"]["label"] == "likely"
    dur = next(e for e in r["mde"]["elements"] if e["id"] == "MDE.A.duration")
    assert dur["status"] == "met" and dur["evidence"] and dur["evidence"][0]["segment_id"] == "s1-feat.depressed_mood"
    assert r["mde"]["postprocess"]["method"] == "rules-only" and r["mde"]["postprocess"]["model_label"] is None
    assert r["mde"]["footer"] == "Hypothesis for clinician review; not a diagnosis."


def test_short_cited_duration_blocks_likely():
    assert predict_rules([depressive_session("s1", onset="10 days")])["results"]["mde"]["label"] == "possible"


def test_latest_assessed_session_wins_and_denial_overrides_earlier_present():
    s1 = depressive_session("s1")
    s2 = session("s2", [finding("feat.depressed_mood", "explicit_denial", sid="s2-d"), finding("feat.anhedonia", "absent", sid="s2-a", who="clinician_observation")])
    periods, _, notes = build_periods([s1, s2])
    current = periods[0]["proposals"]
    assert current["feat.depressed_mood"]["status"] == "explicit_denial" and current["feat.fatigue"]["status"] == "present"  # gap filled from s1
    assert any("latest used" in n for n in notes)
    assert predict_rules([s1, s2])["results"]["mde"]["label"] == "not_met"  # both core items now denied or absent


def test_rejected_and_stale_findings_are_never_used():
    s1 = depressive_session("s1")
    for f in s1["findings"]:
        if f["feature_key"] in {"feat.depressed_mood", "feat.anhedonia"}:
            f["review_status"] = "rejected" if f["feature_key"] == "feat.depressed_mood" else "stale"
    assert predict_rules([s1])["results"]["mde"]["label"] == "insufficient_evidence"


def test_episode_window_uses_session_dates():
    old = depressive_session("s1", d=date(2026, 1, 1))
    new = session("s2", [finding("feat.fatigue", sid="s2-f")], d=date(2026, 6, 1))
    periods, _, _ = build_periods([old, new])
    assert set(periods[0]["proposals"]) == {"feat.fatigue"}  # five months earlier is outside the 28-day episode window


def test_past_findings_form_their_own_periods_and_do_not_combine():
    elevated = ["feat.elevated_mood", "feat.reduced_sleep_need", "feat.talkativeness"]
    s1 = session("s1", [finding(k, tf="past_episode", sid=f"a-{k}", period_label="second year of university", onset_or_duration_text="about two weeks",
                                contexts=["elevated_period"]) for k in elevated[:2]]
                 + [finding(elevated[2], tf="past_episode", sid="b", period_label="last winter", contexts=["elevated_period"])])
    periods, _, _ = build_periods([s1])
    past = [p for p in periods if p["time_frame"] == "past"]
    assert len(past) == 2 and {len(p["proposals"]) for p in past} == {1, 2}
    university = next(p for p in past if len(p["proposals"]) == 2)
    assert university["durations"]["manic"]["days"] == 14
    r = predict_rules([s1])["results"]
    assert r["manic"]["label"] != "likely" and r["hypomanic"]["label"] != "likely"  # B-items in different periods never combine


def test_hospitalisation_makes_any_duration_count_for_mania():
    keys = ["feat.elevated_mood", "feat.increased_energy", "feat.grandiosity", "feat.reduced_sleep_need", "feat.talkativeness", "feat.hospitalised_elevated"]
    s1 = session("s1", [finding(k, tf="past_episode", sid=f"m-{k}", period_label="2023", onset_or_duration_text="three days", contexts=["elevated_period"]) for k in keys])
    periods, _, _ = build_periods([s1])
    assert periods[1]["hospitalised"] is True
    dur = next(e for e in predict_rules([s1])["results"]["manic"]["elements"] if e["id"] == "MANIA.A.duration")
    assert dur["status"] == "met"


def test_each_condition_reads_its_own_core_duration():
    s1 = depressive_session("s1", onset="three weeks")
    s1["findings"].append(finding("feat.worry_excessive", sid="w", contexts=["worry_anxiety"], onset_or_duration_text="about a year"))
    s1["segments"].append(seg("w"))
    periods, _, _ = build_periods([s1])
    assert periods[0]["durations"]["mde"]["days"] == 21 and periods[0]["durations"]["gad"]["days"] == 365


def test_onset_in_an_earlier_finding_dates_the_current_episode():
    s1 = session("s1", [finding("feat.depressed_mood", tf="past_episode", sid="o", period_label="around two months ago")])
    s2 = depressive_session("s2", onset="most days")
    periods, _, _ = build_periods([s1, s2])
    assert periods[0]["durations"]["mde"]["days"] == 61 and periods[0]["durations"]["mde"]["evidence"][0]["segment_id"] == "o"


# ---------------------------------------------------------------- the one fallback call

def test_unparsed_duration_uses_one_cited_model_call_only_when_it_matters():
    s1 = depressive_session("s1", onset="since my exams started")
    assert predict_rules([s1])["results"]["mde"]["label"] == "possible"  # no runtime: never a call, duration unknown
    seen = []

    def respond(system, data):
        seen.append(data)
        return {"d": [{"i": 0, "days": 45, "e": [0]}]}
    rt = FakeRuntime(respond)
    out = predict_rules([s1], runtime=rt)
    assert out["model_calls"] == 1 and len(seen) == 1 and out["results"]["mde"]["label"] == "likely"
    assert seen[0]["phrases"][0]["phrase"] == "since my exams started" and "segments" not in seen[0]  # phrases and cited turns only
    assert any("read by the model" in n for n in out["notes"])


def test_uncited_model_duration_is_ignored():
    s1 = depressive_session("s1", onset="since my exams started")
    out = predict_rules([s1], runtime=FakeRuntime(lambda s, d: {"d": [{"i": 0, "days": 45, "e": []}]}))
    assert out["results"]["mde"]["label"] == "possible"


def test_no_call_when_the_duration_cannot_change_a_label():
    s1 = depressive_session("s1", onset="since my exams started")
    for f in s1["findings"]:
        if f["feature_key"] in {"feat.depressed_mood", "feat.anhedonia"}:
            f["status"] = "explicit_denial"
    rt = FakeRuntime(lambda s, d: pytest.fail("no call expected"))
    assert predict_rules([s1], runtime=rt)["model_calls"] == 0


# ---------------------------------------------------------------- role-play (mocked Stage A)

def test_roleplay_labels_in_code(tmp_path):
    sessions = stage_a_inputs(load_sessions(), tmp_path)
    out = predict_rules(sessions)
    r = out["results"]
    assert out["model_calls"] == 0
    assert r["mde"]["label"] == "likely" and r["mdd"]["label"] == "likely"
    assert r["sad"]["label"] == "not_met" and r["gad"]["label"] == "not_met"
    assert r["manic"]["label"] != "likely" and r["bipolar_i"]["label"] in {"insufficient_evidence", "not_met"}  # as dsm-v1
    assert r["pdd"]["label"] == "not_met"  # about two months cited, needs two years


def test_duration_read_from_the_cited_turn_when_the_finding_states_none():
    s1 = depressive_session("s1", onset="")
    s1["segments"] = [seg(f["evidence_segment_ids"][0], text="I have felt like this for about two months now." if f["feature_key"] == "feat.depressed_mood" else "text")
                      for f in s1["findings"]]
    periods, _, _ = build_periods([s1])
    mde = periods[0]["durations"]["mde"]
    assert mde["days"] == 61 and mde["source"] == "cited turn" and mde["evidence"][0]["segment_id"] == "s1-feat.depressed_mood"
    assert predict_rules([s1])["results"]["mde"]["label"] == "likely"


def test_nearby_turn_needs_an_onset_cue_and_a_client_speaker():
    s1 = depressive_session("s1", onset="")
    ids = [f["evidence_segment_ids"][0] for f in s1["findings"]]
    segs = []
    for sid in ids:
        segs.append(seg(sid))
        if sid == "s1-feat.depressed_mood":
            segs.append(seg("n1", role="CLINICIAN", text="It started about six months ago, you said?"))  # clinician: ignored
            segs.append(seg("n2", text="I played cricket for two hours."))  # no onset cue: ignored
    s1["segments"] = segs
    periods, _, _ = build_periods([s1])
    assert periods[0]["durations"]["mde"]["days"] is None

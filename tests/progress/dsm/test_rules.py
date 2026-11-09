"""Rule engine: code computes every DSM label from proposed feature statuses (hand-built fixtures)."""
from services.progress.dsm import rules

from .helpers import NO_CAUSE, REG, cite, depressive, period, prop


def run(periods, extras=None, keys=None):
    return rules.evaluate_all(REG, periods, extras or {}, keys)


def el(result, element_id):
    return next(e for e in result["elements"] if e["id"] == element_id)


def test_mde_threshold_four_versus_five_items():
    assert run([depressive(4)])["mde"]["label"] == "possible"
    five = run([depressive(5)])
    assert five["mde"]["label"] == "likely"
    assert el(five["mde"], "MDE.A")["line"].startswith("A: 5 of 9 present") and "includes A1, A2" in el(five["mde"], "MDE.A")["line"]
    assert five["mdd"]["label"] == "likely"


def test_mde_without_core_item_is_not_likely():
    res = run([depressive(6, core=False)])["mde"]
    assert res["label"] == "insufficient_evidence" and el(res, "MDE.A")["status"] == "unknown"


def test_both_core_items_denied_is_not_met():
    p = {"feat.depressed_mood": prop("explicit_denial"), "feat.anhedonia": prop("absent"), "feat.fatigue": prop()}
    assert run([period(p, days=30)])["mde"]["label"] == "not_met"


def test_duration_unmet_or_unknown():
    assert run([depressive(6, days=10)])["mde"]["label"] == "possible"
    unknown = run([depressive(6, days=None)])["mde"]
    assert unknown["label"] == "possible" and "two weeks" in " ".join(unknown["missing_information"])


def test_substance_cause_cited_downgrades_but_unassessed_exclusion_does_not():
    cause = run([depressive(6, extra={"excl.substance": prop("present")})])["mde"]
    assert cause["label"] == "possible" and el(cause, "MDE.C")["status"] == "contradicted"
    unassessed = run([depressive(6)])["mde"]
    assert unassessed["label"] == "likely" and el(unassessed, "MDE.C")["status"] == "unknown"
    assert any("alcohol" in m for m in unassessed["missing_information"])
    ruled_out = run([depressive(6, extra=NO_CAUSE)])["mde"]
    assert el(ruled_out, "MDE.C")["status"] == "met" and "ruled out" in el(ruled_out, "MDE.C")["line"]


def test_client_denial_makes_a_criterion_conflicting_and_it_does_not_count():
    p = depressive(5)
    p["proposals"]["feat.appetite_weight"] = prop(counter=[cite("seg-9", kind="denial")])
    res = run([p])["mde"]
    row = next(r for r in el(res, "MDE.A")["items"] if r["item"] == "MDE.A3")
    assert row["status"] == "conflicting" and res["label"] == "possible"
    # A clinician's alternative explanation is counter-evidence but not a client denial.
    p["proposals"]["feat.appetite_weight"] = prop(counter=[cite("seg-9", role="CLINICIAN", kind="alternative")])
    assert run([p])["mde"]["label"] == "likely"


def test_a9_counts_only_from_clinician_sources():
    p = depressive(4)
    assert run([p])["mde"]["label"] == "possible"
    res = run([p], {"a9": {"present": True, "source": "accepted risk statement", "evidence": [cite("seg-r")]}})["mde"]
    assert res["label"] == "likely"


def mania_period(n_b, *, elevated=True, irritable=False, days=10, marked=False, hosp=False, pid="P1", tf="current"):
    p = {"feat.increased_energy": prop()}
    if elevated:
        p["feat.elevated_mood"] = prop()
    if irritable:
        p["feat.irritable_period"] = prop()
    for key in ["feat.grandiosity", "feat.reduced_sleep_need", "feat.talkativeness", "feat.racing_thoughts", "feat.distractibility",
                "feat.goal_directed_activity", "feat.risky_activity"][:n_b]:
        p[key] = prop()
    p["feat.functioning_change_elevated"] = prop()
    p["feat.observed_by_others"] = prop()
    p["imp.occupational"] = prop(contexts=["elevated_period"], degree="marked" if marked else "clinically_significant")
    if hosp:
        p["feat.hospitalised_elevated"] = prop()
    return period(p, days=days, hospitalised=hosp, pid=pid, time_frame=tf)


def test_irritable_only_mania_needs_four_b_items():
    assert run([mania_period(3, elevated=False, irritable=True, marked=True)])["manic"]["label"] == "possible"
    assert run([mania_period(4, elevated=False, irritable=True, marked=True)])["manic"]["label"] == "likely"
    assert run([mania_period(3, marked=True)])["manic"]["label"] == "likely"  # elevated mood: three suffice


def test_hypomania_versus_mania():
    hypo = run([mania_period(3, days=5)])
    assert hypo["hypomanic"]["label"] == "likely" and hypo["manic"]["label"] == "possible"  # 5 days: too short, not marked
    marked = run([mania_period(3, days=5, marked=True)])
    assert marked["hypomanic"]["label"] == "not_met"  # marked impairment makes it manic, not hypomanic
    hosp = run([mania_period(3, days=2, hosp=True)])
    assert hosp["manic"]["label"] == "likely" and hosp["hypomanic"]["label"] == "not_met"


def test_b_items_in_different_periods_do_not_combine():
    a = mania_period(2, pid="P1", marked=True)
    b = period({"feat.racing_thoughts": prop(), "feat.risky_activity": prop()}, pid="P2", days=10, time_frame="past")
    assert run([a, b])["manic"]["label"] == "possible"


def test_bipolar_i_from_a_past_cited_manic_episode():
    past = mania_period(4, marked=True, pid="P-past", tf="past", days=14)
    res = run([depressive(5), past])
    assert res["manic"]["label"] == "likely" and res["bipolar_i"]["label"] == "likely"
    assert res["mdd"]["label"] == "not_met"  # MDD.E: a likely manic episode rules out MDD


def test_bipolar_ii_needs_both_episode_types_and_never_manic():
    hypo = mania_period(3, days=5, pid="P-h", tf="past")
    both = run([depressive(5), hypo])
    assert both["bipolar_ii"]["label"] == "likely" and both["bipolar_i"]["label"] != "likely"
    only_hypo = run([hypo])
    assert only_hypo["bipolar_ii"]["label"] != "likely"
    with_mania = run([depressive(5), hypo, mania_period(4, marked=True, pid="P-m", tf="past")])
    assert with_mania["bipolar_ii"]["label"] == "not_met"


def gad_period(days, *, linked=True, n_c=3):
    ctx = ["worry_anxiety"] if linked else ["low_mood"]
    p = {"feat.worry_excessive": prop(), "feat.worry_uncontrollable": prop(), "imp.occupational": prop(contexts=["worry_anxiety"])}
    for key in ["feat.restlessness", "feat.fatigue", "feat.concentration", "feat.irritability", "feat.sleep_worry"][:n_c]:
        p[key] = prop(contexts=ctx)
    return period(p, days=days)


def test_gad_six_month_duration():
    assert run([gad_period(150)])["gad"]["label"] == "possible"
    assert run([gad_period(200)])["gad"]["label"] == "likely"


def test_gad_associated_symptoms_count_only_when_linked_to_worry():
    res = run([gad_period(200, linked=False)])["gad"]
    assert res["label"] == "possible"
    assert all(r["status"] == "not_linked" for r in el(res, "GAD.C")["items"] if r["item"] in {"GAD.C1", "GAD.C2", "GAD.C3"})


def test_social_avoidance_without_fear_does_not_satisfy_a():
    # The role-play pattern: avoids friends because of low energy, and denies being scared of them.
    p = {"feat.social_avoidance": prop(contexts=["low_mood"]),
         "feat.social_fear_scrutiny": prop("explicit_denial", evidence=[cite("seg-58")]),
         "imp.social": prop(contexts=["low_mood"])}
    res = run([period(p)])["sad"]
    assert res["label"] == "not_met"
    assert el(res, "SAD.D")["status"] == "not_met" and "linked to low mood, not social fear" in el(res, "SAD.D")["detail"]


def test_avoidance_alone_is_never_possible_social_anxiety():
    p = {"feat.social_avoidance": prop(contexts=["social_fear"]), "imp.social": prop(contexts=["social_fear"])}
    assert run([period(p)])["sad"]["label"] == "insufficient_evidence"


def test_full_social_anxiety_pattern_and_performance_specifier():
    p = {k: prop(contexts=["social_fear"]) for k in ["feat.social_fear_scrutiny", "feat.fear_negative_evaluation", "feat.social_fear_consistent",
                                                     "feat.social_avoidance", "imp.social", "feat.performance_only"]}
    res = run([period(p, days=400)])["sad"]
    assert res["label"] == "likely" and [s["key"] for s in res["specifiers"]] == ["performance_only"]
    assert any("out of proportion" in m for m in res["missing_information"])  # judgement criterion: asked, not downgraded


def test_severity_and_anxious_distress_specifiers():
    extra = {"feat.keyed_up_tense": prop(), "feat.restlessness": prop(), "feat.fear_awful": prop()}
    res = run([depressive(8, extra=extra)])["mdd"]
    keys = {s["key"]: s for s in res["specifiers"]}
    assert keys["severity"]["value"] == "severe" and res["codes"]["suggested"] == "F32.2 / F33.2"
    assert keys["anxious_distress"]["value"] == "moderate"
    mild = run([depressive(5)])["mdd"]
    assert {s["key"]: s["value"] for s in mild["specifiers"]}["severity"] == "mild"
    assert run([depressive(4)])["mdd"]["specifiers"] == []  # only for likely patterns


def test_marked_impairment_context_and_footer():
    res = run([depressive(5)])["mdd"]
    assert res["footer"] == "Hypothesis for clinician review; not a diagnosis."
    assert res["label_text"].startswith("Criteria pattern consistent with major depressive disorder")
    assert res["codes"]["icd10cm"].startswith("F32")


def test_cyclothymic_needs_a_two_year_subthreshold_course():
    p = {"feat.elevated_mood": prop("subthreshold"), "feat.depressed_mood": prop("subthreshold"), "imp.other": prop()}
    assert run([period(p, days=900)])["cyclothymic"]["label"] == "likely"
    assert run([period(p, days=200)])["cyclothymic"]["label"] != "likely"


def test_nothing_supplied_is_insufficient_evidence_everywhere():
    res = run([])
    assert {v["label"] for v in res.values()} == {"insufficient_evidence"}
    assert not any(v["has_evidence"] for v in res.values())


def test_chronic_differential_is_not_met_when_a_shorter_course_is_cited():
    from .helpers import period
    p = depressive(6, days=60).copy()
    res = run([p])
    assert res["pdd"]["label"] == "not_met" and res["mde"]["label"] == "likely"
    assert run([depressive(6, days=60, extra={})], keys=None)["mde"]["label"] == "likely"
    assert run([depressive(6, days=None)])["pdd"]["label"] == "possible"  # duration unknown: still possible, ask
    assert period

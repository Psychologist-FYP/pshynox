from services.progress import trends
from services.progress.trends import Point


def pts(*values, status="rated", provenance="AI_GENERATED", review="pending"):
    out = []
    for i, v in enumerate(values):
        if v is None:
            out.append(Point(slot=f"s{i}"))
        else:
            out.append(Point(slot=f"s{i}", value=v, status=status, provenance=provenance, review_status=review, record_id=f"r{i}"))
    return out


def test_gaps_are_never_zero_or_carried_forward():
    summary = trends.summary(pts(10, None, 14))
    assert summary["sessions_assessed"] == 2 and summary["sessions_total"] == 3
    assert [d["delta_previous"] for d in summary["deltas"]] == [None, None, 4]
    assert summary["deltas"][2]["previous_slot"] == "s0"
    assert summary["delta_baseline"] == 4
    active = trends.activate(pts(10, None, 14))
    assert active[1].value is None and active[1].gap_reason == "not_assessed"


def test_reliable_change_threshold_boundaries():
    assert [c.delta for c in trends.instrument_changes(pts(10, 15), 5)] == [5]
    assert trends.instrument_changes(pts(10, 14), 5) == []          # T-1: no change
    assert [c.delta for c in trends.instrument_changes(pts(15, 10), 5)] == [-5]
    assert trends.instrument_changes(pts(0, 27), None) == []        # null threshold: no markers
    # Consecutive assessed values skip gaps.
    changes = trends.instrument_changes(pts(10, None, 16), 5)
    assert [(c.from_slot, c.to_slot) for c in changes] == [("s0", "s2")]


def test_pending_ai_values_count_and_rejected_stale_are_excluded():
    pending = pts(5, 12)
    assert len(trends.instrument_changes(pending, 5)) == 1         # pending AI values have full weight
    rejected = [pending[0], Point(slot="s1", value=12, provenance="AI_GENERATED", review_status="rejected", record_id="x")]
    assert trends.instrument_changes(rejected, 5) == []
    stale = [pending[0], Point(slot="s1", value=12, provenance="AI_GENERATED", review_status="stale", record_id="x")]
    assert trends.instrument_changes(stale, 5) == []
    assert trends.activate(stale)[1].gap_reason == "stale"
    assert trends.summary(rejected)["sessions_assessed"] == 1


def test_administered_and_ai_series_are_independent():
    administered = pts(10, 16, provenance="MANUAL", review="accepted")
    ai = pts(10, 11)
    assert len(trends.instrument_changes(administered, 5)) == 1 and trends.instrument_changes(ai, 5) == []
    assert trends.instrument_changes(administered, 5)[0].provenance_of_inputs == "reviewed"
    mixed = [administered[0], ai[1]]
    assert trends.input_provenance(*mixed) == "mixed"
    assert trends.input_provenance(*ai) == "ai"


def test_domain_jump_and_emergence_rules():
    jump = trends.domain_changes(pts(0, 2))
    assert [c.rule_id for c in jump] == [trends.RULE_DOMAIN_JUMP]
    assert trends.domain_changes(pts(0, 1)) == []
    insufficient = [Point(slot=f"s{i}", status="insufficient_evidence", provenance="AI_GENERATED", review_status="pending", record_id=f"r{i}") for i in range(2)]
    rated = Point(slot="s2", value=1, status="rated", provenance="AI_GENERATED", review_status="pending", record_id="r2")
    emergence = trends.domain_changes(insufficient + [rated])
    assert [c.rule_id for c in emergence] == [trends.RULE_DOMAIN_EMERGENCE]
    assert emergence[0].from_slot == "s0" and emergence[0].to_slot == "s2"
    assert trends.domain_changes(insufficient[:1] + [rated]) == []   # one insufficient session is not enough
    # A jump across a gap is not between consecutive sessions.
    assert trends.domain_changes(pts(0, None, 3)) == []


def test_prediction_label_and_probability_changes():
    a = Point(slot="s0", label="possible:", probability=0.30, provenance="AI_GENERATED", review_status="pending", record_id="a")
    b = Point(slot="s1", label="likely:", probability=0.55, provenance="AI_GENERATED", review_status="accepted", record_id="b")
    rules = sorted(c.rule_id for c in trends.prediction_changes([a, b]))
    assert rules == [trends.RULE_PREDICTION_LABEL, trends.RULE_PREDICTION_PROBABILITY]
    c = Point(slot="s1", label="possible:", probability=0.45, provenance="AI_GENERATED", review_status="pending", record_id="c")
    assert trends.prediction_changes([a, c]) == []                  # 0.15 < 0.2 and same label
    assert trends.prediction_changes([a, c], shift=0.15)[0].rule_id == trends.RULE_PREDICTION_PROBABILITY
    subtype = Point(slot="s1", label="possible:bipolar_ii_pattern", provenance="AI_GENERATED", review_status="pending", record_id="d")
    assert trends.prediction_changes([a, subtype])[0].rule_id == trends.RULE_PREDICTION_LABEL


def test_trajectory_rule_with_thresholds():
    assert trends.trajectory([10, 12], 5) is None                   # fewer than three values
    assert trends.trajectory([15, 12, 10], 5) == "decreasing"
    assert trends.trajectory([15, 12, 11], 5) == "stable"            # change -4 = -(T-1), range 4
    assert trends.trajectory([10, 13, 14], 5) == "stable"            # change 4 = T-1, range 4
    assert trends.trajectory([10, 13, 15], 5) == "increasing"
    assert trends.trajectory([10, 16, 11], 5) == "fluctuating"
    assert trends.trajectory([10, None, 12, None, 11], 5) == "stable"
    assert trends.trajectory([1, 2, 3], None) is None

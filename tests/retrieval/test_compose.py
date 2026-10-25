from services.retrieval.compose import compose_draft


SESSION_ID = "session-soap-demo-001"


def test_compose_draft_is_deterministic_preview_with_source_trace() -> None:
    first = compose_draft(SESSION_ID)
    second = compose_draft(SESSION_ID)

    assert first == second
    assert first.preview_only is True
    assert first.draft.id == "preview-session-soap-demo-001"
    assert [field.section for field in first.draft.fields] == ["subjective", "functioning", "objective", "assessment", "plan"]
    assert first.draft.fields[0].provenance == "RETRIEVAL_CANDIDATE"
    assert first.draft.fields[0].evidence_ids == ["seg-soap-001", "seg-soap-003"]
    assert first.draft.fields[1].evidence_ids == ["seg-soap-003", "seg-soap-004"]
    assert first.traces[0].matched_evidence_ids == first.draft.fields[0].evidence_ids


def test_objective_assessment_plan_are_empty_templates_not_clinician_content() -> None:
    composition = compose_draft(SESSION_ID)

    for field in composition.draft.fields[2:]:
        assert field.text == ""
        assert field.evidence_ids == []
        assert field.provenance == "TEMPLATE"


def test_risk_is_separate_pending_review_candidates() -> None:
    composition = compose_draft(SESSION_ID)

    assert composition.risk_review.status == "PENDING"
    assert len(composition.risk_review.candidates) == 1
    assert composition.risk_review.candidates[0].segment_id == "seg-soap-005"
    assert composition.risk_review.candidates[0].label == "search candidates, not a risk assessment"


def test_unknown_preview_session_is_rejected() -> None:
    try:
        compose_draft("session-missing")
    except ValueError as exc:
        assert "Unknown SOAP preview session" in str(exc)
    else:
        raise AssertionError("Expected unknown preview session to fail")

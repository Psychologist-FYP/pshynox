from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from services.api.schemas import (
    AuditEvent, DomainObservationInput, InstrumentItemScore, InstrumentScore, InstrumentScoreInput, ProgressReview, RiskStatement,
)

NOW = datetime(2026, 11, 7, tzinfo=timezone.utc)


def review_info(**extra):
    return {"provenance": "AI_GENERATED", "review_status": "pending", "created_at": NOW, "updated_at": NOW, **extra}


def test_risk_statement_has_no_numeric_field():
    numeric = {name for name, field in RiskStatement.model_fields.items()
               if field.annotation in (int, float, int | None, float | None)}
    assert numeric == set()
    assert not any("score" in name or "level" in name for name in RiskStatement.model_fields)


def test_phq9_item_9_is_never_ai_estimated():
    items = [InstrumentItemScore(item_id=str(i), score=1) for i in range(1, 10)]
    with pytest.raises(ValidationError):
        InstrumentScore(id="x", patient_id="p", instrument_key="phq9", registry_version="v", items=items, **review_info())
    items[-1] = InstrumentItemScore(item_id="9", score=None, status="not_administered")
    InstrumentScore(id="x", patient_id="p", instrument_key="phq9", registry_version="v", items=items, **review_info())
    manual = [InstrumentItemScore(item_id=str(i), score=1) for i in range(1, 10)]
    InstrumentScore(id="x", patient_id="p", instrument_key="phq9", registry_version="v", items=manual,
                    **review_info(provenance="MANUAL", review_status="accepted"))


def test_domain_input_requires_rating_only_when_rated():
    DomainObservationInput(actor="Dr Demo", status_value="rated", rating=2)
    DomainObservationInput(actor="Dr Demo", status_value="insufficient_evidence")
    with pytest.raises(ValidationError):
        DomainObservationInput(actor="Dr Demo", status_value="rated")
    with pytest.raises(ValidationError):
        DomainObservationInput(actor="Dr Demo", status_value="explicit_denial", rating=0)
    with pytest.raises(ValidationError):
        DomainObservationInput(actor="Dr Demo", status_value="rated", rating=4)


def test_review_payload_rules_and_score_anchor():
    with pytest.raises(ValidationError):
        ProgressReview(actor="Dr Demo", action="reject")
    with pytest.raises(ValidationError):
        ProgressReview(actor="Dr Demo", action="edit")
    ProgressReview(actor="Dr Demo", action="edit", value={"rating": 1})
    with pytest.raises(ValidationError):
        InstrumentScoreInput(actor="Dr Demo", instrument_key="phq9", items={"1": 1})
    with pytest.raises(ValidationError):
        InstrumentScoreInput(actor="Dr Demo", instrument_key="phq9", session_id="s", items={"1": 5})


def test_audit_event_session_is_optional_and_patient_link_additive():
    event = AuditEvent(id="a", session_id=None, patient_id="p", note_draft_id=None, event_type="X", actor_name="Dr",
                       occurred_at=NOW, metadata={})
    assert event.session_id is None
    legacy = AuditEvent(id="a", session_id="s", note_draft_id=None, event_type="X", actor_name="Dr", occurred_at=NOW, metadata={})
    assert legacy.patient_id is None

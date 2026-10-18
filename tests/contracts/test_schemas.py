import pytest
from pydantic import ValidationError

from services.api.schemas import EvidenceHit, TranscriptSegment


def test_transcript_fixture_contract() -> None:
    segment = TranscriptSegment(
        id="seg-1",
        session_id="session-1",
        start_ms=0,
        end_ms=10,
        speaker_role="UNKNOWN",
        text="fictional",
        revision=1,
    )
    assert segment.speaker_role == "UNKNOWN"


@pytest.mark.parametrize(
    "payload",
    [
        {"start_ms": -1, "end_ms": 1},
        {"start_ms": 10, "end_ms": 1},
    ],
)
def test_transcript_rejects_invalid_time(payload: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        TranscriptSegment(
            id="seg-1",
            session_id="session-1",
            speaker_role="CLIENT",
            text="fictional",
            revision=1,
            **payload,
        )


def test_evidence_requires_positive_session_and_segment_ids() -> None:
    with pytest.raises(ValidationError):
        EvidenceHit(
            segment_id="",
            session_id="session-1",
            start_ms=0,
            end_ms=1,
            excerpt="text",
            score=1,
            section="subjective",
            query="query",
        )

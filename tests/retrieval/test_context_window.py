from services.retrieval.browse import context_window


def document(segment_id: str, start_ms: int, session_id: str = "session-1") -> dict[str, object]:
    return {"segment_id": segment_id, "session_id": session_id, "start_ms": start_ms}


def test_context_window_handles_first_and_last_segments() -> None:
    docs = [document("s1", 0), document("s2", 100), document("s3", 200)]

    assert [item["segment_id"] for item in context_window("s1", docs)] == ["s1", "s2", "s3"]
    assert [item["segment_id"] for item in context_window("s3", docs)] == ["s1", "s2", "s3"]


def test_context_window_caps_at_session_boundaries_and_supports_single_segment() -> None:
    docs = [document("s1", 0), document("s2", 100), document("other", 0, "session-2")]

    assert [item["segment_id"] for item in context_window("s2", docs, radius=99)] == ["s1", "s2"]
    assert [item["segment_id"] for item in context_window("other", docs, radius=2)] == ["other"]
    assert context_window("missing", docs) == []


def test_context_window_rejects_negative_radius() -> None:
    try:
        context_window("s1", [document("s1", 0)], radius=-1)
    except ValueError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("Expected negative radius to fail")

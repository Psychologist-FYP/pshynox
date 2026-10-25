from services.api.schemas import TranscriptSegment
from services.retrieval.fuzzy import FuzzyQueryError, context_window, fuzzy_search, membership, parse


def segment(segment_id: str, text: str, start_ms: int, session_id: str = "session-1") -> TranscriptSegment:
    return TranscriptSegment(
        id=segment_id,
        session_id=session_id,
        start_ms=start_ms,
        end_ms=start_ms + 100,
        speaker_role="CLIENT",
        text=text,
        revision=1,
    )


DOCS = [
    segment("s1", "client reports worry and poor sleep", 0),
    segment("s2", "sleep is improving", 100),
    segment("s3", "the clinician asks about meals", 200),
    segment("s4", "historically, no self harm thoughts", 300),
    segment("s5", "unrelated gardening discussion", 400, "session-2"),
]


def test_parser_supports_precedence_parentheses_phrases_and_implicit_and() -> None:
    parsed = parse('(worry OR "poor sleep") AND NOT meals')

    assert parsed.terms == ("worry", "poor sleep", "meals")
    assert parsed.root.as_dict()["operator"] == "AND"
    assert parsed.root.as_dict()["children"][1]["operator"] == "NOT"
    assert parse("worry sleep").root.operator == "AND"


def test_parser_rejects_clear_syntax_errors() -> None:
    for query in ("", "AND worry", "worry OR", "(worry", '"'):
        try:
            parse(query)
        except FuzzyQueryError:
            continue
        raise AssertionError(f"Expected query error for {query!r}")


def test_membership_is_normalized_and_exact_beats_partial() -> None:
    exact = membership("sleep", DOCS[1], DOCS)
    partial = membership("sleepy", DOCS[1], DOCS)
    absent = membership("volcano", DOCS[1], DOCS)

    assert 0 <= absent <= partial < exact <= 1


def test_fuzzy_and_or_and_not_follow_set_theoretic_math() -> None:
    and_results = fuzzy_search("worry AND sleep", DOCS)
    or_results = fuzzy_search("worry OR meals", DOCS)
    not_results = fuzzy_search("sleep AND NOT meals", DOCS)

    assert and_results[0].segment_id == "s1"
    assert or_results[0].segment_id == "s1"
    assert {hit.segment_id for hit in not_results} == {"s1", "s2"}
    assert all(0 <= hit.score <= 1 for hit in and_results + or_results + not_results)


def test_strict_exclude_not_is_set_difference() -> None:
    fuzzy = fuzzy_search("sleep AND NOT meals", DOCS, not_mode="fuzzy")
    strict = fuzzy_search("sleep AND NOT meals", DOCS, not_mode="strict-exclude")

    assert {hit.segment_id for hit in strict} == {"s1", "s2"}
    assert {hit.segment_id for hit in fuzzy} >= {"s1", "s2"}
    assert all(hit.score <= 1 for hit in strict)


def test_context_window_is_session_scoped_and_ranked_result_keeps_center_segment() -> None:
    window = context_window("s2", DOCS, radius=1)
    assert [item.id for item in window] == ["s1", "s2", "s3"]
    assert context_window("s5", DOCS, radius=2)[0].id == "s5"
    assert fuzzy_search("worry AND meals", DOCS, window=1)[0].segment_id == "s2"


def test_zero_hits_and_stable_tie_breaking() -> None:
    assert fuzzy_search("nonexistent", DOCS) == []
    ranked = fuzzy_search("sleep", DOCS, k=10)
    assert ranked == sorted(ranked, key=lambda hit: (-hit.score, hit.start_ms, hit.segment_id))

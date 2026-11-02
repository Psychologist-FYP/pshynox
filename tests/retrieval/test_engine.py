"""Fictional engineering cases, not clinical validation or dataset judgments."""
from dataclasses import replace

import numpy as np
import pytest

from services.api.schemas import TranscriptSegment
from services.retrieval.config import RetrievalSettings
from services.retrieval.engine import EvidenceRetrievalProvider, SearchScope, reciprocal_rank_fusion
from services.retrieval.query import QuerySyntaxError, parse_query, tokenize


def turn(id, text, role="CLIENT", session="session-a", revision=1):
    return TranscriptSegment(id=str(id), session_id=session, text=text, speaker_role=role,
                             start_ms=int(id) * 1000, end_ms=int(id) * 1000 + 900, revision=revision)


@pytest.fixture
def turns():
    return [turn(1, "Have you been sleeping less?", "CLINICIAN"),
            turn(2, "No. I sleep four hours because of night shifts."),
            turn(3, "I don't feel anxious anymore."),
            turn(4, "My sister has depression."),
            turn(5, "We agreed to follow up next week.", "CLINICIAN"),
            turn(6, "Neend nahi aati. نیند نہیں آتی۔")]


@pytest.mark.parametrize("method", ["fixture", "bm25", "tfidf"])
def test_methods_preserve_original_evidence_and_context(turns, method):
    engine = EvidenceRetrievalProvider(RetrievalSettings(method=method))
    hit = engine.search('"four hours" AND NOT medication', "session-a", turns)[0]
    assert hit.segment_id == "2"
    assert hit.excerpt == turns[1].text
    assert hit.source_segment_ids == ["1", "2", "3"]
    assert hit.matched_source_segment_ids == ["2"]
    assert hit.sources[0].speaker_role == "CLINICIAN"
    assert [s.matched for s in hit.sources] == [False, True, False]
    assert hit.rank == 1 and hit.window_id and hit.transcript_revision
    assert engine.search('anxious AND NOT anymore', "session-a", turns) == []
    assert engine.search('"sister has depression"', "session-a", turns)[0].excerpt == turns[3].text
    assert engine.search("نیند", "session-a", turns)[0].segment_id == "6"
    assert engine.search("neend AND nahi", "session-a", turns)[0].segment_id == "6"
    assert engine.search("hallucination", "session-a", turns) == []


def test_common_term_in_single_document_has_positive_bm25_score():
    hit = EvidenceRetrievalProvider().search("sleep", "session-a", [turn(1, "sleep sleep")])[0]
    assert hit.score > 0


@pytest.mark.parametrize("method", ["bm25", "tfidf"])
def test_revision_deletion_and_role_changes_never_return_stale_sources(method):
    engine = EvidenceRetrievalProvider(RetrievalSettings(method=method))
    original = [turn(1, "sleep trouble"), turn(2, "exercise routine")]
    first = engine.search("sleep", "session-a", original)[0]
    corrected = [turn(1, "exercise routine", revision=2), original[1]]
    assert engine.search("sleep", "session-a", corrected) == []
    changed = [turn(1, "sleep trouble", role="CLINICIAN", revision=3), original[1]]
    assert engine.search("sleep", "session-a", changed, scope=SearchScope(speaker_role="CLIENT")) == []
    hit = engine.search("sleep", "session-a", changed)[0]
    assert hit.transcript_revision != first.transcript_revision
    assert hit.speaker_role == "CLINICIAN"
    assert engine.search("sleep", "session-a", changed[1:]) == []
    assert engine.search("sleep", "session-a", changed[1:] + [turn(3, "sleep improved")])[0].segment_id == "3"


def test_scope_and_cache_do_not_cross_sessions_or_recordings(turns):
    engine = EvidenceRetrievalProvider(RetrievalSettings(cache_sessions=1))
    with pytest.raises(ValueError, match="requested session"):
        engine.search("sleep", "other", turns)
    scope = SearchScope(patient_id="p1", recording_ids={"1": "r1", "2": "r2"})
    hit = engine.search("sleep", "session-a", turns, scope=scope)[0]
    assert hit.patient_id == "p1" and hit.recording_id == "r2"
    assert hit.source_segment_ids == ["2"]  # independent recordings have separate timelines
    assert engine.search("sleep", "session-a", turns, scope=replace(scope, recording_id="r1")) == []
    assert engine.search("sleep", "session-a", turns, scope=replace(scope, start_ms=3000)) == []
    other = [turn(1, "unrelated", session="session-b")]
    assert engine.search("sleep", "session-b", other) == []
    assert len(engine._indexes) == 1
    engine.invalidate("session-b")
    assert not engine._indexes


def test_meeting_tracks_share_context_and_keep_recording_ids(turns):
    scope = SearchScope(recording_ids={"1": "doctor", "2": "patient"},
                        timeline_ids={"1": "meeting:x", "2": "meeting:x"}, speaker_role="CLIENT")
    hit = EvidenceRetrievalProvider().search("sleep", "session-a", turns[:2], scope=scope)[0]
    assert [s.recording_id for s in hit.sources] == ["doctor", "patient"]


@pytest.mark.parametrize("query", ['"sleep', "sleep AND", "(sleep OR worry", "sleep)", "OR sleep", '""', "()"])
def test_malformed_query_fails_clearly(query):
    with pytest.raises(QuerySyntaxError):
        parse_query(query)


def test_boolean_precedence_phrases_and_negation_tokens():
    assert parse_query("sleep OR worry AND NOT medication").matches(tokenize("sleep medication"))
    assert not parse_query("(sleep OR worry) AND NOT medication").matches(tokenize("sleep medication"))
    assert parse_query("sleep NOT medication").matches(tokenize("sleep without tablets"))
    assert not parse_query('"sleep difficulty"').matches(tokenize("sleep has no difficulty"))
    assert tokenize("No NOT never nahi ４ hours نیند") == ["no", "not", "never", "nahi", "4", "hours", "نیند"]
    assert not parse_query("not anxious").explicit  # lower-case linguistic negation is text


class FakeTokenizer:
    def encode(self, text, add_special_tokens=True):
        return list(text.encode("ascii")) + ([0, 0] if add_special_tokens else [])

    def decode(self, ids, **kwargs):
        return bytes(ids).decode("ascii")

    def num_special_tokens_to_add(self, pair=False):
        return 2


class FakeEncoder:
    """Verifies wiring only; deliberately not an E5 quality substitute."""
    tokenizer = FakeTokenizer()
    max_seq_length = 64

    def __init__(self):
        self.inputs = []

    def encode(self, texts, **kwargs):
        assert kwargs["normalize_embeddings"]
        self.inputs.extend(texts)
        assert all(t.startswith(("query: ", "passage: ")) for t in texts)
        assert all(len(self.tokenizer.encode(t)) <= self.max_seq_length for t in texts if t.startswith("passage:"))
        return np.array([[1., 0.] if "sleep" in t or "rest" in t else [0., 1.] for t in texts])


def test_dense_prefix_limits_semantic_search_and_revision_cache():
    encoder = FakeEncoder()
    engine = EvidenceRetrievalProvider(RetrievalSettings(method="e5"), encoder=encoder)
    turns = [turn(1, "sleep trouble " * 20), turn(2, "sports")]
    hits = engine.search("rest", "session-a", turns)
    assert hits[0].segment_id == "1"
    assert engine.diagnostics["split_source_ids"] == ["1"]
    assert engine.diagnostics["passage_truncations"] == 0
    count = sum(t.startswith("passage:") for t in encoder.inputs)
    engine.search("rest", "session-a", turns)
    assert sum(t.startswith("passage:") for t in encoder.inputs) == count
    assert engine.search('rest AND NOT sleep', "session-a", turns) == []
    engine.search("sports", "session-a", [turn(2, "sports", revision=2)])
    assert sum(t.startswith("passage:") for t in encoder.inputs) > count


def test_hybrid_rrf_and_score_abstention():
    scores = reciprocal_rank_fusion([[1, 2], [2, 3]])
    assert scores[2] == pytest.approx(1 / 62 + 1 / 61)
    assert scores[1] == pytest.approx(1 / 61)
    engine = EvidenceRetrievalProvider(RetrievalSettings(method="hybrid"), encoder=FakeEncoder())
    assert engine.search("sleep", "session-a", [turn(1, "sleep trouble"), turn(2, "sports")])[0].segment_id == "1"
    strict = EvidenceRetrievalProvider(RetrievalSettings(method="e5", min_score=1.1), encoder=FakeEncoder())
    assert strict.search("rest", "session-a", [turn(1, "sleep")]) == []

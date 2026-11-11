"""Urdu Sukhan output (Urdu script, English words in Latin script, no punctuation) through storage, editing and search.

All text is fictional. The segments mimic Urdu Sukhan's style: no punctuation, English words left in Latin script.
"""
import unicodedata
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.main import app, session_factory
from services.api.schemas import TranscriptSegment
from services.retrieval.config import RetrievalSettings
from services.retrieval.engine import EvidenceRetrievalProvider
from services.retrieval.query import parse_query, tokenize
from services.speech.text import clean
from services.speech.worker import process_one
from tests.speech.test_pipeline import queue, session, upload

MIXED = "مجھے آج کل بہت anxiety ہوتی ہے اور office میں panic attacks بھی آتے ہیں"


def mixed_turns():
    return [
        TranscriptSegment(id="1", session_id="s", start_ms=0, end_ms=4000, speaker_role="CLINICIAN", text="آپ کی نیند کیسی ہے", revision=1),
        TranscriptSegment(id="2", session_id="s", start_ms=4000, end_ms=9000, speaker_role="CLIENT", text=MIXED, revision=1),
        TranscriptSegment(id="3", session_id="s", start_ms=9000, end_ms=12000, speaker_role="CLIENT",
                          text="رات کو depressionکی وجہ سے نیند نہیں آتی", revision=1),
        TranscriptSegment(id="4", session_id="s", start_ms=12000, end_ms=15000, speaker_role="CLINICIAN", text="We will follow up next week", revision=1),
    ]


# ---------------------------------------------------------------- stored form

def test_clean_keeps_both_scripts_and_removes_only_layout_controls():
    assert clean(MIXED) == MIXED
    out = clean("\u200f" + MIXED.replace("ہوتی", "ہوتی\u202b") + "\u202c")
    assert out == MIXED
    assert clean("ٹھیک   ہے \u2066okay\u2069 ") == "ٹھیک ہے okay"
    assert clean("ے\u0654") == "ۓ" and unicodedata.is_normalized("NFC", clean("ے\u0654"))
    assert clean("کیوں\u200cکہ") == "کیوں\u200cکہ"   # ZWNJ is Urdu spelling, kept


# ---------------------------------------------------------------- search tokenizer

def test_tokenizer_splits_mixed_lines_into_urdu_and_english_words():
    assert tokenize(MIXED) == ["مجھے", "آج", "کل", "بہت", "anxiety", "ہوتی", "ہے", "اور", "office", "میں",
                               "panic", "attacks", "بھی", "آتے", "ہیں"]
    assert tokenize("depressionکی وجہ") == ["depression", "کی", "وجہ"]      # missing space between scripts
    assert tokenize("Anxiety PANIC") == ["anxiety", "panic"]


def test_tokenizer_folds_arabic_letter_variants_digits_and_joiners():
    assert tokenize("نيند نہيں آتي") == tokenize("نیند نہیں آتی")           # Arabic yeh → Urdu yeh
    assert tokenize("كل") == tokenize("کل")                                  # Arabic kaf → keheh
    assert tokenize("مجهے") == tokenize("مجہے")                              # Arabic heh → heh goal
    assert tokenize("۱۲ بجے ٣") == ["12", "بجے", "3"]
    assert tokenize("سـلام کیوں\u200cکہ") == ["سلام", "کیوںکہ"]               # tatweel, ZWNJ
    assert tokenize("No NOT never nahi ４ hours نیند") == ["no", "not", "never", "nahi", "4", "hours", "نیند"]


def test_boolean_queries_match_mixed_lines():
    tokens = tokenize(MIXED)
    assert parse_query('"panic attacks" AND office').matches(tokens)
    assert parse_query('"بہت anxiety"').matches(tokens)
    assert not parse_query("anxiety AND NOT office").matches(tokens)


@pytest.mark.parametrize("method", ["bm25", "tfidf"])
def test_bm25_finds_urdu_and_english_words_in_mixed_segments(method):
    engine = EvidenceRetrievalProvider(RetrievalSettings(method=method))
    turns = mixed_turns()
    assert engine.search("anxiety", "s", turns)[0].segment_id == "2"
    assert engine.search("نیند", "s", turns)[0].segment_id in {"1", "3"}
    assert engine.search("depression", "s", turns)[0].segment_id == "3"
    assert engine.search("نيند AND depression", "s", turns)[0].segment_id == "3"   # typed with Arabic yeh
    assert engine.search('"panic attacks"', "s", turns)[0].excerpt == MIXED        # excerpt is the stored text


# ---------------------------------------------------------------- storage, editor and API

def mixed_transcriber(path, *, session_id, mode=None):
    texts = ["\u200f" + MIXED.replace("ہوتی", "ہوتی\u202b"), "رات کو depression کی وجہ سے نیند نہیں آتی ہوے\u0654", "\u200e\u200f"]
    return [TranscriptSegment(id=f"seg-{uuid4().hex}", session_id=session_id, start_ms=i * 300, end_ms=i * 300 + 250,
                              speaker_role="UNKNOWN", text=t, revision=1) for i, t in enumerate(texts)], \
        {"asr_mode": "urdu_mixed", "asr_model": "ggml-urdu-sukhan-asr-v1-q8_0.bin", "asr_model_label": "Urdu Sukhan ASR v1 (q8_0)",
         "asr_engine": "whisper.cpp", "language": "ur", "device": "gpu", "gpu_backend": "vulkan", "gpu_device": "Test GPU",
         "vad": True, "model_licence": "CC-BY-NC-4.0", "model_non_commercial": True, "asr_fallback": False, "asr_notices": []}


def test_mixed_segments_are_stored_editable_searchable_and_show_their_model():
    with TestClient(app) as c:
        sid = session(c)
        rid = upload(c, sid).json()["id"]
        queue(c, sid, rid)
        assert process_one(session_factory, mixed_transcriber)
        segments = c.get(f"/api/v1/sessions/{sid}/transcript").json()
        # Stored as clean NFC text; the control-only segment is dropped.
        assert [s["text"] for s in segments] == [MIXED, "رات کو depression کی وجہ سے نیند نہیں آتی ہوۓ"]
        hits = c.get(f"/api/v1/sessions/{sid}/evidence", params={"query": "anxiety"}).json()
        assert hits[0]["segment_id"] == segments[0]["id"] and hits[0]["excerpt"] == MIXED
        assert c.get(f"/api/v1/sessions/{sid}/evidence", params={"query": "نيند"}).json()[0]["segment_id"] == segments[1]["id"]

        # The editor saves mixed text in the same normal form and search sees the edit.
        edited = c.patch(f"/api/v1/sessions/{sid}/transcript/{segments[1]['id']}",
                         json={"text": "\u202bرات کو  insomnia کی وجہ سے نیند نہیں آتی\u202c"})
        assert edited.status_code == 200, edited.text
        assert edited.json()["text"] == "رات کو insomnia کی وجہ سے نیند نہیں آتی" and edited.json()["revision"] == 2
        assert c.get(f"/api/v1/sessions/{sid}/evidence", params={"query": "insomnia"}).json()[0]["segment_id"] == segments[1]["id"]

        # Each recording and job says which model and mode produced the transcript.
        produced = c.get(f"/api/v1/sessions/{sid}/recordings").json()[0]["transcribed_with"]
        assert produced["asr_mode"] == "urdu_mixed" and produced["asr_model_label"] == "Urdu Sukhan ASR v1 (q8_0)"
        assert (produced["asr_engine"], produced["device"], produced["gpu_backend"], produced["model_non_commercial"]) == ("whisper.cpp", "gpu", "vulkan", True)
        job = c.get(f"/api/v1/sessions/{sid}/transcription-jobs").json()[0]
        assert job["asr_model_label"] == "Urdu Sukhan ASR v1 (q8_0)" and job["produced_by"] == produced


def test_recordings_not_yet_transcribed_have_no_producing_model():
    with TestClient(app) as c:
        sid = session(c)
        upload(c, sid)
        assert c.get(f"/api/v1/sessions/{sid}/recordings").json()[0]["transcribed_with"] is None

from sqlalchemy import select, func

from scripts.seed_retrieval_demo import PATIENT_ID, SESSIONS, seed_demo
from services.api.db import (Base, PatientRecord, SessionRecord, TranscriptSegmentRecord,
                             make_engine, make_session_factory)
from services.api.schemas import TranscriptSegment
from services.retrieval.engine import EvidenceRetrievalProvider, SearchScope


def test_demo_is_idempotent_preserves_edits_and_is_searchable(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path}/demo.db")
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    first = seed_demo(factory)
    assert len(first["created"]) == 3
    first_sid, second_sid = SESSIONS
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(PatientRecord)) == 1
        assert db.scalar(select(func.count()).select_from(SessionRecord)) == 2
        segments = [TranscriptSegment(id=s.id, session_id=s.session_id, start_ms=s.start_ms, end_ms=s.end_ms,
                                      text=s.text, revision=s.revision, speaker_role=s.speaker_role)
                    for s in db.scalars(select(TranscriptSegmentRecord).where(TranscriptSegmentRecord.session_id == first_sid))]
        provider = EvidenceRetrievalProvider()
        hit = provider.search('"four hours"', first_sid, segments, scope=SearchScope(patient_id=PATIENT_ID))[0]
        assert hit.segment_id.endswith("turn-002")
        assert provider.search("watercolour", first_sid, segments) == []
        record = db.get(TranscriptSegmentRecord, hit.segment_id)
        record.text = "My edited demo transcript."
        record.revision = 2
        db.commit()
    assert seed_demo(factory)["created"] == []
    with factory() as db:
        edited = db.get(TranscriptSegmentRecord, hit.segment_id)
        assert edited.text == "My edited demo transcript." and edited.revision == 2
        assert db.scalar(select(func.count()).select_from(TranscriptSegmentRecord)) == 28
    engine.dispose()

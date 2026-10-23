from uuid import uuid4
from services.api.db import TranscriptSegmentRecord
from services.api.main import session_factory


def seed_segment(session_id):
    """Explicit fictional test data; application session creation remains empty."""
    with session_factory() as db:
        row = TranscriptSegmentRecord(id=f"test-{uuid4().hex}", session_id=session_id,
            start_ms=0, end_ms=1000, speaker_role="UNKNOWN", text="Fictional test speech.", revision=1)
        db.add(row)
        db.commit()
        return row.id

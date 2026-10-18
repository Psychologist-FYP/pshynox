from fastapi.testclient import TestClient

from services.api.db import SessionRecord, TranscriptSegmentRecord
from services.api.main import app, session_factory


def test_session_ids_are_assigned_sequentially_when_omitted() -> None:
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/sessions",
            json={"session_type": "FOLLOW_UP", "consent": {"fictional_fixture": True}},
        ).json()
        second = client.post(
            "/api/v1/sessions",
            json={"session_type": "FOLLOW_UP", "consent": {"fictional_fixture": True}},
        ).json()
        assert int(second["id"].removeprefix("session-")) == int(first["id"].removeprefix("session-")) + 1
        assert second["pseudonym_id"].startswith("client-")
        ids = [first["id"], second["id"]]

    db = session_factory()
    try:
        db.query(TranscriptSegmentRecord).filter(TranscriptSegmentRecord.session_id.in_(ids)).delete(synchronize_session=False)
        db.query(SessionRecord).filter(SessionRecord.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

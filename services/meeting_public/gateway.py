"""The narrow interface between the public meeting server and the rest of Pshynox.

Every function takes the *guest* (patient) token only. Host tokens are refused here, so the clinician's capability is
never usable from the internet. Results carry no session, patient or transcript information.
"""
from __future__ import annotations

from services.api import meeting_core as core
from services.api.db import MeetingRecord, make_engine, make_session_factory

MeetingError = core.MeetingError
_factory = None


def configure(factory) -> None:
    """Share the clinician API's session factory when both apps run in one process (serve.py)."""
    global _factory
    _factory = factory


def factory():
    global _factory
    if _factory is None:
        _factory = make_session_factory(make_engine())
    return _factory


def _guest(db, mid: str, token: str, *, allow_ended=False) -> MeetingRecord:
    row, _ = core.authorize(db, mid, token, guest_only=True, allow_ended=allow_ended)
    return row


def guest_view(mid: str, token: str) -> dict:
    with factory()() as db:
        row = _guest(db, mid, token, allow_ended=True)
        return {"id": row.id, "role": "CLIENT", "status": row.status, "expires_ms": row.expires_ms, "audio_only": bool(row.audio_only)}


def record_guest_consent(mid: str, token: str) -> dict:
    with factory()() as db:
        row = _guest(db, mid, token)
        core.session_writable(db, row.session_id)
        core.record_guest_consent(db, row)
        return {"recording_consent": True}


def guest_ice_servers(mid: str, token: str) -> dict:
    with factory()() as db:
        row = _guest(db, mid, token)
        return core.ice_for(row)


async def guest_signal(ws, mid: str) -> None:
    await core.run_signal(ws, mid, factory(), allowed_roles={"CLIENT"})


def health() -> dict:
    return {"status": "ok"}

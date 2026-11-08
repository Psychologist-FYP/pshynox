"""Two-person WebRTC meetings: clinician-side routes. A single API process owns live sockets.

Shared logic (tokens, consent, the signaling hub) lives in ``services.api.meeting_core`` so the public meeting-only
server (``services.meeting_public``) can use it without importing clinician routes. Only capability-authenticated
meeting endpoints are accessible to LAN guests. Audio and ASR stay on the hosting computer; chat is ephemeral.
"""
import asyncio
import hashlib
import io
import json
import os
import secrets
import uuid
import wave
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request, WebSocket
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from services.api import meeting_core as core
from services.api import turn
from services.api.db import (MeetingRecord, MeetingChunkRecord, RecordingRecord,
    TranscriptionJobRecord, utc_now)
from services.api.speech_routes import fail, writable, session_record

now_ms = core.now_ms
token_hash = core.token_hash
audit = core.audit


def authorize(db, mid, authorization, host_only=False, allow_ended=False):
    try:
        return core.authorize(db, mid, core.bearer(authorization), host_only=host_only, allow_ended=allow_ended)
    except core.MeetingError as exc:
        fail(exc.code, exc.message, exc.status)


class CreateMeeting(BaseModel):
    recording_consent: Literal[True]
    audio_only: bool | None = None      # default: on in development (keeps relayed traffic small)


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matched: bool


class ConnectionStats(BaseModel):
    """Call-end summary from the clinician browser's getStats(). No addresses: extra fields are rejected."""
    model_config = ConfigDict(extra="forbid")
    relayed: bool
    local_candidate_type: Literal["host", "srflx", "prflx", "relay", "unknown"] = "unknown"
    remote_candidate_type: Literal["host", "srflx", "prflx", "relay", "unknown"] = "unknown"
    bytes_sent: int = Field(ge=0, le=50 * 1024 ** 3)
    bytes_received: int = Field(ge=0, le=50 * 1024 ** 3)
    duration_s: int = Field(ge=0, le=24 * 3600)


def audio_only_default() -> bool:
    return os.getenv("APP_ENV", "development").lower() == "development"


class GuestConsent(BaseModel):
    recording_consent: Literal[True]


def build_meeting_router(db_dependency, factory):
    router = APIRouter(prefix="/api/v1")
    hub = core.HUB

    @router.post("/sessions/{sid}/meetings", status_code=201)
    def create(sid: str, payload: CreateMeeting, db=Depends(db_dependency)):
        writable(db, sid)
        # One open room per session prevents competing recording timelines.
        existing = db.scalar(select(MeetingRecord).where(MeetingRecord.session_id == sid,
            MeetingRecord.status == "OPEN", MeetingRecord.expires_ms > now_ms()))
        if existing:
            fail("MEETING_EXISTS", "This session already has an open meeting. Resume or close it first.")
        session = session_record(db, sid)
        consent = json.loads(session.consent_json)
        consent["recording_consent"] = True
        session.consent_json = json.dumps(consent)
        host, guest = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        mode = core.meeting_mode()
        row = MeetingRecord(id=f"meeting-{uuid.uuid4().hex}", session_id=sid,
            host_hash=token_hash(host), guest_hash=token_hash(guest), status="OPEN",
            created_ms=now_ms(), expires_ms=now_ms() + core.INVITATION_MS, guest_consented=False, mode=mode,
            audio_only=audio_only_default() if payload.audio_only is None else payload.audio_only)
        db.add(row)
        audit(db, row, "MEETING_CREATED", "CLINICIAN")
        db.commit()
        return {"id": row.id, "host_token": host, "guest_token": guest, "mode": mode, "public_origin": core.public_origin(),
                "audio_only": row.audio_only}

    @router.post("/sessions/{sid}/meetings/close")
    async def close_session_meetings(sid: str, db=Depends(db_dependency)):
        session_record(db, sid)
        rows = db.scalars(select(MeetingRecord).where(MeetingRecord.session_id == sid, MeetingRecord.status == "OPEN")).all()
        for row in rows:
            row.status = "ENDED"
            audit(db, row, "MEETING_ENDED", "CLINICIAN")
        db.commit()
        for row in rows:
            await hub.close_meeting(row.id)
            await asyncio.to_thread(turn.revoke_meeting, row.id)
        return {"closed": len(rows)}

    @router.get("/meetings/{mid}")
    def info(mid: str, authorization: str = Header(default=""), db=Depends(db_dependency)):
        row, role = authorize(db, mid, authorization, allow_ended=True)
        result = {"id": row.id, "role": role, "status": row.status, "expires_ms": row.expires_ms, "audio_only": bool(row.audio_only)}
        if role == "CLINICIAN":
            result["session_id"] = row.session_id
            jobs = db.scalars(select(TranscriptionJobRecord).join(MeetingChunkRecord,
                MeetingChunkRecord.recording_id == TranscriptionJobRecord.recording_id)
                .where(MeetingChunkRecord.meeting_id == mid)).all()
            result["jobs"] = [{"id": j.id, "status": j.status, "error_message": j.error_message} for j in jobs]
        return result

    @router.post("/meetings/{mid}/end")
    async def end(mid: str, authorization: str = Header(default=""), db=Depends(db_dependency)):
        row, _ = authorize(db, mid, authorization, host_only=True, allow_ended=True)
        if row.status != "ENDED":
            row.status = "ENDED"
            audit(db, row, "MEETING_ENDED", "CLINICIAN")
            db.commit()
        await hub.close_meeting(mid)
        await asyncio.to_thread(turn.revoke_meeting, mid)
        return {"status": "ENDED"}

    @router.post("/meetings/{mid}/consent")
    def guest_consent(mid: str, payload: GuestConsent, authorization: str = Header(default=""), db=Depends(db_dependency)):
        """The patient's explicit recording consent (LAN mode; the public app has the same route in internet mode)."""
        try:
            row, _ = core.authorize(db, mid, core.bearer(authorization), guest_only=True)
            core.session_writable(db, row.session_id)
        except core.MeetingError as exc:
            fail(exc.code, exc.message, exc.status)
        core.record_guest_consent(db, row)
        return {"recording_consent": True}

    @router.get("/meetings/{mid}/ice-servers")
    def ice_servers(mid: str, authorization: str = Header(default=""), db=Depends(db_dependency)):
        row, _ = authorize(db, mid, authorization)
        return core.ice_for(row)

    @router.post("/meetings/{mid}/verification")
    def verification(mid: str, payload: VerificationResult, authorization: str = Header(default=""), db=Depends(db_dependency)):
        """The clinician compared the 6-digit codes with the patient. Audited; the code itself is not stored."""
        row, _ = authorize(db, mid, authorization, host_only=True)
        if payload.matched:
            row.verification_confirmed_at = utc_now()
        audit(db, row, "MEETING_VERIFICATION_CONFIRMED" if payload.matched else "MEETING_VERIFICATION_MISMATCH", "CLINICIAN")
        db.commit()
        return {"matched": payload.matched}

    @router.post("/meetings/{mid}/stats")
    def connection_stats(mid: str, payload: ConnectionStats, authorization: str = Header(default=""), db=Depends(db_dependency)):
        """Cost awareness: whether the call was relayed and roughly how many bytes moved. No IP addresses stored."""
        row, _ = authorize(db, mid, authorization, host_only=True, allow_ended=True)
        row.connection_json = json.dumps({**payload.model_dump(), "recorded_ms": now_ms()})
        db.commit()
        return {"saved": True}

    @router.get("/meeting-usage")
    def usage(month: str | None = None, db=Depends(db_dependency)):
        """Monthly totals for the clinician (local only). month = YYYY-MM, default the current UTC month."""
        import datetime as dt
        start = dt.datetime.strptime(month, "%Y-%m").replace(tzinfo=dt.timezone.utc) if month else dt.datetime.now(dt.timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = (start + dt.timedelta(days=32)).replace(day=1)
        rows = db.scalars(select(MeetingRecord).where(MeetingRecord.created_ms >= int(start.timestamp() * 1000),
                                                      MeetingRecord.created_ms < int(end.timestamp() * 1000))).all()
        stats = [json.loads(r.connection_json) for r in rows if r.connection_json]
        relayed = [s for s in stats if s.get("relayed")]
        relayed_bytes = sum(s["bytes_sent"] + s["bytes_received"] for s in relayed)
        return {"month": start.strftime("%Y-%m"), "meetings": len(rows), "with_stats": len(stats), "relayed_meetings": len(relayed),
                "relayed_bytes": relayed_bytes, "estimated_cost_usd": round(relayed_bytes / 1e9 * 0.05, 4),
                "turn_provider": turn.provider(), "audio_only_default": audio_only_default(), "mode": core.meeting_mode()}

    @router.post("/meetings/{mid}/chunks/{chunk_id}", status_code=201)
    async def chunk(mid: str, chunk_id: uuid.UUID, request: Request,
                    speaker_role: Literal["CLINICIAN", "CLIENT"], offset_ms: int = 0,
                    source: Literal["clinician_local", "patient_device", "network_received"] | None = None,
                    offset_uncertainty_ms: int | None = None, rtt_ms: int | None = None,
                    authorization: str = Header(default=""), db=Depends(db_dependency)):
        row, _ = authorize(db, mid, authorization, host_only=True, allow_ended=True)
        # Where the audio came from: the clinician's own mic, the patient's own device (sent over the encrypted data
        # channel), or the patient audio as received over the network (fallback; also the legacy LAN default).
        source = source or ("clinician_local" if speaker_role == "CLINICIAN" else "network_received")
        if (speaker_role == "CLINICIAN") != (source == "clinician_local"):
            fail("INVALID_SOURCE", "This audio source does not match the speaker.", 422)
        for value in (offset_uncertainty_ms, rtt_ms):
            if value is not None and not 0 <= value <= 120000:
                fail("INVALID_TIMING", "Invalid clock-sync values.", 422)
        writable(db, row.session_id)
        if not row.guest_consented or row.started_ms is None or json.loads(session_record(db, row.session_id).consent_json).get("recording_consent") is not True:
            fail("CONSENT_REQUIRED", "Both participants must consent before recording.", 403)
        if not 0 <= offset_ms <= 4 * 60 * 60 * 1000:
            fail("INVALID_OFFSET", "Invalid meeting timestamp.", 422)
        if request.headers.get("content-type", "").split(";")[0] != "audio/wav":
            fail("UNSUPPORTED_AUDIO", "Meeting chunks must be PCM WAV audio.", 415)
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > 8 * 1024 * 1024:
                fail("CHUNK_TOO_LARGE", "Audio chunk exceeds 8 MB.", 413)
        digest = hashlib.sha256(body).hexdigest()
        cid = str(chunk_id)
        old = db.get(MeetingChunkRecord, cid)
        if old:
            saved = db.get(RecordingRecord, old.recording_id)
            if old.meeting_id != mid or old.speaker_role != speaker_role or old.offset_ms != offset_ms or saved.sha256 != digest or (old.source or source) != source:
                fail("CHUNK_CONFLICT", "A different audio chunk already uses this identifier.")
            return {"recording_id": old.recording_id}
        try:
            with wave.open(io.BytesIO(body), "rb") as wav:
                rate, frames = wav.getframerate(), wav.getnframes()
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE" or not 8000 <= rate <= 48000 or not 0 < frames <= rate * 61:
                    raise ValueError()
                if len(wav.readframes(frames)) != frames * 2:
                    raise ValueError()
                duration = round(frames * 1000 / rate)
        except (wave.Error, EOFError, ValueError):
            fail("INVALID_CHUNK", "Use a complete mono PCM WAV chunk of at most 61 seconds.", 422)
        root = Path(os.getenv("LOCAL_DATA_DIR", "data")).resolve() / "recordings"
        root.mkdir(parents=True, exist_ok=True)
        rid, jid = f"recording-{uuid.uuid4().hex}", f"job-{uuid.uuid4().hex}"
        path = root / f"{rid}.wav"
        committed = False
        try:
            path.write_bytes(body)
            db.add(RecordingRecord(id=rid, session_id=row.session_id, path=str(path), sha256=digest,
                mime="audio/wav", duration_ms=duration, sample_rate=rate, channels=1, created_at=utc_now()))
            db.flush()
            db.add(MeetingChunkRecord(id=cid, meeting_id=mid, recording_id=rid, speaker_role=speaker_role, offset_ms=offset_ms,
                                      source=source, offset_uncertainty_ms=offset_uncertainty_ms, rtt_ms=rtt_ms))
            db.add(TranscriptionJobRecord(id=jid, session_id=row.session_id, recording_id=rid,
                idempotency_key=cid, status="QUEUED", metrics_json="{}", attempt=1, created_at=utc_now(), updated_at=utc_now()))
            db.commit()
            committed = True
        except IntegrityError:
            db.rollback()
            fail("CHUNK_RETRY", "A concurrent upload is finishing. Retry this chunk.")
        finally:
            if not committed:
                path.unlink(missing_ok=True)
        return {"recording_id": rid}

    @router.websocket("/meetings/{mid}/signal")
    async def signal(mid: str, ws: WebSocket):
        # Token is sent in the first frame, never in URL/query/access logs.
        if not core.clinician_request_allowed(ws.headers):
            await ws.close(code=4403, reason="The clinician API is local only")
            return
        await core.run_signal(ws, mid, factory, allowed_roles={"CLINICIAN", "CLIENT"}, hub=hub)

    return router

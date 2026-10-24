"""Two-person WebRTC signaling. A single API process owns live sockets.

Only capability-authenticated meeting endpoints are accessible to LAN guests.
Audio and ASR stay on the hosting computer; chat is ephemeral.
"""
import asyncio
import hashlib
import io
import json
import os
import secrets
import time
import uuid
import wave
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from services.api.db import (MeetingRecord, MeetingChunkRecord, RecordingRecord,
    TranscriptionJobRecord, AuditEventRecord, utc_now)
from services.api.speech_routes import fail, writable, session_record


def now_ms():
    return round(time.time() * 1000)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def authorize(db, mid, authorization, host_only=False, allow_ended=False):
    row = db.get(MeetingRecord, mid)
    token = (authorization or "").removeprefix("Bearer ")
    digest = token_hash(token)
    role = None
    if row and secrets.compare_digest(row.host_hash, digest):
        role = "CLINICIAN"
    elif row and secrets.compare_digest(row.guest_hash, digest):
        role = "CLIENT"
    if not role or (host_only and role != "CLINICIAN"):
        fail("MEETING_ACCESS_DENIED", "This meeting link is invalid.", 403)
    if row.expires_ms < now_ms() or (row.status == "ENDED" and not allow_ended):
        fail("MEETING_CLOSED", "This meeting has ended or its invitation has expired.", 410)
    return row, role


def audit(db, row, event, actor):
    db.add(AuditEventRecord(id=f"audit-{uuid.uuid4().hex}", session_id=row.session_id,
        note_draft_id=None, event_type=event, actor_name=actor, occurred_at=utc_now(), metadata_json="{}"))


class CreateMeeting(BaseModel):
    recording_consent: Literal[True]


class Signal(BaseModel):
    type: Literal["offer", "answer", "ice", "chat", "media", "leave", "recording"]
    data: dict | str | bool | None = None


def build_meeting_router(db_dependency, factory):
    router = APIRouter(prefix="/api/v1")
    rooms: dict[str, dict[str, WebSocket]] = {}
    lock = asyncio.Lock()

    async def send(ws, payload):
        try:
            await ws.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            pass

    async def broadcast(mid, payload):
        for ws in list(rooms.get(mid, {}).values()):
            await send(ws, payload)

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
        row = MeetingRecord(id=f"meeting-{uuid.uuid4().hex}", session_id=sid,
            host_hash=token_hash(host), guest_hash=token_hash(guest), status="OPEN",
            created_ms=now_ms(), expires_ms=now_ms() + 4 * 60 * 60 * 1000, guest_consented=False)
        db.add(row)
        audit(db, row, "MEETING_CREATED", "CLINICIAN")
        db.commit()
        return {"id": row.id, "host_token": host, "guest_token": guest,
                "public_origin": os.getenv("MEETING_PUBLIC_ORIGIN", "").rstrip("/")}

    @router.post("/sessions/{sid}/meetings/close")
    async def close_session_meetings(sid: str, db=Depends(db_dependency)):
        session_record(db, sid)
        rows = db.scalars(select(MeetingRecord).where(MeetingRecord.session_id == sid, MeetingRecord.status == "OPEN")).all()
        for row in rows:
            row.status = "ENDED"
            audit(db, row, "MEETING_ENDED", "CLINICIAN")
        db.commit()
        for row in rows:
            await broadcast(row.id, {"type": "ended"})
            for ws in list(rooms.get(row.id, {}).values()):
                await ws.close(code=1000)
        return {"closed": len(rows)}

    @router.get("/meetings/{mid}")
    def info(mid: str, authorization: str = Header(default=""), db=Depends(db_dependency)):
        row, role = authorize(db, mid, authorization, allow_ended=True)
        result = {"id": row.id, "role": role, "status": row.status, "expires_ms": row.expires_ms,
            "ice_servers": json.loads(os.getenv("MEETING_ICE_SERVERS", "[]"))}
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
        await broadcast(mid, {"type": "ended"})
        for ws in list(rooms.get(mid, {}).values()):
            await ws.close(code=1000)
        return {"status": "ENDED"}

    @router.post("/meetings/{mid}/chunks/{chunk_id}", status_code=201)
    async def chunk(mid: str, chunk_id: uuid.UUID, request: Request,
                    speaker_role: Literal["CLINICIAN", "CLIENT"], offset_ms: int = 0,
                    authorization: str = Header(default=""), db=Depends(db_dependency)):
        row, _ = authorize(db, mid, authorization, host_only=True, allow_ended=True)
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
            if old.meeting_id != mid or old.speaker_role != speaker_role or old.offset_ms != offset_ms or saved.sha256 != digest:
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
            db.add(MeetingChunkRecord(id=cid, meeting_id=mid, recording_id=rid, speaker_role=speaker_role, offset_ms=offset_ms))
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
        await ws.accept()
        role = None
        admitted = False
        try:
            auth = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
            if auth.get("recording_consent") is not True:
                await ws.close(code=4403, reason="Recording consent is required")
                return
            with factory() as db:
                row, role = authorize(db, mid, f"Bearer {auth.get('token', '')}")
                writable(db, row.session_id)
                if json.loads(session_record(db, row.session_id).consent_json).get("recording_consent") is not True:
                    await ws.close(code=4403, reason="Recording consent was withdrawn")
                    return
            async with lock:
                room = rooms.setdefault(mid, {})
                if role in room:
                    await ws.close(code=4409, reason="This participant is already connected")
                    return
                room[role] = ws
                admitted = True
                if role == "CLIENT":
                    with factory() as db:
                        row = db.get(MeetingRecord, mid)
                        row.guest_consented = True
                        audit(db, row, "MEETING_RECORDING_CONSENT", role)
                        db.commit()
                await send(ws, {"type": "joined", "role": role})
                if len(room) == 2:
                    with factory() as db:
                        row = db.get(MeetingRecord, mid)
                        if row.started_ms is None:
                            row.started_ms = now_ms()
                            db.commit()
                        offset = now_ms() - row.started_ms
                    await broadcast(mid, {"type": "ready", "offset_ms": offset})
            window_start, count = time.monotonic(), 0
            while True:
                raw = await ws.receive_text()
                if len(raw) > 65536:
                    await ws.close(code=4400, reason="Message too large")
                    break
                if time.monotonic() - window_start > 10:
                    window_start, count = time.monotonic(), 0
                count += 1
                if count > 200:
                    await ws.close(code=4429, reason="Too many messages")
                    break
                message = Signal.model_validate_json(raw)
                with factory() as db:
                    authorize(db, mid, f"Bearer {auth.get('token', '')}")
                if message.type == "leave":
                    break
                if message.type == "chat" and (not isinstance(message.data, str) or not 0 < len(message.data.strip()) <= 2000):
                    continue
                if message.type == "recording" and role != "CLINICIAN":
                    continue
                peer = rooms.get(mid, {}).get("CLIENT" if role == "CLINICIAN" else "CLINICIAN")
                if peer:
                    await send(peer, {"type": message.type, "data": message.data, "role": role})
        except WebSocketDisconnect:
            pass
        except Exception:
            try:
                await ws.close(code=4403, reason="Meeting unavailable or invalid message")
            except RuntimeError:
                pass
        finally:
            if admitted:
                async with lock:
                    room = rooms.get(mid, {})
                    if room.get(role) is ws:
                        room.pop(role, None)
                    await broadcast(mid, {"type": "peer-left"})
                    if not room:
                        rooms.pop(mid, None)

    return router

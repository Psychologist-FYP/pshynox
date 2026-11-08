"""Meeting logic shared by the clinician API and the public meeting-only server.

This module imports only the database models. It is the narrow interface the public app relies on (through
``services.meeting_public.gateway``), so it must never import clinician route modules.

The live room registry (``HUB``) is in process memory: the clinician API and the public app must run in the same
process (``services.meeting_public.serve``) for a clinician and a patient to meet.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import secrets
import time
import uuid
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel
from sqlalchemy import select

from services.api.db import AuditEventRecord, MeetingRecord, NoteDraftRecord, SessionRecord, utc_now

INVITATION_MS = 4 * 60 * 60 * 1000
FORWARDING_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip", "tailscale-funnel-request")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class MeetingError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ---------------------------------------------------------------- configuration

def meeting_mode() -> str:
    mode = os.getenv("MEETING_MODE", "lan").strip().lower()
    return mode if mode in {"lan", "internet"} else "lan"


def normalise_origin(value: str, *, require_https: bool = False) -> str:
    """Scheme + lowercase host (+ non-default port). Paths, queries and trailing slashes are dropped."""
    value = (value or "").strip()
    if not value:
        if require_https:
            raise ValueError("MEETING_PUBLIC_ORIGIN is required (for example https://name.tailnet.ts.net)")
        return ""
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("MEETING_PUBLIC_ORIGIN must look like https://host[:port]")
    if require_https and parts.scheme != "https":
        raise ValueError("MEETING_PUBLIC_ORIGIN must use https:// in internet mode")
    if parts.username or parts.password:
        raise ValueError("MEETING_PUBLIC_ORIGIN must not contain credentials")
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    default = {"https": 443, "http": 80}[parts.scheme]
    return f"{parts.scheme}://{host}{'' if port in (None, default) else f':{port}'}"


def public_origin() -> str:
    try:
        return normalise_origin(os.getenv("MEETING_PUBLIC_ORIGIN", ""), require_https=meeting_mode() == "internet")
    except ValueError:
        return ""


def is_loopback(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in LOOPBACK_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (address.ipv4_mapped or address).is_loopback if isinstance(address, ipaddress.IPv6Address) else address.is_loopback


def host_name(header: str) -> str:
    header = (header or "").strip()
    if header.startswith("["):
        return header[1:header.find("]")] if "]" in header else header
    return header.rsplit(":", 1)[0] if header.count(":") == 1 else header


def clinician_request_allowed(headers) -> bool:
    """Internet mode: the clinician API answers only requests addressed to a loopback name with no proxy headers.

    If a tunnel were pointed at the clinician port by mistake, requests would carry the public hostname (and usually
    forwarding headers) and are refused. LAN mode keeps its existing rules.
    """
    if meeting_mode() != "internet":
        return True
    if any(headers.get(h) for h in FORWARDING_HEADERS):
        return False
    return is_loopback(host_name(headers.get("host", "")))


# ---------------------------------------------------------------- tokens, sessions, audit

def now_ms() -> int:
    return round(time.time() * 1000)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def bearer(authorization: str | None) -> str:
    return (authorization or "").removeprefix("Bearer ").strip()


def authorize(db, mid: str, token: str, *, host_only=False, guest_only=False, allow_ended=False):
    row = db.get(MeetingRecord, mid)
    digest = token_hash(token or "")
    role = None
    if row and secrets.compare_digest(row.host_hash, digest):
        role = "CLINICIAN"
    elif row and secrets.compare_digest(row.guest_hash, digest):
        role = "CLIENT"
    if not role or (host_only and role != "CLINICIAN") or (guest_only and role != "CLIENT"):
        raise MeetingError("MEETING_ACCESS_DENIED", "This meeting link is invalid.", 403)
    if row.expires_ms < now_ms() or (row.status == "ENDED" and not allow_ended):
        raise MeetingError("MEETING_CLOSED", "This meeting has ended or its invitation has expired.", 410)
    return row, role


def session_writable(db, sid: str) -> SessionRecord:
    session = db.get(SessionRecord, sid)
    if not session:
        raise MeetingError("SESSION_NOT_FOUND", "Create a session before uploading audio.", 404)
    draft = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == sid))
    if draft and draft.workflow_status != "DRAFT":
        raise MeetingError("NOTE_TERMINAL", "A decided session cannot receive new recordings or transcripts.")
    return session


def recording_consented(session: SessionRecord) -> bool:
    return json.loads(session.consent_json).get("recording_consent") is True


def audit(db, row: MeetingRecord, event: str, actor: str, metadata: dict | None = None) -> None:
    db.add(AuditEventRecord(id=f"audit-{uuid.uuid4().hex}", session_id=row.session_id, note_draft_id=None, event_type=event,
                            actor_name=actor, occurred_at=utc_now(), metadata_json=json.dumps(metadata or {})))


def record_guest_consent(db, row: MeetingRecord) -> bool:
    """Idempotent; returns True when consent was newly recorded."""
    if row.guest_consented:
        return False
    row.guest_consented = True
    audit(db, row, "MEETING_RECORDING_CONSENT", "CLIENT")
    db.commit()
    return True


def ice_for(row: MeetingRecord) -> dict:
    """Fresh ICE servers for one participant; TURN credentials never outlive the invitation."""
    from services.api import turn
    remaining = max(0, (row.expires_ms - now_ms()) // 1000)
    issued = turn.issue(row.id, remaining)
    if not issued["relay"] and (static := ice_servers_from_env()):
        issued = {**issued, "ice_servers": static}          # explicit MEETING_ICE_SERVERS (e.g. LAN) when no provider
    return {"ice_servers": issued["ice_servers"], "relay_configured": issued["relay"], "warning": issued["warning"],
            "ttl": issued["ttl"], "expires_ms": row.expires_ms}


def ice_servers_from_env() -> list:
    try:
        value = json.loads(os.getenv("MEETING_ICE_SERVERS", "[]"))
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------- signaling hub

class Signal(BaseModel):
    type: Literal["offer", "answer", "ice", "chat", "media", "leave", "recording"]
    data: dict | str | bool | None = None


class Hub:
    """Two-person rooms keyed by meeting id. One instance per process, shared by both apps."""

    def __init__(self):
        self.rooms: dict[str, dict[str, object]] = {}
        self.lock = asyncio.Lock()

    @staticmethod
    async def send(ws, payload) -> None:
        try:
            await ws.send_json(payload)
        except Exception:  # peer went away mid-send
            pass

    async def broadcast(self, mid: str, payload) -> None:
        for ws in list(self.rooms.get(mid, {}).values()):
            await self.send(ws, payload)

    async def close_meeting(self, mid: str) -> None:
        await self.broadcast(mid, {"type": "ended"})
        for ws in list(self.rooms.get(mid, {}).values()):
            try:
                await ws.close(code=1000)
            except Exception:
                pass

    def peer(self, mid: str, role: str):
        return self.rooms.get(mid, {}).get("CLIENT" if role == "CLINICIAN" else "CLINICIAN")


HUB = Hub()


async def run_signal(ws, mid: str, factory, *, allowed_roles: set[str], hub: Hub = HUB) -> None:
    """One participant's signaling socket. The token arrives in the first frame, never in the URL."""
    from starlette.websockets import WebSocketDisconnect
    await ws.accept()
    role = None
    admitted = False
    try:
        auth = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        token = str(auth.get("token", ""))
        if auth.get("recording_consent") is not True:
            await ws.close(code=4403, reason="Recording consent is required")
            return
        with factory() as db:
            row, role = authorize(db, mid, token)
            if role not in allowed_roles:
                raise MeetingError("MEETING_ACCESS_DENIED", "This meeting link is invalid.", 403)
            session = session_writable(db, row.session_id)
            if not recording_consented(session):
                await ws.close(code=4403, reason="Recording consent was withdrawn")
                return
        async with hub.lock:
            room = hub.rooms.setdefault(mid, {})
            if role in room:
                await ws.close(code=4409, reason="This participant is already connected")
                return
            room[role] = ws
            admitted = True
            if role == "CLIENT":
                with factory() as db:
                    record_guest_consent(db, db.get(MeetingRecord, mid))
            await hub.send(ws, {"type": "joined", "role": role})
            if len(room) == 2:
                with factory() as db:
                    row = db.get(MeetingRecord, mid)
                    if row.started_ms is None:
                        row.started_ms = now_ms()
                        db.commit()
                    offset = now_ms() - row.started_ms
                await hub.broadcast(mid, {"type": "ready", "offset_ms": offset})
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
                authorize(db, mid, token)
            if message.type == "leave":
                break
            if message.type == "chat" and (not isinstance(message.data, str) or not 0 < len(message.data.strip()) <= 2000):
                continue
            if message.type == "recording" and role != "CLINICIAN":
                continue
            peer = hub.peer(mid, role)
            if peer:
                await hub.send(peer, {"type": message.type, "data": message.data, "role": role})
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await ws.close(code=4403, reason="Meeting unavailable or invalid message")
        except Exception:
            pass
    finally:
        if admitted:
            async with hub.lock:
                room = hub.rooms.get(mid, {})
                if room.get(role) is ws:
                    room.pop(role, None)
                await hub.broadcast(mid, {"type": "peer-left"})
                if not room:
                    hub.rooms.pop(mid, None)

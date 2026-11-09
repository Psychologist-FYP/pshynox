"""Short-lived ICE servers (STUN/TURN) for one meeting participant. Server side only.

Provider keys come from the environment (.env) and never reach a browser, a response or a log line. Browsers receive
only the provider's short-lived credentials. Nothing is cached across meetings: every call generates fresh credentials.

    TURN_PROVIDER=cloudflare (default when CLOUDFLARE_TURN_KEY_ID and CLOUDFLARE_TURN_API_TOKEN are set) | metered | none
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

import httpx

log = logging.getLogger("pshynox.turn")
CLOUDFLARE = "https://rtc.live.cloudflare.com/v1/turn/keys/{key}/credentials"
STUN_ONLY = [{"urls": ["stun:stun.cloudflare.com:3478"]}]
NO_TURN_WARNING = "No TURN relay is configured: the connection may fail on some networks."
FAILED_WARNING = "The TURN relay could not be reached: the connection may fail on some networks."
PORT_53 = re.compile(r"^[a-z]+:[^?]*:53(?:\?|$)", re.I)
TIMEOUT = 5.0

# Cloudflare usernames issued per meeting, kept only in memory for best-effort revocation when the meeting ends.
_issued: dict[str, list[str]] = {}
_lock = threading.Lock()


def client() -> httpx.Client:     # replaced in tests with an httpx.MockTransport client
    return httpx.Client(timeout=TIMEOUT)


def provider() -> str | None:
    name = os.getenv("TURN_PROVIDER", "").strip().lower()
    if name in {"none", "off"}:
        return None
    if name == "metered":
        return "metered" if os.getenv("METERED_APP_SUBDOMAIN") and os.getenv("METERED_API_KEY") else None
    if name in {"", "cloudflare"} and os.getenv("CLOUDFLARE_TURN_KEY_ID") and os.getenv("CLOUDFLARE_TURN_API_TOKEN"):
        return "cloudflare"
    return None


def filter_servers(servers) -> list[dict]:
    """Keep the provider's entries unchanged except URLs on port 53 (blocked by browsers, per Cloudflare's docs)."""
    out = []
    for entry in servers if isinstance(servers, list) else []:
        if not isinstance(entry, dict):
            continue
        urls = entry.get("urls")
        urls = [urls] if isinstance(urls, str) else list(urls or [])
        kept = [u for u in urls if isinstance(u, str) and not PORT_53.match(u)]
        if kept:
            out.append({**entry, "urls": kept})
    return out


def ttl_for(remaining_s: int) -> int:
    configured = int(os.getenv("CLOUDFLARE_TURN_TTL_SECONDS", "14400") or 14400)
    return max(60, min(configured, int(remaining_s)))


def _cloudflare(ttl: int) -> list[dict]:
    key, token = os.environ["CLOUDFLARE_TURN_KEY_ID"], os.environ["CLOUDFLARE_TURN_API_TOKEN"]
    with client() as c:
        response = c.post(f"{CLOUDFLARE.format(key=key)}/generate-ice-servers", json={"ttl": ttl},
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    response.raise_for_status()
    return response.json()["iceServers"]


def _metered() -> list[dict]:
    url = f"https://{os.environ['METERED_APP_SUBDOMAIN']}.metered.live/api/v1/turn/credentials"
    with client() as c:
        response = c.get(url, params={"apiKey": os.environ["METERED_API_KEY"]})
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, list) else data.get("iceServers", [])


def has_relay(servers: list[dict]) -> bool:
    return any(str(u).startswith(("turn:", "turns:")) for s in servers for u in (s.get("urls") if isinstance(s.get("urls"), list) else [s.get("urls")]))


def issue(meeting_id: str, remaining_s: int) -> dict:
    """ICE servers for one participant of ``meeting_id`` whose invitation has ``remaining_s`` seconds left."""
    name = provider()
    if name is None:
        return {"ice_servers": STUN_ONLY, "provider": None, "relay": False, "warning": NO_TURN_WARNING, "ttl": None}
    started = time.monotonic()
    ttl = ttl_for(remaining_s)
    try:
        servers = filter_servers(_cloudflare(ttl) if name == "cloudflare" else _metered())
        if not servers:
            raise ValueError("empty ICE server list")
    except Exception as exc:  # never include response bodies or headers: they may carry credentials
        log.warning("turn.credentials provider=%s failed (%s) latency_ms=%d", name, type(exc).__name__, (time.monotonic() - started) * 1000)
        return {"ice_servers": STUN_ONLY, "provider": name, "relay": False, "warning": FAILED_WARNING, "ttl": None}
    log.info("turn.credentials provider=%s ok latency_ms=%d", name, (time.monotonic() - started) * 1000)
    if name == "cloudflare":
        with _lock:
            _issued.setdefault(meeting_id, []).extend(s["username"] for s in servers if s.get("username"))
    return {"ice_servers": servers, "provider": name, "relay": has_relay(servers), "warning": None, "ttl": ttl if name == "cloudflare" else None}


def revoke_meeting(meeting_id: str) -> int:
    """Best effort: revoke Cloudflare credentials issued for this meeting. Returns how many were revoked."""
    with _lock:
        usernames = _issued.pop(meeting_id, [])
    if not usernames or provider() != "cloudflare":
        return 0
    key, token, done = os.environ["CLOUDFLARE_TURN_KEY_ID"], os.environ["CLOUDFLARE_TURN_API_TOKEN"], 0
    try:
        with client() as c:
            for username in usernames:
                response = c.post(f"{CLOUDFLARE.format(key=key)}/{username}/revoke", headers={"Authorization": f"Bearer {token}"})
                done += response.status_code in (200, 204)
    except Exception as exc:
        log.warning("turn.revoke failed (%s)", type(exc).__name__)
    log.info("turn.revoke revoked=%d of %d", done, len(usernames))
    return done

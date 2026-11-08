"""Public meeting-only ASGI app. Bind to 127.0.0.1 only; Tailscale Funnel forwards the internet to it.

Every request is untrusted. Routes (everything else is 404):

    GET  /healthz                                   liveness only
    GET  /meeting/<meeting-id>                      patient-only page (apps/web/dist-meeting/meeting.html)
    GET  /assets/<file>, /images/<file>, /meeting-pcm.js   files of that build
    GET  /api/v1/meetings/<id>                      guest view (status, expiry)
    POST /api/v1/meetings/<id>/consent              patient recording consent
    GET  /api/v1/meetings/<id>/ice-servers          ICE servers for a valid invitation
    WS   /api/v1/meetings/<id>/signal               signaling for that meeting (guest token in the first frame)
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from fastapi import FastAPI, Header, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from services.api.meeting_core import is_loopback, host_name, public_origin
from services.meeting_public import gateway
from services.meeting_public.limits import Limiter

ROOT = Path(__file__).resolve().parents[2]
DIST = Path(os.getenv("MEETING_PUBLIC_DIST", str(ROOT / "apps/web/dist-meeting")))
MEETING_ID = re.compile(r"^meeting-[a-f0-9]{32}$")
ASSET = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
MAX_BODY = 1024
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob: mediastream:; "
       "connect-src 'self'; worker-src 'self'; font-src 'self'; manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP, "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(self), microphone=(self), geolocation=(), payment=(), usb=()",
    "Strict-Transport-Security": "max-age=31536000", "X-Frame-Options": "DENY",
}
log = logging.getLogger("pshynox.meeting_public")


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def not_found() -> JSONResponse:
    return error(404, "NOT_FOUND", "Not found.")


def create_app(*, dist: Path | None = None, limiter: Limiter | None = None) -> FastAPI:
    dist = Path(dist or DIST)
    limiter = limiter or Limiter()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.limiter = limiter
    seen_headers: set[str] = set()

    def origin_ok(origin: str | None) -> bool:
        expected = public_origin()
        return bool(expected) and origin == expected

    def host_ok(host: str | None) -> bool:
        """Answer only for the public hostname (DNS-rebinding guard) or loopback (the launcher's local health check)."""
        expected = public_origin()
        name = host_name(host or "").lower()
        return is_loopback(name) or (bool(expected) and name == host_name(expected.split("://", 1)[1]))

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not host_ok(request.headers.get("host")):
            response = error(421, "WRONG_HOST", "Unknown host.")
        else:
            length = request.headers.get("content-length")
            if (length and (not length.isdigit() or int(length) > MAX_BODY)) or request.headers.get("transfer-encoding"):
                response = error(413, "TOO_LARGE", "Request too large.")
            else:
                response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers[k] = v
        if not request.url.path.startswith(("/assets/", "/images/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_errors(_request, exc):
        return not_found() if exc.status_code in (404, 405) else error(exc.status_code, "ERROR", "Request failed.")

    def meeting_error(exc: gateway.MeetingError, mid: str) -> JSONResponse:
        if exc.status == 403:
            limiter.failed(mid)
        return error(exc.status, exc.code, exc.message)

    def bearer(authorization: str) -> str:
        return (authorization or "").removeprefix("Bearer ").strip()

    def limited(route: str, mid: str | None) -> JSONResponse | None:
        if mid is not None and not MEETING_ID.match(mid):
            return not_found()
        if not limiter.allow(route, mid):
            return error(429, "RATE_LIMITED", "Too many requests. Wait a minute and try again.")
        return None

    @app.get("/healthz")
    def healthz(request: Request):
        if os.getenv("MEETING_DIAG_HEADERS") == "1":
            names = sorted({k.lower() for k in request.headers.keys()} - seen_headers)
            if names:
                seen_headers.update(names)
                log.warning("public request header names (diagnostic, values not logged): %s", ", ".join(names))
        return gateway.health()

    @app.get("/meeting/{mid}")
    def page(mid: str):
        if (blocked := limited("static", None)) or not MEETING_ID.match(mid):
            return blocked or not_found()
        html = dist / "meeting.html"
        if not html.is_file():
            return error(503, "PAGE_NOT_BUILT", "The meeting page is not available.")
        return FileResponse(html, media_type="text/html")

    def static_file(folder: str, name: str):
        if (blocked := limited("static", None)) or not ASSET.match(name) or name.endswith(".map"):
            return blocked or not_found()
        path = (dist / folder / name) if folder else (dist / name)
        if not path.is_file() or not path.resolve().is_relative_to(dist.resolve()):
            return not_found()
        response = FileResponse(path)
        if folder:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.get("/assets/{name}")
    def assets(name: str):
        return static_file("assets", name)

    @app.get("/images/{name}")
    def images(name: str):
        return static_file("images", name)

    @app.get("/meeting-pcm.js")
    def worklet():
        return static_file("", "meeting-pcm.js")

    @app.get("/api/v1/meetings/{mid}")
    def lookup(mid: str, authorization: str = Header(default="")):
        if blocked := limited("lookup", mid):
            return blocked
        try:
            return gateway.guest_view(mid, bearer(authorization))
        except gateway.MeetingError as exc:
            return meeting_error(exc, mid)

    @app.post("/api/v1/meetings/{mid}/consent")
    async def consent(mid: str, request: Request, authorization: str = Header(default="")):
        if blocked := limited("consent", mid):
            return blocked
        if not origin_ok(request.headers.get("origin")):
            return error(403, "BAD_ORIGIN", "Open the invitation link directly.")
        body = await request.body()
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict) or data.get("recording_consent") is not True:
            return error(422, "CONSENT_REQUIRED", "Recording consent is required to join.")
        try:
            return gateway.record_guest_consent(mid, bearer(authorization))
        except gateway.MeetingError as exc:
            return meeting_error(exc, mid)

    @app.get("/api/v1/meetings/{mid}/ice-servers")
    def ice(mid: str, authorization: str = Header(default="")):
        if blocked := limited("ice", mid):
            return blocked
        try:
            return gateway.guest_ice_servers(mid, bearer(authorization))
        except gateway.MeetingError as exc:
            return meeting_error(exc, mid)

    @app.websocket("/api/v1/meetings/{mid}/signal")
    async def signal(mid: str, ws: WebSocket):
        if not MEETING_ID.match(mid) or not host_ok(ws.headers.get("host")) or not origin_ok(ws.headers.get("origin")):
            await ws.close(code=4403, reason="Not allowed")
            return
        if not limiter.allow("signal", mid):
            await ws.close(code=4429, reason="Too many connection attempts")
            return
        await gateway.guest_signal(ws, mid)

    return app


app = create_app()

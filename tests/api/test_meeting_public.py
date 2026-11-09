"""Public meeting-only server: route allow-list, guest-only tokens, origin/host checks, limits, headers, isolation.

One TestClient drives both apps through a Host-routing shim, so they share one event loop and the signaling hub as
they do in production (services.meeting_public.serve).
"""
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.api.db import AuditEventRecord, MeetingRecord
from services.api.main import app as clinician_app, session_factory
from services.api import meeting_core as core
from services.meeting_public import gateway
from services.meeting_public.app import create_app
from services.meeting_public.limits import Limiter

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = "meet.example.ts.net"
ORIGIN = f"https://{PUBLIC}"
LOCAL = {"host": "127.0.0.1"}
PUB = {"host": PUBLIC}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("MEETING_MODE", "internet")
    monkeypatch.setenv("MEETING_PUBLIC_ORIGIN", ORIGIN + "/")
    dist = tmp_path / "dist-meeting"
    (dist / "assets").mkdir(parents=True)
    (dist / "images").mkdir()
    (dist / "meeting.html").write_text('<!doctype html><script type="module" src="/assets/app.js"></script>')
    (dist / "assets" / "app.js").write_text("console.log('patient page')")
    (dist / "assets" / "app.js.map").write_text("{}")
    (dist / "meeting-pcm.js").write_text("// worklet")
    (tmp_path / "secret.txt").write_text("outside the build")
    gateway.configure(session_factory)
    public = create_app(dist=dist, limiter=Limiter())

    async def shim(scope, receive, send):
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode()
        target = public if host.split(":")[0] == PUBLIC else clinician_app
        await target(scope, receive, send)

    with TestClient(shim, base_url="http://127.0.0.1") as client:
        yield {"client": client, "public": public, "dist": dist}


def create(c):
    sid = c.post("/api/v1/sessions", json={"session_type": "FOLLOW_UP", "consent": {}}, headers=LOCAL).json()["id"]
    response = c.post(f"/api/v1/sessions/{sid}/meetings", json={"recording_consent": True}, headers=LOCAL)
    assert response.status_code == 201, response.text
    return sid, response.json()


def bearer(room, guest=True):
    return {"Authorization": "Bearer " + room["guest_token" if guest else "host_token"]}


def test_invitation_uses_the_normalised_public_origin(env):
    _, room = create(env["client"])
    assert room["public_origin"] == ORIGIN and room["mode"] == "internet"


def test_public_app_serves_only_the_allow_list(env):
    c = env["client"]
    sid, room = create(c)
    mid = room["id"]
    clinician_paths = ["/api/v1/health", "/api/v1/patients", "/api/v1/sessions", f"/api/v1/sessions/{sid}", f"/api/v1/sessions/{sid}/transcript",
                       f"/api/v1/sessions/{sid}/note-draft", f"/api/v1/sessions/{sid}/recordings", f"/api/v1/patients/p/progress",
                       "/api/v1/progress/registry", "/api/v1/research/datasets", "/api/v1/research/progress/runs", "/docs", "/redoc",
                       "/openapi.json", "/", "/workspace", "/meeting/not-a-meeting", "/assets/../secret.txt", "/assets/app.js.map"]
    for path in clinician_paths:
        response = c.get(path, headers={**PUB, **bearer(room, guest=False)})
        assert response.status_code == 404, path
        assert response.json() == {"error": {"code": "NOT_FOUND", "message": "Not found."}}
    for path in [f"/api/v1/meetings/{mid}/end", f"/api/v1/meetings/{mid}/chunks/{uuid4()}?speaker_role=CLIENT", f"/api/v1/sessions/{sid}/meetings",
                 f"/api/v1/sessions/{sid}/recordings", "/api/v1/patients"]:
        assert c.post(path, headers={**PUB, **bearer(room, guest=False), "origin": ORIGIN}, content=b"x").status_code == 404, path
    assert c.get("/healthz", headers=PUB).json() == {"status": "ok"}
    page = c.get(f"/meeting/{mid}", headers=PUB)
    assert page.status_code == 200 and "patient page" not in page.text and page.headers["content-type"].startswith("text/html")
    assert c.get("/assets/app.js", headers=PUB).status_code == 200
    assert c.get("/meeting-pcm.js", headers=PUB).status_code == 200


def test_security_headers_on_every_response(env):
    c = env["client"]
    _, room = create(c)
    for path in ["/healthz", f"/meeting/{room['id']}", "/api/v1/patients", f"/api/v1/meetings/{room['id']}"]:
        h = c.get(path, headers=PUB).headers
        assert "script-src 'self'" in h["content-security-policy"] and "unsafe-inline" not in h["content-security-policy"]
        assert "frame-ancestors 'none'" in h["content-security-policy"]
        assert h["referrer-policy"] == "no-referrer" and h["x-content-type-options"] == "nosniff"
        assert "access-control-allow-origin" not in h and "server" not in h


def test_guest_view_consent_and_ice_accept_only_the_guest_token(env):
    c = env["client"]
    _, room = create(c)
    mid = room["id"]
    view = c.get(f"/api/v1/meetings/{mid}", headers={**PUB, **bearer(room)})
    assert view.status_code == 200 and set(view.json()) == {"id", "role", "status", "expires_ms", "audio_only"} and view.json()["role"] == "CLIENT"
    for path in [f"/api/v1/meetings/{mid}", f"/api/v1/meetings/{mid}/ice-servers"]:
        assert c.get(path, headers={**PUB, **bearer(room, guest=False)}).status_code == 403     # host capability never usable publicly
        assert c.get(path, headers={**PUB, "Authorization": "Bearer wrong"}).status_code == 403
    consent = lambda h, body=b'{"recording_consent": true}': c.post(f"/api/v1/meetings/{mid}/consent", headers=h, content=body)
    assert consent({**PUB, **bearer(room, guest=False), "origin": ORIGIN}).status_code == 403
    assert consent({**PUB, **bearer(room)}).status_code == 403                                  # no Origin
    assert consent({**PUB, **bearer(room), "origin": "https://evil.example"}).status_code == 403
    assert consent({**PUB, **bearer(room), "origin": ORIGIN}, b'{"recording_consent": false}').status_code == 422
    assert consent({**PUB, **bearer(room), "origin": ORIGIN}, b"x" * 2000).status_code == 413
    ok = consent({**PUB, **bearer(room), "origin": ORIGIN})
    assert ok.status_code == 200 and ok.json() == {"recording_consent": True}
    with session_factory() as db:
        assert db.get(MeetingRecord, mid).guest_consented is True
        assert db.query(AuditEventRecord).filter_by(event_type="MEETING_RECORDING_CONSENT").count() >= 1
    assert c.get(f"/api/v1/meetings/{mid}/ice-servers", headers={**PUB, **bearer(room)}).status_code == 200


def test_expired_and_ended_invitations(env):
    c = env["client"]
    _, room = create(c)
    mid = room["id"]
    with session_factory() as db:
        db.get(MeetingRecord, mid).expires_ms = 1
        db.commit()
    assert c.get(f"/api/v1/meetings/{mid}", headers={**PUB, **bearer(room)}).status_code == 410
    _, other = create(c)
    assert c.post(f"/api/v1/meetings/{other['id']}/end", headers={**LOCAL, **bearer(other, guest=False)}).status_code == 200
    assert c.post(f"/api/v1/meetings/{other['id']}/consent", headers={**PUB, **bearer(other), "origin": ORIGIN}, content=b'{"recording_consent": true}').status_code == 410


def test_rate_limits_and_failed_token_lockout(env):
    c = env["client"]
    _, room = create(c)
    mid = room["id"]
    env["public"].state.limiter.rules.update({"lookup": (3, 60), "failed": (2, 600)})
    for _ in range(3):
        assert c.get(f"/api/v1/meetings/{mid}", headers={**PUB, **bearer(room)}).status_code == 200
    assert c.get(f"/api/v1/meetings/{mid}", headers={**PUB, **bearer(room)}).status_code == 429
    _, other = create(c)
    for _ in range(2):
        assert c.get(f"/api/v1/meetings/{other['id']}/ice-servers", headers={**PUB, "Authorization": "Bearer guess"}).status_code == 403
    assert c.get(f"/api/v1/meetings/{other['id']}/ice-servers", headers={**PUB, **bearer(other)}).status_code == 429   # locked out


def test_public_app_refuses_an_unknown_host(env):
    # Straight to the public app (not through the shim): DNS-rebinding style Host values are refused.
    with TestClient(env["public"], base_url="https://attacker.example") as direct:
        response = direct.get("/healthz")
        assert response.status_code == 421 and response.json()["error"]["code"] == "WRONG_HOST"
    with TestClient(env["public"], base_url="http://127.0.0.1:8443") as local:
        assert local.get("/healthz").status_code == 200          # the launcher's local health check


def test_patient_and_clinician_meet_in_one_room_across_the_two_apps(env):
    c = env["client"]
    _, room = create(c)
    mid = room["id"]
    with c.websocket_connect(f"/api/v1/meetings/{mid}/signal", headers=LOCAL) as host:
        host.send_json({"token": room["host_token"], "recording_consent": True})
        assert host.receive_json()["type"] == "joined"
        with c.websocket_connect(f"/api/v1/meetings/{mid}/signal", headers={**PUB, "origin": ORIGIN}) as guest:
            guest.send_json({"token": room["guest_token"], "recording_consent": True})
            assert guest.receive_json() == {"type": "joined", "role": "CLIENT"}
            assert guest.receive_json()["type"] == "ready" and host.receive_json()["type"] == "ready"
            host.send_json({"type": "offer", "data": {"type": "offer", "sdp": "fictional"}})
            assert guest.receive_json()["type"] == "offer"


def test_public_signaling_refuses_bad_origin_and_the_host_token(env):
    c = env["client"]
    _, room = create(c)
    url = f"/api/v1/meetings/{room['id']}/signal"
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect(url, headers={**PUB, "origin": "https://evil.example"}) as ws:
            ws.receive_json()
    with c.websocket_connect(url, headers={**PUB, "origin": ORIGIN}) as ws:
        ws.send_json({"token": room["host_token"], "recording_consent": True})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4403


def test_clinician_api_refuses_public_host_and_forwarded_requests_in_internet_mode(env):
    c = env["client"]
    sid, room = create(c)
    for headers in [{"host": "nachovarga.tail5ed395.ts.net"}, {**LOCAL, "x-forwarded-for": "203.0.113.9"}, {**LOCAL, "forwarded": "for=203.0.113.9"},
                    {**LOCAL, "x-pshynox-client-ip": "192.168.1.22"}]:
        for path in ["/api/v1/patients", f"/api/v1/sessions/{sid}/transcript", f"/api/v1/meetings/{room['id']}"]:
            assert clinician_only(c, path, headers).status_code == 403, (headers, path)
    assert c.get("/api/v1/patients", headers=LOCAL).status_code == 200
    assert c.get("/api/v1/patients", headers={"host": "localhost:5173"}).status_code == 200


def clinician_only(c, path, headers):
    # Send to the clinician app even when the Host is a tunnel name (as a misconfigured tunnel would).
    with TestClient(clinician_app, base_url="http://127.0.0.1") as direct:
        return direct.get(path, headers={**headers, "Authorization": "Bearer x"})


def test_clinician_websocket_refuses_a_public_host_in_internet_mode(env):
    _, room = create(env["client"])
    with TestClient(clinician_app, base_url="http://127.0.0.1") as direct:
        with pytest.raises(WebSocketDisconnect):
            with direct.websocket_connect(f"/api/v1/meetings/{room['id']}/signal", headers={"host": "nachovarga.tail5ed395.ts.net"}) as ws:
                ws.send_json({"token": room["host_token"], "recording_consent": True})
                ws.receive_json()


def test_public_app_never_imports_clinician_routes():
    forbidden = ["services.api.main", "services.api.patient_routes", "services.api.progress_routes", "services.api.research_routes",
                 "services.api.note_generation_routes", "services.api.speech_routes", "services.api.meeting_routes", "services.progress"]
    code = ("import sys, json, services.meeting_public.app; "
            f"print(json.dumps([m for m in sys.modules if any(m == f or m.startswith(f + '.') for f in {forbidden!r})]))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []


@pytest.mark.parametrize("value,expected", [
    ("https://NachoVarga.Tail5ed395.ts.net/", "https://nachovarga.tail5ed395.ts.net"),
    ("https://host.ts.net:443/meeting/x?y=1", "https://host.ts.net"),
    ("https://host.ts.net:8443", "https://host.ts.net:8443"),
])
def test_origin_normalisation(value, expected):
    assert core.normalise_origin(value, require_https=True) == expected


@pytest.mark.parametrize("value", ["", "http://host.ts.net", "host.ts.net", "https://user:pw@host.ts.net", "ftp://host"])
def test_internet_mode_requires_an_https_origin(value):
    with pytest.raises(ValueError):
        core.normalise_origin(value, require_https=True)


def test_launchers_refuse_non_loopback_binds_and_http_origins(monkeypatch):
    from services.meeting_public.serve import check_config
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_internet
    assert run_internet.check_binds(clinician_API="127.0.0.1", clinician_UI="localhost", public_server="::1") == []
    assert len(run_internet.check_binds(clinician_API="0.0.0.0", clinician_UI="192.168.1.5", public_server="127.0.0.1")) == 2
    monkeypatch.setenv("MEETING_MODE", "internet")
    monkeypatch.setenv("MEETING_PUBLIC_ORIGIN", "http://host.ts.net")
    assert any("https" in p for p in check_config("127.0.0.1", "127.0.0.1"))
    monkeypatch.setenv("MEETING_PUBLIC_ORIGIN", ORIGIN)
    assert check_config("127.0.0.1", "127.0.0.1") == []
    assert check_config("0.0.0.0", "127.0.0.1")


def test_launcher_public_checks_report_leaks():
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_internet
    good = lambda url: (200, {"status": "ok"}) if url.endswith("/healthz") else (404, None)
    assert all(passed for _, passed, _ in run_internet.public_checks(ORIGIN, good))
    leaky = lambda url: (200, {"status": "ok"}) if url.endswith(("/healthz", "/api/v1/patients")) else (404, None)
    failed = [name for name, passed, _ in run_internet.public_checks(ORIGIN, leaky) if not passed]
    assert failed == ["clinician route /api/v1/patients not served publicly"]


def test_built_patient_page_has_no_inline_script_or_external_origin():
    html = ROOT / "apps/web/dist-meeting/meeting.html"
    if not html.exists():
        pytest.skip("patient page not built (npm run build:meeting --prefix apps/web)")
    import re
    text = html.read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text)
    assert not re.search(r"(src|href)=\"(https?:)?//", text)

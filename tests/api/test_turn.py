"""TURN credential issuance (mocked provider), TTL clamp, no caching, failure paths, secrecy, revocation, usage stats."""
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from services.api import turn
from services.api.db import MeetingRecord
from services.api.main import app, session_factory

TOKEN = "cf-api-token-SECRET-value"
KEY = "cf-key-id-123"
CF_RESPONSE = {"iceServers": [
    {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.cloudflare.com:53"]},
    {"urls": ["turn:turn.cloudflare.com:3478?transport=udp", "turn:turn.cloudflare.com:53?transport=udp",
              "turns:turn.cloudflare.com:443?transport=tcp"], "username": "user-1", "credential": "short-lived-cred"},
]}


@pytest.fixture
def cloudflare(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_TURN_KEY_ID", KEY)
    monkeypatch.setenv("CLOUDFLARE_TURN_API_TOKEN", TOKEN)
    monkeypatch.delenv("TURN_PROVIDER", raising=False)
    monkeypatch.delenv("CLOUDFLARE_TURN_TTL_SECONDS", raising=False)
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path.endswith("/generate-ice-servers"):
            if calls_fail["on"]:
                return httpx.Response(500, json={"error": TOKEN})
            return httpx.Response(201, json=CF_RESPONSE)
        if request.url.path.endswith("/revoke"):
            return httpx.Response(204)
        return httpx.Response(404)
    calls_fail = {"on": False}
    monkeypatch.setattr(turn, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler), timeout=1))
    turn._issued.clear()
    return {"calls": calls, "fail": calls_fail}


def test_issue_drops_port_53_clamps_ttl_and_never_caches(cloudflare):
    first = turn.issue("meeting-a", remaining_s=900)
    urls = [u for s in first["ice_servers"] for u in s["urls"]]
    assert not any(":53" in u for u in urls) and "turns:turn.cloudflare.com:443?transport=tcp" in urls
    assert first["relay"] and first["warning"] is None and first["ttl"] == 900          # remaining lifetime < 14400
    body = json.loads(cloudflare["calls"][0].content)
    assert body == {"ttl": 900}
    assert cloudflare["calls"][0].url.path == f"/v1/turn/keys/{KEY}/credentials/generate-ice-servers"
    turn.issue("meeting-a", remaining_s=99999)
    assert json.loads(cloudflare["calls"][1].content) == {"ttl": 14400}                 # default TTL, never above it
    assert len([c for c in cloudflare["calls"] if c.url.path.endswith("generate-ice-servers")]) == 2   # fresh each time


def test_failure_and_missing_config_fall_back_to_stun_with_a_warning(cloudflare, monkeypatch, caplog):
    cloudflare["fail"]["on"] = True
    with caplog.at_level(logging.INFO, logger="pshynox.turn"):
        out = turn.issue("meeting-b", 3600)
    assert out["ice_servers"] == turn.STUN_ONLY and not out["relay"] and "may fail" in out["warning"]
    assert TOKEN not in caplog.text and "failed" in caplog.text
    monkeypatch.delenv("CLOUDFLARE_TURN_API_TOKEN")
    out = turn.issue("meeting-b", 3600)
    assert turn.provider() is None and out["ice_servers"] == turn.STUN_ONLY and "No TURN relay" in out["warning"]


def test_secrets_never_reach_the_response_or_logs(cloudflare, caplog):
    with caplog.at_level(logging.DEBUG):
        out = turn.issue("meeting-c", 3600)
    text = json.dumps(out)
    assert TOKEN not in text and KEY not in text
    assert TOKEN not in caplog.text and "user-1" not in caplog.text and "short-lived-cred" not in caplog.text
    assert "ok latency_ms=" in caplog.text


def test_metered_provider(monkeypatch):
    monkeypatch.setenv("TURN_PROVIDER", "metered")
    monkeypatch.setenv("METERED_APP_SUBDOMAIN", "pshynox")
    monkeypatch.setenv("METERED_API_KEY", "metered-secret")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"urls": "turn:a.relay.metered.ca:80", "username": "u", "credential": "c"}])
    monkeypatch.setattr(turn, "client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    out = turn.issue("meeting-d", 3600)
    assert out["provider"] == "metered" and out["relay"] and out["ice_servers"][0]["urls"] == ["turn:a.relay.metered.ca:80"]
    assert seen[0].url.host == "pshynox.metered.live" and "metered-secret" not in json.dumps(out)


def create(c, **extra):
    sid = c.post("/api/v1/sessions", json={"session_type": "FOLLOW_UP", "consent": {}}).json()["id"]
    return sid, c.post(f"/api/v1/sessions/{sid}/meetings", json={"recording_consent": True, **extra}).json()


def test_meeting_routes_issue_per_participant_and_revoke_on_end(cloudflare):
    with TestClient(app) as c:
        _, room = create(c)
        for token in (room["host_token"], room["guest_token"]):
            r = c.get(f"/api/v1/meetings/{room['id']}/ice-servers", headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200 and r.json()["relay_configured"] and TOKEN not in r.text
            assert r.json()["ttl"] <= 4 * 3600
        assert c.get(f"/api/v1/meetings/{room['id']}/ice-servers", headers={"Authorization": "Bearer nope"}).status_code == 403
        assert c.post(f"/api/v1/meetings/{room['id']}/end", headers={"Authorization": f"Bearer {room['host_token']}"}).status_code == 200
        revoked = [r for r in cloudflare["calls"] if r.url.path.endswith("/revoke")]
        assert [r.url.path.split("/")[-2] for r in revoked] == ["user-1", "user-1"]
        assert c.get(f"/api/v1/meetings/{room['id']}/ice-servers", headers={"Authorization": f"Bearer {room['guest_token']}"}).status_code == 410


def test_audio_only_option_and_default(monkeypatch):
    from services.api import meeting_routes
    monkeypatch.setenv("APP_ENV", "development")
    assert meeting_routes.audio_only_default() is True
    monkeypatch.setenv("APP_ENV", "test")          # the test client is trusted as local only under APP_ENV=test
    assert meeting_routes.audio_only_default() is False
    monkeypatch.setattr(meeting_routes, "audio_only_default", lambda: True)
    with TestClient(app) as c:
        _, explicit = create(c, audio_only=False)
        assert explicit["audio_only"] is False
        _, default = create(c)
        assert default["audio_only"] is True
        guest = c.get(f"/api/v1/meetings/{default['id']}", headers={"Authorization": f"Bearer {default['guest_token']}"}).json()
        assert guest["audio_only"] is True


def test_connection_stats_store_no_addresses_and_monthly_usage_totals():
    with TestClient(app) as c:
        _, room = create(c)
        url = f"/api/v1/meetings/{room['id']}/stats"
        host = {"Authorization": f"Bearer {room['host_token']}"}
        stats = {"relayed": True, "local_candidate_type": "relay", "remote_candidate_type": "srflx", "bytes_sent": 3_000_000, "bytes_received": 2_000_000, "duration_s": 600}
        assert c.post(url, json={**stats, "remote_address": "203.0.113.9"}, headers=host).status_code == 422     # no IPs accepted
        assert c.post(url, json=stats, headers={"Authorization": f"Bearer {room['guest_token']}"}).status_code == 403
        assert c.post(url, json=stats, headers=host).status_code == 200
        with session_factory() as db:
            saved = json.loads(db.get(MeetingRecord, room["id"]).connection_json)
        assert "address" not in json.dumps(saved) and saved["relayed"] is True
        usage = c.get("/api/v1/meeting-usage").json()
        assert usage["relayed_meetings"] >= 1 and usage["relayed_bytes"] >= 5_000_000
        assert usage["estimated_cost_usd"] == pytest.approx(usage["relayed_bytes"] / 1e9 * 0.05, abs=1e-4)

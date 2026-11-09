"""Fictional audio: invitations, simultaneous participants, and saved timelines."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from services.api.main import app, session_factory
from services.api.db import MeetingRecord
from services.speech.worker import process_one
from tests.speech.test_pipeline import fake_transcriber, wav_bytes


def create(c):
    sid = c.post('/api/v1/sessions', json={'session_type': 'FOLLOW_UP', 'consent': {}}).json()['id']
    result = c.post(f'/api/v1/sessions/{sid}/meetings', json={'recording_consent': True})
    assert result.status_code == 201, result.text
    return sid, result.json()


def headers(room, guest=False):
    return {'Authorization': 'Bearer ' + room['guest_token' if guest else 'host_token']}


def consent_pair(c, room):
    with c.websocket_connect(f"/api/v1/meetings/{room['id']}/signal") as host:
        host.send_json({'token': room['host_token'], 'recording_consent': True})
        assert host.receive_json()['type'] == 'joined'
        with c.websocket_connect(f"/api/v1/meetings/{room['id']}/signal") as guest:
            guest.send_json({'token': room['guest_token'], 'recording_consent': True})
            assert guest.receive_json()['type'] == 'joined'
            assert guest.receive_json()['type'] == 'ready'
            assert host.receive_json()['type'] == 'ready'


def chunk(c, room, cid=None, role='CLIENT', offset=0, body=None, guest=False):
    return c.post(f"/api/v1/meetings/{room['id']}/chunks/{cid or uuid4()}?speaker_role={role}&offset_ms={offset}",
        content=wav_bytes() if body is None else body,
        headers={**headers(room, guest), 'Content-Type': 'audio/wav'})


def test_invitation_access_and_lan_patient_isolation():
    with TestClient(app) as c:
        sid, room = create(c)
        assert c.get(f"/api/v1/meetings/{room['id']}").status_code == 403
        guest = c.get(f"/api/v1/meetings/{room['id']}", headers=headers(room, True)).json()
        assert guest['role'] == 'CLIENT' and 'session_id' not in guest and 'jobs' not in guest
        assert chunk(c, room).status_code == 403
        assert c.post(f"/api/v1/meetings/{room['id']}/end", headers=headers(room, True)).status_code == 403
        remote = {'X-Pshynox-Client-IP': '192.168.1.22'}
        for path in ['/api/v1/patients', '/api/v1/sessions', f'/api/v1/sessions/{sid}/transcript', '/docs', '/openapi.json']:
            assert c.get(path, headers=remote).status_code == 403
        assert c.get(f"/api/v1/meetings/{room['id']}", headers={**remote, **headers(room, True)}).status_code == 200
        assert c.post(f'/api/v1/sessions/{sid}/meetings', headers=remote, json={'recording_consent': True}).status_code == 403
        assert c.get('/api/v1/patients', headers={'X-Pshynox-Client-IP': '::ffff:127.0.0.1'}).status_code == 200
        with TestClient(app, client=('192.168.1.22', 50000)) as lan:
            assert lan.get('/api/v1/patients', headers={'X-Pshynox-Client-IP': '127.0.0.1'}).status_code == 403


def test_simultaneous_join_recording_status_chat_and_duplicate_participant():
    with TestClient(app) as c:
        _, room = create(c)
        url = f"/api/v1/meetings/{room['id']}/signal"
        with c.websocket_connect(url) as host:
            host.send_json({'token': room['host_token'], 'recording_consent': True})
            assert host.receive_json()['role'] == 'CLINICIAN'
            with c.websocket_connect(url) as duplicate:
                duplicate.send_json({'token': room['host_token'], 'recording_consent': True})
                with pytest.raises(WebSocketDisconnect) as exc: duplicate.receive_json()
                assert exc.value.code == 4409
            with c.websocket_connect(url) as guest:
                guest.send_json({'token': room['guest_token'], 'recording_consent': True})
                assert guest.receive_json()['role'] == 'CLIENT'
                assert guest.receive_json()['type'] == host.receive_json()['type'] == 'ready'
                # Chat, media and recording control moved to the encrypted data channel: signaling drops them
                # (without closing the socket) and relays only offer/answer/ice/leave.
                for value in [False, True]:
                    host.send_json({'type': 'recording', 'data': value})
                host.send_json({'type': 'media', 'data': {'camera': True, 'mic': True}})
                host.send_json({'type': 'offer', 'data': {'type': 'offer', 'sdp': 'fictional-sdp'}})
                assert guest.receive_json()['type'] == 'offer'          # the dropped messages never arrived
                guest.send_json({'type': 'chat', 'data': 'Fictional hello', 'role': 'CLINICIAN'})
                guest.send_json({'type': 'ice', 'data': {'candidate': 'fictional'}})
                message = host.receive_json()
                assert message['type'] == 'ice' and message['role'] == 'CLIENT' and 'Fictional hello' not in str(message)
                guest.send_json({'type': 'leave'})
                assert host.receive_json()['type'] == 'peer-left'


def test_missing_consent_and_expiry():
    with TestClient(app) as c:
        _, room = create(c)
        with c.websocket_connect(f"/api/v1/meetings/{room['id']}/signal") as ws:
            ws.send_json({'token': room['guest_token'], 'recording_consent': False})
            with pytest.raises(WebSocketDisconnect): ws.receive_json()
        with session_factory() as db:
            db.get(MeetingRecord, room['id']).expires_ms = 1
            db.commit()
        assert c.get(f"/api/v1/meetings/{room['id']}", headers=headers(room)).status_code == 410


def test_chunks_idempotent_attributed_sorted_and_review_gated():
    with TestClient(app) as c:
        sid, room = create(c)
        consent_pair(c, room)
        cid = uuid4()
        late = chunk(c, room, cid, offset=60000)
        assert late.status_code == 201, late.text
        assert chunk(c, room, cid, offset=60000).json() == late.json()
        assert chunk(c, room, cid, role='CLINICIAN', offset=60000).status_code == 409
        assert chunk(c, room, role='CLINICIAN', offset=2000).status_code == 201
        assert chunk(c, room, guest=True).status_code == 403
        while process_one(session_factory, fake_transcriber): pass
        segments = c.get(f'/api/v1/sessions/{sid}/transcript').json()
        assert [(s['start_ms'], s['end_ms'], s['speaker_role']) for s in segments] == [(2000, 2900, 'CLINICIAN'), (60000, 60900, 'CLIENT')]
        recordings = c.get(f'/api/v1/sessions/{sid}/recordings').json()
        assert {r['offset_ms'] for r in recordings} == {2000, 60000}
        draft = c.post(f'/api/v1/sessions/{sid}/note-draft/populate').json()
        assert draft['fields'][0]['evidence_ids'] == [segments[1]['id']]
        assert c.post(f'/api/v1/sessions/{sid}/risk-review/complete', json={'confirmation': True, 'reviewer_name': 'Test'}).status_code == 409
        assert c.post(f"/api/v1/meetings/{room['id']}/end", headers=headers(room)).status_code == 200
        assert c.post(f'/api/v1/sessions/{sid}/risk-review/complete', json={'confirmation': True, 'reviewer_name': 'Test'}).status_code == 200
        assert len(c.get(f'/api/v1/sessions/{sid}/transcription-jobs').json()) == 2


def test_bad_chunks_and_withdrawn_consent():
    with TestClient(app) as c:
        sid, room = create(c)
        consent_pair(c, room)
        for body in [b'', b'not wav', wav_bytes(channels=2), wav_bytes()[:-100]]:
            assert chunk(c, room, body=body).status_code == 422
        assert chunk(c, room, offset=-1).status_code == 422
        assert chunk(c, room, role='UNKNOWN').status_code == 422
        c.patch(f'/api/v1/sessions/{sid}/recording-consent', json={'recording_consent': False})
        assert chunk(c, room).status_code == 403
        assert c.get(f'/api/v1/sessions/{sid}/transcription-jobs').json() == []


def test_end_revokes_join():
    with TestClient(app) as c:
        _, room = create(c)
        assert c.post(f"/api/v1/meetings/{room['id']}/end", headers=headers(room)).status_code == 200
        with c.websocket_connect(f"/api/v1/meetings/{room['id']}/signal") as ws:
            ws.send_json({'token': room['guest_token'], 'recording_consent': True})
            with pytest.raises(WebSocketDisconnect): ws.receive_json()


def test_verification_confirmation_is_audited_host_only():
    from services.api.db import AuditEventRecord
    with TestClient(app) as c:
        _, room = create(c)
        url = f"/api/v1/meetings/{room['id']}/verification"
        assert c.post(url, json={'matched': True}, headers=headers(room, guest=True)).status_code == 403
        assert c.post(url, json={'matched': True, 'code': '123456'}, headers=headers(room)).status_code == 422   # the code is never sent
        assert c.post(url, json={'matched': False}, headers=headers(room)).status_code == 200
        assert c.post(url, json={'matched': True}, headers=headers(room)).status_code == 200
        with session_factory() as db:
            assert db.get(MeetingRecord, room['id']).verification_confirmed_at is not None
            events = [e.event_type for e in db.query(AuditEventRecord).filter(AuditEventRecord.event_type.like('MEETING_VERIFICATION%'))]
        assert 'MEETING_VERIFICATION_MISMATCH' in events and 'MEETING_VERIFICATION_CONFIRMED' in events


def test_chunk_source_and_clock_fields_are_validated_stored_and_listed():
    from services.api.db import MeetingChunkRecord
    with TestClient(app) as c:
        sid, room = create(c)
        consent_pair(c, room)
        def up(cid, role, query):
            return c.post(f"/api/v1/meetings/{room['id']}/chunks/{cid}?speaker_role={role}&offset_ms=60000{query}",
                          content=wav_bytes(), headers={**headers(room), 'Content-Type': 'audio/wav'})
        cid = uuid4()
        assert up(cid, 'CLIENT', '&source=patient_device&offset_uncertainty_ms=12&rtt_ms=24').status_code == 201
        assert up(cid, 'CLIENT', '&source=patient_device&offset_uncertainty_ms=12&rtt_ms=24').status_code == 201      # idempotent retry
        assert up(cid, 'CLIENT', '&source=network_received').status_code == 409                                       # same id, other source
        assert up(uuid4(), 'CLINICIAN', '&source=patient_device').status_code == 422                                  # source must match speaker
        assert up(uuid4(), 'CLIENT', '&source=clinician_local').status_code == 422
        assert up(uuid4(), 'CLIENT', '&source=somewhere').status_code == 422
        assert up(uuid4(), 'CLIENT', '&source=patient_device&rtt_ms=999999').status_code == 422
        legacy = chunk(c, room, role='CLIENT', offset=0)                                                              # LAN clients send no source
        assert legacy.status_code == 201
        with session_factory() as db:
            saved = db.get(MeetingChunkRecord, str(cid))
            assert (saved.source, saved.offset_uncertainty_ms, saved.rtt_ms) == ('patient_device', 12, 24)
        sources = {r['offset_ms']: r['source'] for r in c.get(f'/api/v1/sessions/{sid}/recordings').json()}
        assert sources == {60000: 'patient_device', 0: 'network_received'}
        while process_one(session_factory, fake_transcriber):     # leave no queued jobs for later tests
            pass


def test_patient_device_chunks_require_patient_consent_first():
    with TestClient(app) as c:
        _, room = create(c)
        response = c.post(f"/api/v1/meetings/{room['id']}/chunks/{uuid4()}?speaker_role=CLIENT&offset_ms=0&source=patient_device",
                          content=wav_bytes(), headers={**headers(room), 'Content-Type': 'audio/wav'})
        assert response.status_code == 403 and (response.json().get('error') or response.json().get('detail'))['code'] == 'CONSENT_REQUIRED'

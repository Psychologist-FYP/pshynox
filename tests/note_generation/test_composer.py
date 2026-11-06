import json
from uuid import uuid4
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from services.api.main import app, session_factory
from services.api.db import NoteDraftRecord, AuditEventRecord
from services.note_generation.composer import apply_to_note, note_token
from services.api.note_generation_routes import Start, snapshot
from services.note_generation.workflow import SECTIONS
from tests.api.helpers import seed_segment


def setup_job(client):
    session = client.post('/api/v1/sessions', json={'pseudonym_id':uuid4().hex,'session_type':'FOLLOW_UP','consent':{}}).json()
    sid = session['id']
    seed_segment(sid)
    client.get(f'/api/v1/sessions/{sid}/note-draft')
    snap,_ = snapshot(Start(kind='clinical', session_id=sid))
    with session_factory() as db:
        note = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id==sid))
        token = note_token(note)
    fields = [{'section':s,'items':[],'limitations':[]} for s in SECTIONS]
    fields[0]['items']=[{'text':'Client reported difficulty sleeping.','source_ids':[snap['segments'][0]['id']],'status':'supported'}]
    fields[-1]['items']=[{'text':'Sleep diary was discussed.','source_ids':[snap['segments'][0]['id']],'status':'supported'}]
    return {'id':uuid4().hex,'request':{'session_id':sid},'snapshot':snap,'target_note_token':token,'draft':{'edits':fields}}


def test_apply_maps_once_and_keeps_audit_and_pending_status():
    with TestClient(app) as client:
        job = setup_job(client)
        with session_factory() as db:
            result = apply_to_note(db,job)
            fields = {f.section:f for f in result.fields}
            assert fields['subjective'].text == 'Client reported difficulty sleeping.'
            assert fields['plan'].text == 'Sleep diary was discussed.'
            assert fields['objective'].text == fields['assessment'].text == ''
            assert all(f.provenance=='AI_GENERATED' and f.review_status=='PENDING' for f in fields.values())
            assert result.workflow_status == 'DRAFT' and result.risk_review_status == 'PENDING'
            event = db.scalar(select(AuditEventRecord).where(AuditEventRecord.session_id==result.session_id, AuditEventRecord.event_type=='AI_DRAFT_APPLIED'))
            assert json.loads(event.metadata_json)['generation_id'] == job['id']
            assert 'previous_fields' in json.loads(event.metadata_json)
            with pytest.raises(HTTPException,match='note changed'):
                apply_to_note(db,job)


def test_concurrent_manual_edit_preserved():
    with TestClient(app) as client:
        job=setup_job(client);sid=job['request']['session_id']
        note=client.get(f'/api/v1/sessions/{sid}/note-draft').json()
        note['fields'][0]['text']='My unsuperseded manual note'
        client.put(f'/api/v1/sessions/{sid}/note-draft',json={'fields':note['fields']})
        with session_factory() as db:
            with pytest.raises(HTTPException,match='note changed'):
                apply_to_note(db,job)
        assert client.get(f'/api/v1/sessions/{sid}/note-draft').json()['fields'][0]['text']=='My unsuperseded manual note'


def test_generation_applies_without_browser_and_apply_is_read_only_afterward(monkeypatch):
    import time
    import services.api.note_generation_routes as routes
    class FakeRuntime:
        process=None
        metrics={}
        def __init__(self,folder,cancel): pass
        def check(self): pass
        def close(self): pass
    monkeypatch.setattr(routes,'Runtime',FakeRuntime)
    with TestClient(app) as client:
        template=setup_job(client);sid=template['request']['session_id']
        def fake_run(*args, **kwargs):
            return {'edits':template['draft']['edits'],'original':template['draft']['edits'],'status':'draft'}
        monkeypatch.setattr(routes,'run',fake_run)
        response=client.post('/api/v1/note-generation/jobs',json={'kind':'clinical','session_id':sid,'transcript_reviewed':True})
        assert response.status_code==202,response.text
        jid=response.json()['id']
        for _ in range(100):
            detail=client.get(f'/api/v1/note-generation/jobs/{jid}').json()
            if detail['status']!='running':break
            time.sleep(.02)
        assert detail['status']=='completed',detail
        assert detail['applied_at']
        note=client.get(f'/api/v1/sessions/{sid}/note-draft').json()
        assert note['fields'][0]['text']=='Client reported difficulty sleeping.'
        note['fields'][0]['text']='Later human correction'
        client.put(f'/api/v1/sessions/{sid}/note-draft',json={'fields':note['fields']})
        again=client.post(f'/api/v1/note-generation/jobs/{jid}/apply')
        assert again.status_code==200
        assert again.json()['fields'][0]['text']=='Later human correction'
        refused=client.post('/api/v1/note-generation/jobs',json={'kind':'clinical','session_id':sid,'transcript_reviewed':True})
        assert refused.status_code==409

import json
from fastapi.testclient import TestClient
from services.api.main import app
from services.api.note_generation_routes import Start, snapshot
from services.note_generation.storage import root, write
from services.retrieval.engine import digest
from services.note_generation.workflow import SECTIONS
import pytest


def test_research_heldout_blocked(tmp_path,monkeypatch):
    monkeypatch.setenv('RESEARCH_DATA_DIR',str(tmp_path))
    write(tmp_path/'datasets/annomi.json',{'conversations':[{'conversation_id':'held','split':'test','segments':[]} ]})
    with pytest.raises(Exception,match='Only development'):
        snapshot(Start(kind='research',session_id='held'))


def test_review_versions_stale_and_original_preserved(tmp_path,monkeypatch):
    monkeypatch.setenv('RESEARCH_DATA_DIR',str(tmp_path))
    rows=[{'id':'a','session_id':'dev','text':'Reported sleep problem','speaker_role':'CLIENT','start_ms':0,'end_ms':1,'revision':1}]
    path=tmp_path/'datasets/annomi.json'
    write(path,{'conversations':[{'conversation_id':'dev','split':'dev','segments':rows}]})
    payload=Start(kind='research',session_id='dev',transcript_reviewed=True)
    snap,_=snapshot(payload);jid='a'*32
    fields=[{'section':s,'items':[],'limitations':['Not established']} for s in SECTIONS]
    draft={'original':fields,'edits':fields,'status':'draft','review_history':[],'packet':{'evidence':{'a':{}}}}
    write(root()/jid/'job.json',{'id':jid,'request':payload.model_dump(),'snapshot':snap,'stage':'validation','status':'completed'})
    write(root()/jid/'draft.json',draft)
    with TestClient(app) as client:
        edited=json.loads(json.dumps(fields));edited[0]['items']=[{'text':'Client reported sleep problem.','source_ids':['a'],'status':'supported'}]
        body={'edits':edited,'actor':'Fictional tester','expected_hash':digest(fields)}
        response=client.put(f'/api/v1/note-generation/jobs/{jid}/draft',json=body)
        assert response.status_code==200,response.text
        saved=response.json()['draft'];assert saved['original']==fields and saved['status']=='draft'
        assert client.put(f'/api/v1/note-generation/jobs/{jid}/draft',json=body).status_code==409
        body.update(expected_hash=digest(edited),approve=True)
        assert client.put(f'/api/v1/note-generation/jobs/{jid}/draft',json=body).status_code==422
        rows[0]['revision']=2;write(path,{'conversations':[{'conversation_id':'dev','split':'dev','segments':rows}]})
        assert client.get(f'/api/v1/note-generation/jobs/{jid}').json()['stale']
        assert client.put(f'/api/v1/note-generation/jobs/{jid}/draft',json=body).status_code==409


def test_job_single_concurrency_cancel_and_review_gate(tmp_path,monkeypatch):
    import time
    import services.api.note_generation_routes as routes
    monkeypatch.setenv('RESEARCH_DATA_DIR',str(tmp_path))
    write(tmp_path/'datasets/annomi.json',{'conversations':[{'conversation_id':'dev','split':'dev','segments':[{'id':'t','session_id':'dev','start_ms':0,'end_ms':1,'speaker_role':'CLIENT','text':'Fictional','revision':1}]}]})
    class FakeRuntime:
        process=None
        metrics={}
        def __init__(self,folder,cancel):self.cancel=cancel
        def close(self):pass
    def fake_run(snap,runtime,folder,**kwargs):
        kwargs['progress']('overview')
        if not runtime.cancel.wait(3):raise RuntimeError('Timed out test job')
        raise RuntimeError('Cancelled')
    monkeypatch.setattr(routes,'Runtime',FakeRuntime)
    monkeypatch.setattr(routes,'run',fake_run)
    body={'kind':'research','session_id':'dev'}
    with TestClient(app) as client:
        assert client.post('/api/v1/note-generation/jobs',json=body).status_code==422
        body['transcript_reviewed']=True
        response=client.post('/api/v1/note-generation/jobs',json=body)
        assert response.status_code==202,response.text
        jid=response.json()['id']
        assert client.post('/api/v1/note-generation/jobs',json=body).status_code==409
        assert client.post(f'/api/v1/note-generation/jobs/{jid}/cancel').status_code==200
        for _ in range(100):
            j=client.get(f'/api/v1/note-generation/jobs/{jid}').json()
            if j['status']=='cancelled':break
            time.sleep(.02)
        assert j['status']=='cancelled' and j['draft'] is None

"""Synthetic structural regressions, not clinician-rated note-quality tests."""
import json
from copy import deepcopy
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from services.api.main import app, session_factory
from services.api.db import NoteDraftRecord
from services.api.schemas import GeneratedNoteItem
from services.note_generation.composer import apply_to_note, note_token
from services.note_generation.workflow import draft_packet, validate_items
from services.note_generation.runtime import GenerationError
from tests.note_generation.test_composer import setup_job


def item(kind='ai_proposed_formulation', refs=None):
    return GeneratedNoteItem(id=uuid4().hex,text='Reported travel demands may limit attendance.',type=kind,
        source_ids=refs or ['c'],status='supported',attribution='AI interpretation' if kind=='ai_proposed_formulation' else 'client',
        timeframe='current',uncertainty='tentative',basis='Client describes travel limiting attendance.',
        missing_information=['Feasible schedule is not established.']).model_dump()


def proposal_job(client):
    job=setup_job(client)
    source=job['snapshot']['segments'][0]['id']
    proposal=item(refs=[source])
    job['draft']={'version':'local-notes-v3','edits':[{'section':'Assessment','items':[proposal]}]}
    with session_factory() as db:note=apply_to_note(db,job)
    return job,proposal,note.model_dump(mode='json')


@pytest.mark.parametrize('action',['accept','edit','reject'])
def test_explicit_review_preserves_original_and_save_is_not_acceptance(action):
    with TestClient(app) as client:
        job,proposal,note=proposal_job(client);sid=note['session_id']
        field=next(f for f in note['fields'] if f['section']=='assessment')
        assert field['text']=='' and field['generated_items'][0]['review_status']=='pending'
        saved=client.put(f'/api/v1/sessions/{sid}/note-draft',json={'fields':note['fields']})
        assert saved.status_code==200,saved.text
        note=saved.json()
        assert client.post(f'/api/v1/sessions/{sid}/approve',json={'actor_name':'Test reviewer'}).status_code==409
        updated=client.post(f'/api/v1/sessions/{sid}/note-draft/formulations/{proposal["id"]}',json={'actor':'Test reviewer','action':action,'text':'Reviewed: travel may affect attendance.','expected_updated_at':note['updated_at']})
        assert updated.status_code==200,updated.text
        field=next(f for f in updated.json()['fields'] if f['section']=='assessment');reviewed=field['generated_items'][0]
        assert reviewed['text']==proposal['text']
        assert reviewed['source_ids']==proposal['source_ids']
        assert reviewed['review_history'][0]['actor']=='Test reviewer'
        assert reviewed['review_status']=={'accept':'accepted','edit':'edited','reject':'rejected'}[action]
        assert field['text']==({'accept':proposal['text'],'edit':'Reviewed: travel may affect attendance.','reject':''}[action])
        assert job['draft']['edits'][0]['items'][0]['review_status']=='pending'


def test_generic_save_cannot_forge_or_erase_formulation_review():
    with TestClient(app) as client:
        _,proposal,note=proposal_job(client);sid=note['session_id']
        forged=deepcopy(note['fields']);field=next(f for f in forged if f['section']=='assessment')
        field['generated_items'][0]['review_status']='accepted'
        assert client.put(f'/api/v1/sessions/{sid}/note-draft',json={'fields':forged}).status_code==409
        field['generated_items']=[]
        assert client.put(f'/api/v1/sessions/{sid}/note-draft',json={'fields':forged}).status_code==409
        # Editing a referenced source invalidates the review action.
        client.patch(f'/api/v1/sessions/{sid}/transcript/{proposal["source_ids"][0]}',json={'text':'Changed source.'})
        response=client.post(f'/api/v1/sessions/{sid}/note-draft/formulations/{proposal["id"]}',json={'actor':'Test','action':'accept','expected_updated_at':note['updated_at']})
        assert response.status_code==409


def test_regeneration_preserves_manual_text_and_adopted_formulation():
    with TestClient(app) as client:
        job,proposal,note=proposal_job(client);sid=note['session_id']
        note=client.post(f'/api/v1/sessions/{sid}/note-draft/formulations/{proposal["id"]}',json={'actor':'Test','action':'accept','expected_updated_at':note['updated_at']}).json()
        field=next(f for f in note['fields'] if f['section']=='subjective');field['text']='My own text';field['provenance']='MANUAL'
        client.put(f'/api/v1/sessions/{sid}/note-draft',json={'fields':note['fields']})
        with session_factory() as db:
            record=db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id==sid));job['target_note_token']=note_token(record)
            job['draft']['edits'].append({'section':'Subjective','items':[{'text':'Replacement','source_ids':proposal['source_ids']}]})
            result=apply_to_note(db,job)
        assert next(f.text for f in result.fields if f.section=='subjective')=='My own text'
        assert next(f.text for f in result.fields if f.section=='assessment')==proposal['text']


def test_structured_routing_agreement_and_missing_progress(tmp_path):
    report=item('client_report',['c']);report['text']='Client reports a gym membership.'
    function=item('client_report',['c']);function['text']='Travel limits participation.'
    suggestion=item('proposed_plan',['t']);suggestion['text']='Suggested trying a different time.'
    invalid_agreement=item('agreed_plan',['t']);invalid_agreement['text']='Client agreed to attend.'
    class Writer:
        context=4096
        model={'sha256':'mock'}
        metrics={}
        folder=tmp_path/'job'
        def check(self):pass
        def fits(self,*args):return True
        def generate(self,system,data,output=700):
            section=system.split('Requested section: ')[1].split('\n')[0]
            values={'Subjective':[report],'Objective':[report],'Functioning':[function],'Plan':[suggestion,invalid_agreement]}
            return {'items':deepcopy(values.get(section,[])),'limitations':[]}
    packet={'evidence':{'c':{'source':{'id':'c','speaker_role':'CLIENT','text':'I have a gym membership but travel makes attendance difficult.'}},'t':{'source':{'id':'t','speaker_role':'CLINICIAN','text':'Would another time help?'}}}}
    fields,_=draft_packet(packet,Writer(),strategy='sections');sections={f['section']:f for f in fields}
    assert not sections['Objective']['items']
    assert sections['Objective']['fallback']=='No clinician observations documented in the supplied material.'
    assert sections['Functioning']['items'] and not sections['Progress']['items']
    assert sections['Plan']['no_agreed_plan']
    assert all(i['type']!='agreed_plan' for i in sections['Plan']['items'])
    assert sections['Assessment']['fallback']=='Assessment is limited by the available information.'


def test_formulation_requires_basis_and_original_sources():
    proposal=item();proposal['basis']=''
    with pytest.raises(GenerationError,match='basis'):validate_items({'items':[proposal],'limitations':[]},{'c'},typed=True)
    proposal['basis']='A rationale'
    with pytest.raises(GenerationError,match='source IDs'):validate_items({'items':[proposal],'limitations':[]},{'other'},typed=True)


def test_new_proposal_is_reviewable_beside_preserved_human_assessment():
    with TestClient(app) as client:
        job=setup_job(client);sid=job['request']['session_id']
        note=client.get(f'/api/v1/sessions/{sid}/note-draft').json()
        field=next(f for f in note['fields'] if f['section']=='assessment');field['text']='Existing human formulation.'
        client.put(f'/api/v1/sessions/{sid}/note-draft',json={'fields':note['fields']})
        proposed=item(refs=[job['snapshot']['segments'][0]['id']]);job['draft']={'version':'local-notes-v3','edits':[{'section':'Assessment','items':[proposed]}]}
        with session_factory() as db:
            record=db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id==sid));job['target_note_token']=note_token(record)
            result=apply_to_note(db,job)
        field=next(f for f in result.fields if f.section=='assessment')
        assert field.text=='Existing human formulation.'
        assert len(field.generated_items)==1 and field.generated_items[0].review_status=='pending'

import json
from services.retrieval import bulk_review
from services.retrieval.workspace import ResearchStore
from tests.retrieval.test_workspace import create_research_fixture,add_query,grade_all


def test_bulk_acceptance_preserves_human_labels_and_does_not_invent_no_answer_confirmation(tmp_path,monkeypatch):
    store=create_research_fixture(tmp_path)
    grade_all(store,add_query(store),[0,2])
    old=store.state('synthetic')['queries']
    (tmp_path/'datasets/synthetic.json').rename(tmp_path/'datasets/annomi.json')
    (tmp_path/'annotations/synthetic.json').rename(tmp_path/'annotations/annomi.json')
    def source(store,name,cid):
        return {'transcript_hash':'test','queries':[{'query_id':'ai-'+cid,'conversation_id':cid,
            'text':'fictional absent question','intent':'fictional test scope','draft':True,'no_answer_confirmed':False,
            'query_revision':'test','annotation_origin':'ai','suggestions':[{'source_segment_id':f'{cid}-{i}',
                'suggested_grade':0,'rationale':'Fictional test-only absent evidence'} for i in range(2)]}]}
    monkeypatch.setattr(bulk_review,'suggestions',source)
    result=bulk_review.accept(store,'Anas')
    assert result['new_bulk_accepted_grades']==4
    state=store.state('annomi')
    for original in old:assert next(q for q in state['queries'] if q['query_id']==original['query_id'])==original
    for q in state['queries']:
        if q['query_id'].startswith('ai-'):
            assert not q['draft'] and not q['no_answer_confirmed']
            for j in q['judgments'].values():
                assert j['reviewer']=='Anas' and j['review_action']=='bulk-accepted' and j['annotation_origin']=='ai'
    again=bulk_review.accept(store,'Anas')
    assert again['new_bulk_accepted_grades']==0
    assert len(store.state('annomi')['queries'])==3

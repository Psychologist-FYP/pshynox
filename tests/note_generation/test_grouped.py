import copy
import json
import pytest
from services.note_generation.grouped import draft_grouped,validate,schema,SECTIONS
from services.note_generation.runtime import GenerationError,OutputLimitError
from services.retrieval.engine import digest


class Writer:
    context=4096
    safety_margin=64
    model={'sha256':'mock-group'}
    def __init__(self,handler=None,max_sections=7):
        self.metrics={'calls':[]};self.handler=handler;self.max_sections=max_sections;self.cancel=False
    def check(self):
        if self.cancel:raise GenerationError('Cancelled')
    def fits(self,system,data,output):
        return len(data['requested_sections'])<=self.max_sections and len(system)//4+len(json.dumps(data))//4+output+64<=self.context
    def generate(self,system,data,output):
        self.metrics['calls'].append({'system':system,'data':copy.deepcopy(data),'output':output})
        if self.handler:return self.handler(self,system,data,output)
        return empty(data)


def empty(data):return {'sections':{s:{'status':'not_documented','items':[]} for s in data['requested_sections']}}
def source(i,text,role='CLIENT'):
    return {'id':f'long-original-session-utterance-{i}','speaker_role':role,'text':text,'start_ms':i*100,'end_ms':i*100+90,'revision':1}
def packet(rows):return {'revision':digest(rows),'overview':[{'text':'DO NOT SEND duplicated hints'}], 'evidence':{r['id']:{'source':r} for r in rows}}
def item(text,refs,kind='report',who='adult reports about child',**kwargs):return dict(text=text,kind=kind,refs=refs,who=who,**kwargs)


def test_one_call_compact_aliases_and_cached_resume(tmp_path):
    rows=[source(0,'Has your child been distressed?','CLINICIAN'),source(1,'Yeah.')]
    def result(rt,system,data,output):
        value=empty(data);value['sections']['Subjective']={'status':'documented','items':[item('Adult confirms the child is distressed.',['T1'])]};return value
    rt=Writer(result);fields,_=draft_grouped(packet(rows),rt,tmp_path/'job')
    assert len(rt.metrics['calls'])==1
    data=rt.metrics['calls'][0]['data'];assert 'extracted_hints' not in data and 'already_documented' not in data
    assert [r['id'] for r in data['sources']]==['T0','T1']
    assert rows[0]['id'] not in json.dumps(data)
    assert fields[0]['items'][0]['source_ids']==[r['id'] for r in rows]
    assert fields[0]['items'][0]['attribution']=='adult reports about child'
    assert fields[0]['items'][0]['basis']==''
    resumed,_=draft_grouped(packet(rows),rt,tmp_path/'resume')
    assert resumed==fields and len(rt.metrics['calls'])==1
    coverage=json.loads((tmp_path/'resume/draft-aliases.json').read_text())['coverage']
    assert all(len(c)==2 for c in coverage.values())


@pytest.mark.parametrize('max_sections,expected',[(3,3),(1,7)])
def test_related_groups_then_single_section_fallback(tmp_path,max_sections,expected):
    rt=Writer(max_sections=max_sections)
    fields,_=draft_grouped(packet([source(0,'Reported sleep difficulty.')]),rt,tmp_path/'job')
    assert len(rt.metrics['calls'])==expected and [f['section'] for f in fields]==SECTIONS
    ledger=json.loads((tmp_path/'job/drafting-recovery.json').read_text())
    assert all(not c['unresolved'] for c in ledger['section_coverage'].values())


def test_token_limited_valid_json_retried_then_grouped(tmp_path):
    def result(rt,system,data,output):
        if len(rt.metrics['calls'])<=2:raise OutputLimitError('limit even though JSON looked complete')
        return empty(data)
    rt=Writer(result);draft_grouped(packet([source(0,'Difficult sleep.')]),rt,tmp_path/'job')
    calls=rt.metrics['calls'];assert calls[0]['output']==1600 and calls[1]['output']>1600
    assert len(calls)==5
    assert rt.metrics['recovery']['grouped-draft-v6']['splits']==1


def test_empty_selection_is_insufficient_not_absent(tmp_path):
    rt=Writer();fields,_=draft_grouped(packet([]),rt,tmp_path/'job')
    assert not rt.metrics['calls']
    assert all(f['evidence_status']=='insufficient_evidence' for f in fields)


def test_validated_empty_status_and_schema_omits_boilerplate():
    data={'requested_sections':['Objective'],'sources':[{'id':'T0','role':'CLINICIAN','text':'Consider reducing smoking.'}],'context':[]}
    value={'sections':{'Objective':{'status':'documented','items':[]}}}
    with pytest.raises(GenerationError):validate(value,data)
    props=schema(data)['properties']['sections']['properties']['Objective']['properties']
    assert set(props)=={'status','items'}
    assert 'basis' not in props['items']['items']['properties']
    value['sections']['Objective']={'status':'documented','items':[item('Advice was offered.',['T0'],kind='intervention')]}
    with pytest.raises(GenerationError,match='section kind'):validate(value,data)


def test_formulation_remains_pending_review_and_requires_basis(tmp_path):
    def result(rt,system,data,output):
        value=empty(data);value['sections']['Assessment']={'status':'documented','items':[
            item('Reported barriers may limit participation.',['T0'],kind='formulation',who='AI interpretation',
                 basis='Adult reports difficulty finding time.',missing=['Feasible schedule'])]};return value
    rt=Writer(result);fields,_=draft_grouped(packet([source(0,'I cannot find time.')]),rt,tmp_path/'job')
    proposal=fields[2]['items'][0]
    assert proposal['type']=='ai_proposed_formulation' and proposal['review_status']=='pending' and proposal['basis']


def test_advice_and_recommendation_distinct_from_agreement(tmp_path):
    def result(rt,system,data,output):
        value=empty(data)
        value['sections']['Interventions']={'status':'documented','items':[item('Discussed health risks.',['T0'],'intervention','clinician discussing adult and child risks')]}
        value['sections']['Plan']={'status':'documented','items':[item('Clinician recommended quitting.',['T0'],'recommendation','clinician proposal for adult')]}
        return value
    rt=Writer(result);fields,_=draft_grouped(packet([source(0,'I recommend quitting.','CLINICIAN')]),rt,tmp_path/'job')
    assert fields[5]['items'][0]['type']=='intervention_delivered_or_discussed'
    assert fields[6]['items'][0]['type']=='proposed_plan' and fields[6]['no_agreed_plan']
    assert not fields[1]['items']


def test_subjective_functioning_exact_duplicate_removed(tmp_path):
    def result(rt,system,data,output):
        value=empty(data)
        for section in ['Subjective','Functioning']:
            value['sections'][section]={'status':'documented','items':[item('Adult reports stress about child symptoms.',['T0'])]}
        return value
    fields,_=draft_grouped(packet([source(0,'It is stressful.')]),Writer(result),tmp_path/'job')
    assert fields[0]['items'] and not fields[3]['items']


def test_failed_work_budget_keeps_completed_groups(tmp_path,monkeypatch):
    monkeypatch.setenv('NOTE_MAX_GENERATION_CALLS','1')
    rt=Writer(max_sections=3)
    with pytest.raises(GenerationError,match='request budget'):draft_grouped(packet([source(0,'Sleep difficulty.')]),rt,tmp_path/'job')
    data=json.loads((tmp_path/'job/drafting-recovery.json').read_text())
    assert not data['section_coverage']['Subjective']['unresolved']
    assert data['section_coverage']['Plan']['unresolved']


def test_non_token_error_and_cancellation_do_not_split(tmp_path):
    def crash(*a):raise GenerationError('runtime unavailable')
    rt=Writer(crash)
    with pytest.raises(GenerationError,match='runtime unavailable'):draft_grouped(packet([source(0,'Sleep difficulty.')]),rt,tmp_path/'job')
    assert len(rt.metrics['calls'])==1
    rt=Writer();rt.cancel=True
    with pytest.raises(GenerationError,match='Cancelled'):draft_grouped(packet([source(0,'Sleep difficulty.')]),rt,tmp_path/'cancel')


def test_missing_originals_from_overview_are_retrieved_before_drafting(tmp_path,monkeypatch):
    from tests.note_generation.test_workflow import MockRuntime
    from services.note_generation.workflow import run
    import services.note_generation.workflow as workflow
    rows=[{**source(0,'I cannot find time.'),'session_id':'s'},
          {**source(1,'We discussed support groups.','CLINICIAN'),'session_id':'s'},
          {**source(2,'I will ask my doctor.'),'session_id':'s'}]
    class Runtime(MockRuntime):
        def generate(self,system,data,output=700):
            if 'primary' in data:
                return {'items':[{'text':'Clinician discussed support groups.','type':'intervention_delivered_or_discussed',
                   'source_ids':[rows[1]['id']],'status':'supported','attribution':'clinician'}]}
            return super().generate(system,data,output)
    class NoHits:
        def __init__(self,*a,**kw):pass
        def search(self,*a,**kw):return []
    monkeypatch.setattr(workflow,'EvidenceRetrievalProvider',NoHits)
    result=run({'session_id':'s','revision':digest(rows),'segments':rows},Runtime(),tmp_path/'job',mode='predefined')
    evidence=result['packet']['evidence']
    assert rows[1]['id'] in evidence and rows[0]['id'] in evidence
    assert evidence[rows[1]['id']]['source']['text']=='We discussed support groups.'
    assert evidence[rows[1]['id']]['retrieval'][0]['provider']=='overview-navigation'


def test_short_reply_does_not_link_nonadjacent_selected_turn(tmp_path):
    rows=[source(0,'Unrelated adult symptom.','CLINICIAN'),source(2,'Yeah.')]
    def result(rt,system,data,output):
        v=empty(data);v['sections']['Subjective']={'status':'documented','items':[item('Acknowledgement cannot be interpreted without preceding context.',['T1'],kind='unresolved')]};return v
    fields,_=draft_grouped(packet(rows),Writer(result),tmp_path/'job')
    assert fields[0]['items'][0]['source_ids']==[rows[1]['id']]


def test_interrupted_statement_restores_both_source_parts(tmp_path):
    rows=[source(0,'My medication needs-'),source(1,'Yes.','CLINICIAN'),source(2,'-changing, but I have not changed it.')]
    def result(rt,system,data,output):
        v=empty(data);v['sections']['Subjective']={'status':'documented','items':[item('Adult reports medication needs changing, not completed.',['T0'])]};return v
    fields,_=draft_grouped(packet(rows),Writer(result),tmp_path/'job')
    assert fields[0]['items'][0]['source_ids']==[r['id'] for r in rows]


def test_stale_group_cache_is_not_reused(tmp_path):
    rows=[source(0,'Sleep difficulty.')];rt=Writer()
    draft_grouped(packet(rows),rt,tmp_path/'job')
    changed=packet([source(0,'Sleep improved.')])
    draft_grouped(changed,rt,tmp_path/'changed')
    assert len(rt.metrics['calls'])==2


def test_identical_words_for_different_subjects_are_not_deduplicated(tmp_path):
    def result(rt,system,data,output):
        v=empty(data)
        v['sections']['Subjective']={'status':'documented','items':[item('Sleep is disrupted.',['T0'],who='adult self-report')]}
        v['sections']['Functioning']={'status':'documented','items':[item('Sleep is disrupted.',['T1'],who='adult reporting about child')]}
        return v
    fields,_=draft_grouped(packet([source(0,'I cannot sleep.'),source(1,'My child is also awake.')]),Writer(result),tmp_path/'job')
    assert fields[0]['items'] and fields[3]['items']


def test_validation_failure_falls_back_to_groups_but_runtime_error_does_not(tmp_path):
    def result(rt,system,data,output):
        if len(data['requested_sections'])==7:
            raise GenerationError('Invalid output schema: grouped response omitted a section')
        return empty(data)
    rt=Writer(result);draft_grouped(packet([source(0,'Reported sleep difficulty.')]),rt,tmp_path/'job')
    assert len(rt.metrics['calls'])==5 # bounded repair, then three related groups
    assert rt.metrics['recovery']['grouped-draft-v6']['splits']==1


def test_client_intention_requires_client_source_without_agreed_commitment(tmp_path):
    def result(rt,system,data,output):
        v=empty(data);v['sections']['Plan']={'status':'documented','items':[
            item('Adult intends to ask their doctor about options.',['T0'],kind='intention',who='adult client')]} ;return v
    fields,_=draft_grouped(packet([source(0,'I will ask my doctor.')]),Writer(result),tmp_path/'job')
    assert fields[6]['items'][0]['type']=='proposed_plan' and fields[6]['no_agreed_plan']


def test_explicit_correction_and_specific_denial_are_preserved():
    from services.note_generation.grouped import fidelity_errors
    sources={'T0':{'text':'Not really overreacting. She cares about my health.'},'T1':{'text':'I am not bloated.'}}
    bad=item('Client believes their spouse is overreacting and denies bloating or other symptoms.',['T0','T1'])
    assert len(fidelity_errors(bad,sources))==2
    good=item('Client does not regard their spouse as overreacting and denies bloating.',['T0','T1'])
    assert fidelity_errors(good,sources)==[]


def test_oversized_source_grouping_keeps_exact_span_coverage(tmp_path):
    rows=[source(0,'long source text '*700)]
    rt=Writer(max_sections=1)
    draft_grouped(packet(rows),rt,tmp_path/'job')
    data=json.loads((tmp_path/'job/draft-aliases.json').read_text())
    for spans in data['coverage'].values():
        cursor=0
        for entry in sorted(spans,key=lambda r:r['span'][0]):
            assert entry['span'][0]==cursor
            cursor=entry['span'][1]
        assert cursor==len(rows[0]['text'])
    assert data['source_metadata'][rows[0]['id']]['start_ms']==0


def test_complete_response_valid_sections_saved_and_only_failed_section_retried(tmp_path):
    def result(rt,system,data,output):
        v=empty(data)
        if len(data['requested_sections'])==7:
            v['sections']['Objective']={'status':'documented','items':[item('Reported symptom.',['T0'],kind='observation')]}
        return v
    rt=Writer(result);fields,_=draft_grouped(packet([source(0,'A reported symptom.')]),rt,tmp_path/'job')
    assert len(rt.metrics['calls'])==2
    assert rt.metrics['calls'][1]['data']['requested_sections']==['Objective']
    before=len(rt.metrics['calls'])
    resumed,_=draft_grouped(packet([source(0,'A reported symptom.')]),rt,tmp_path/'resume')
    assert len(rt.metrics['calls'])==before and fields==resumed


def test_output_growth_then_section_validation_recovers_without_accepting_partial(tmp_path):
    def result(rt,system,data,output):
        if len(rt.metrics['calls'])==1:raise OutputLimitError('limit')
        v=empty(data)
        if len(data['requested_sections'])==7:
            v['sections']['Objective']={'status':'documented','items':[item('Client report.',['T0'],kind='observation')]}
        return v
    rt=Writer(result);fields,_=draft_grouped(packet([source(0,'Client report.')]),rt,tmp_path/'job')
    assert len(rt.metrics['calls'])==3 and not fields[1]['items']
    assert rt.metrics['calls'][1]['output']>rt.metrics['calls'][0]['output']


def test_formulation_schema_requires_support_only_for_formulations():
    data={'requested_sections':['Assessment'],'sources':[{'id':'T0','role':'CLIENT','text':'I cannot find time.'}],'context':[]}
    branches=schema(data)['properties']['sections']['properties']['Assessment']['properties']['items']['items']['oneOf']
    ordinary,formulation=branches
    assert set(ordinary['properties']['kind']['enum'])=={'assessment','unresolved'}
    assert 'basis' not in ordinary['properties'] and 'missing' not in ordinary['properties']
    assert formulation['properties']['kind']['enum']==['formulation']
    assert {'basis','missing'}<=set(formulation['required'])
    assert formulation['properties']['basis']['minLength']==1
    value={'sections':{'Assessment':{'status':'documented','items':[item('Time constraints may limit participation.',['T0'],kind='formulation',who='AI interpretation',basis='Reported time constraint.',missing=[])]}}}
    validate(value,data) # Empty is valid; do not invent missing clinical facts.
    for bad in ['', ' ', None]:
        invalid=copy.deepcopy(value);invalid['sections']['Assessment']['items'][0]['basis']=bad
        with pytest.raises(GenerationError,match='nonempty basis'):validate(invalid,data)
    invalid=copy.deepcopy(value);del invalid['sections']['Assessment']['items'][0]['missing']
    with pytest.raises(GenerationError,match='missing-information list'):validate(invalid,data)


def test_completed_legacy_schema_checkpoints_are_upgraded_without_generation(tmp_path,monkeypatch):
    import services.note_generation.grouped as grouped
    strict_schema=grouped.schema
    rows=[source(0,'Reported sleep difficulty.')]
    monkeypatch.setattr(grouped,'schema',lambda data,**kw:strict_schema(data,strict_formulation=False))
    rt=Writer();before,_=draft_grouped(packet(rows),rt,tmp_path/'old')
    assert len(rt.metrics['calls'])==1
    monkeypatch.setattr(grouped,'schema',strict_schema)
    after,_=draft_grouped(packet(rows),rt,tmp_path/'new')
    assert after==before and len(rt.metrics['calls'])==1
    assert rt.metrics['recovery']['grouped-draft-v6']['cache_hits']==1


def test_recovery_error_is_formatted_once_with_short_source_range(tmp_path):
    def invalid(*a):raise GenerationError('Invalid factual item: formulation requires support')
    rt=Writer(invalid,max_sections=1)
    with pytest.raises(GenerationError) as error:
        draft_grouped(packet([source(i,'Reported symptom.') for i in range(10)]),rt,tmp_path/'job')
    assert str(error.value).count('Completed work saved.')==1
    assert 'through T9:' in str(error.value)
    assert len(str(error.value))<500

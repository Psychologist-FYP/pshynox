import json
import threading
import pytest
from services.note_generation.runtime import Runtime, GenerationError, OutputLimitError, finish_reason
from services.note_generation.recovery import Recovery, deduplicate, split
from services.note_generation.workflow import pack


class Fake:
    context=4096
    safety_margin=64
    model={'sha256':'mock-model'}
    def __init__(self, outcomes=(), room=1600):
        self.metrics={};self.outcomes=list(outcomes);self.requests=[];self.room=room;self.cancel=False
    def check(self):
        if self.cancel:raise GenerationError('Cancelled')
    def fits(self,system,data,output):return output<=self.room
    def generate(self,system,data,output):
        self.requests.append((data,output))
        outcome=self.outcomes.pop(0) if self.outcomes else {'items':[]}
        if isinstance(outcome,Exception):raise outcome
        return outcome


def row(i='a',text='x'*256):return {'id':i,'text':text,'speaker_role':'CLIENT','start_ms':0,'end_ms':100,'revision':1}
def validate(value,data):assert isinstance(value['items'],list)
def payload(rows):return {'primary':rows}
def limit():return OutputLimitError('limit')


def test_exact_budget_uses_rendered_template(tmp_path):
    rt=Runtime(tmp_path);rt.context=100;rt.safety_margin=4
    rt.prompt=lambda system,data:'<system>'+system+'</system><user>'+json.dumps(data)+'</user><assistant>'
    rt.count=lambda prompt:len(prompt) # mock actual tokenizer, with overhead
    count=rt.count(rt.prompt('policy',[]))
    assert rt.fits('policy',[],100-4-count)
    assert not rt.fits('policy',[],100-3-count)


@pytest.mark.parametrize('result',[{'stop_type':'limit'},{'finish_reason':'length'},{'stopped_limit':True},{'truncated':True},{'finish_reason':'max_tokens'}])
def test_finish_limits(result):assert finish_reason(result)=='output_limit'


def test_valid_json_at_limit_is_withheld(tmp_path):
    rt=Runtime(tmp_path);rt.writer='qwen';rt.model={'sha256':'model'}
    rt.prompt=lambda *a:'template';rt.count=lambda p:10
    rt.request=lambda *a:{'content':'{"items":[]}','stop_type':'limit','tokens_predicted':800}
    with pytest.raises(OutputLimitError):rt.generate('system',[],800)
    assert rt.metrics['calls'][0]['formatted_prompt_tokens']==10
    assert not (tmp_path/'generated-responses.jsonl').exists()


def test_larger_retry_and_checkpoint(tmp_path):
    rt=Fake([limit(),{'items':[]}]);rec=Recovery(rt,tmp_path/'job','rev','v4')
    result=rec.process('system',[row()],payload,validate)
    assert [o for _,o in rt.requests]==[800,1200]
    assert result and rec.stats['retries']==1
    resumed=Recovery(rt,tmp_path/'other','rev','v4')
    resumed.process('system',[row()],payload,validate)
    assert len(rt.requests)==2 and resumed.stats['cache_hits']==1
    Recovery(rt,tmp_path/'new-revision','changed','v4').process('system',[row()],payload,validate)
    assert len(rt.requests)==3


def test_no_room_splits_and_overlap_does_not_count(tmp_path):
    rt=Fake([limit()],room=800);rec=Recovery(rt,tmp_path/'job','rev','v4')
    result=rec.process('system',[row('a'),row('b')],lambda rows:{'primary':rows,'context':[row('neighbour')]},validate)
    assert [o for _,o in rt.requests]==[800,800,800]
    assert [r['id'] for rows,_ in result for r in rows]==['a','b']
    assert rec.stats['splits']==1


def test_split_retry_and_work_exhaustion(tmp_path,monkeypatch):
    monkeypatch.setenv('NOTE_MAX_SPLIT_DEPTH','1')
    rt=Fake([limit()]*20,room=800);rec=Recovery(rt,tmp_path/'job','rev','v4')
    with pytest.raises(GenerationError,match='Split depth'):rec.process('s',[row()],payload,validate)
    monkeypatch.setenv('NOTE_MAX_GENERATION_CALLS','1')
    rt=Fake([limit()],room=800);rec=Recovery(rt,tmp_path/'work','rev','v4')
    with pytest.raises(GenerationError,match='request budget'):rec.process('s',[row('a'),row('b')],payload,validate)
    assert len(rt.requests)==1


def test_minimum_span():
    with pytest.raises(GenerationError,match='Minimum'):split([row(text='a'*100)],64)
    source=row(text='abcdef'*100);parts=split([source],64)
    assert ''.join(p[0]['text'] for p in parts)==source['text']
    assert [p[0]['span'] for p in parts]==[[0,300],[300,600]]
    assert all(p[0]['start_ms']==0 and p[0]['revision']==1 for p in parts)


def test_split_manifest_resume_and_non_token_failure(tmp_path):
    rt=Fake([limit(),{'items':[]},GenerationError('runtime crashed')],room=800)
    rec=Recovery(rt,tmp_path/'job','rev','v4')
    with pytest.raises(GenerationError,match='runtime crashed'):rec.process('s',[row('a'),row('b')],payload,validate)
    assert rec.stats['splits']==1
    resumed=Recovery(rt,tmp_path/'resume','rev','v4')
    resumed.process('s',[row('a'),row('b')],payload,validate)
    assert len(rt.requests)==4 # failed parent and successful child are not regenerated
    assert resumed.stats['cache_hits']==1


def test_cancellation_during_recovery(tmp_path):
    rt=Fake([limit()],room=800);rec=Recovery(rt,tmp_path/'job','rev','v4',lambda s:setattr(rt,'cancel',True))
    with pytest.raises(GenerationError,match='Cancelled'):rec.process('s',[row('a'),row('b')],payload,validate)
    assert len(rt.requests)==1


def test_deterministic_consolidation_retains_conflicts():
    a={'text':'No sleep difficulty','status':'explicit_denial','source_ids':['a'],'attribution':'client'}
    b={**a,'source_ids':['b']}
    conflict={**a,'status':'conflicting','source_ids':['c']}
    result=deduplicate([a,b,conflict]*10000)
    assert len(result)==2 and result[0]['source_ids']==['a','b']


def test_oversized_turn_packing_uses_measured_payload():
    class Budget:
        def fits(self,system,rows,output=700):return len(json.dumps(rows))+output+64<=1200
    source=row(text='yes '*1000)
    chunks=pack(Budget(),'policy',[source],output=800)
    assert ''.join(r['text'] for c in chunks for r in c)==source['text']
    spans=[r['span'] for c in chunks for r in c]
    assert spans[0][0]==0 and spans[-1][1]==len(source['text'])
    assert all(a[1]==b[0] for a,b in zip(spans,spans[1:]))


def test_short_reply_has_neighbour_context_and_exact_coverage(tmp_path):
    from tests.note_generation.test_workflow import MockRuntime
    from services.note_generation.workflow import run
    from services.retrieval.engine import digest
    class Small(MockRuntime):
        def __init__(self):super().__init__();self.inputs=[];self.metrics={}
        def fits(self,system,data,output=700):
            if isinstance(data,dict) and 'primary' in data:return len(data['primary'])==1
            return super().fits(system,data,output)
        def generate(self,system,data,output=700):
            if isinstance(data,dict) and 'primary' in data:self.inputs.append(data)
            return super().generate(system,data,output)
    data=[{**row('q','Would you try a sleep diary?'),'session_id':'s','speaker_role':'CLINICIAN'},
          {**row('r','Yes, before next session.'),'session_id':'s','start_ms':101,'end_ms':200}]
    rt=Small();result=run({'session_id':'s','revision':digest(data),'segments':data},rt,tmp_path/'job',mode='predefined')
    reply=next(d for d in rt.inputs if d['primary'][0]['id']=='r')
    assert reply['context'][0]['id']=='q'
    assert reply['context'][0]['text']==data[0]['text']
    assert [r['id'] for r in result['packet']['overview_coverage']]==['q','r']
    ledger=json.loads((tmp_path/'job/overview-recovery.json').read_text())
    assert ledger['unresolved']==[]


def test_coverage_rejects_gaps_duplicates_and_foreign_ids():
    from services.note_generation.recovery import verify_coverage
    source=row(text='abcd')
    verify_coverage([source],[{'id':'a','span':[0,2]},{'id':'a','span':[2,4]}])
    for spans in [[{'id':'a','span':[1,4]}],[{'id':'a','span':[0,4]}]*2,[{'id':'foreign','span':[0,4]}]]:
        with pytest.raises(GenerationError):verify_coverage([source],spans)


def test_second_limit_splits_and_schema_retry_is_separate(tmp_path):
    rt=Fake([limit(),limit(),{'items':[]},{'items':[]}]);rec=Recovery(rt,tmp_path/'job','rev','v4')
    result=rec.process('s',[row('a'),row('b')],payload,validate)
    assert len(result)==2 and rec.stats['splits']==1 and rec.stats['retries']==1
    assert [o for _,o in rt.requests]==[800,1200,800,800]
    rt=Fake([GenerationError('Malformed JSON'),{'items':[]}]);rec=Recovery(rt,tmp_path/'repair','rev','different')
    rec.process('s',[row()],payload,validate)
    assert rec.stats['splits']==0 and [o for _,o in rt.requests]==[800,800]


def test_cache_model_prompt_context_settings_invalidation(tmp_path,monkeypatch):
    rt=Fake();Recovery(rt,tmp_path/'job','rev','v4').process('s',[row()],payload,validate)
    Recovery(rt,tmp_path/'job','rev','v5').process('s',[row()],payload,validate)
    rt.context=5000
    Recovery(rt,tmp_path/'job','rev','v5').process('s',[row()],payload,validate)
    rt.model={'sha256':'changed-model'}
    Recovery(rt,tmp_path/'job','rev','v5').process('s',[row()],payload,validate)
    monkeypatch.setenv('NOTE_OVERVIEW_MAX_OUTPUT','1800')
    Recovery(rt,tmp_path/'job','rev','v5').process('s',[row()],payload,validate)
    assert len(rt.requests)==5


def test_larger_retry_uses_exact_available_room(tmp_path):
    rt=Fake([limit(),{'items':[]}],room=1100)
    rt.prompt=lambda *a:'formatted'
    rt.count=lambda prompt:4096-64-1100
    rec=Recovery(rt,tmp_path/'job','rev','v4')
    rec.process('s',[row()],payload,validate)
    assert [o for _,o in rt.requests]==[800,1100]


def test_schema_constrains_original_ids_and_optional_overview_fields():
    from services.note_generation.recovery import response_schema
    schema=response_schema({'primary':[row('primary')],'context':[row('context')]},True)
    item=schema['properties']['items']['items']
    assert item['properties']['source_ids']['items']['enum']==['primary','context']
    assert 'timeframe' not in item['required'] and 'basis' not in item['properties']
    assert item['additionalProperties'] is False


def test_observed_medication_omission_gets_one_corrective_retry(tmp_path):
    from tests.note_generation.test_workflow import MockRuntime
    from services.note_generation.workflow import run
    from services.retrieval.engine import digest
    class Writer(MockRuntime):
        def __init__(self):super().__init__();self.metrics={};self.overview_calls=0;self.repair_seen=False
        def generate(self,system,data,output=700):
            value=super().generate(system,data,output)
            if isinstance(data,dict) and 'primary' in data:
                self.overview_calls+=1
                if self.overview_calls==1:return {'items':[]}
                self.repair_seen='Correct the validation failure' in system
                value['items'][0]['text']='Client reports medication needs changing.'
            return value
    data=[{**row('med','My medication needs changing.'),'session_id':'s'}]
    rt=Writer();result=run({'session_id':'s','revision':digest(data),'segments':data},rt,tmp_path/'job',mode='predefined')
    assert rt.overview_calls==2 and rt.repair_seen
    assert 'medication' in result['packet']['overview'][0]['text']

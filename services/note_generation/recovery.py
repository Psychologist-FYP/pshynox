"""Bounded source-preserving recovery; caches and coverage never depend on model prose."""
import json
import os
from pathlib import Path
from .runtime import GenerationError, OutputLimitError
from .storage import write
from .terminal import trace_for
from services.retrieval.engine import digest


class IncompleteGenerationError(GenerationError):
    """A source-scoped failure already formatted for the user; do not wrap again."""


def settings():
    values = {k:int(os.getenv(env, default)) for k, env, default in [
        ('initial','NOTE_OVERVIEW_OUTPUT','800'), ('maximum','NOTE_OVERVIEW_MAX_OUTPUT','1600'),
        ('depth','NOTE_MAX_SPLIT_DEPTH','8'), ('calls','NOTE_MAX_GENERATION_CALLS','96'),
        ('turns','NOTE_OVERVIEW_MAX_TURNS','16'), ('minimum','NOTE_MIN_SPAN_CHARS','64')]}
    if not (128 <= values['initial'] <= values['maximum'] and 0 <= values['depth'] <= 16
            and values['turns'] >= 2 and values['calls'] >= 1 and values['minimum'] >= 16):
        raise GenerationError('Invalid note recovery configuration')
    return values


def bounds(rows):
    return [{'id':r['id'], 'span':r.get('span',[0,len(r['text'])])} for r in rows]


def verify_coverage(sources, completed):
    expected = {r['id']:len(r['text']) for r in sources}
    if any(r['id'] not in expected for r in completed):
        raise GenerationError('Coverage contains a foreign source')
    for source_id, length in expected.items():
        cursor = 0
        spans = sorted(r['span'] for r in completed if r['id']==source_id)
        for start, end in spans:
            if start != cursor or not start <= end <= length:
                raise GenerationError('Coverage contains a gap or duplicate span: '+source_id)
            cursor = end
        if not spans or cursor != length:
            raise GenerationError('Coverage unresolved: '+source_id)


def split(rows, minimum):
    if len(rows) > 1:
        mid = len(rows)//2
        return [rows[:mid], rows[mid:]]
    row = rows[0]
    if len(row['text']) < minimum*2:
        raise GenerationError('Minimum useful source span reached')
    mid = len(row['text'])//2
    start = row.get('span',[0,0])[0]
    return [[{**row,'text':row['text'][:mid],'span':[start,start+mid]}],
            [{**row,'text':row['text'][mid:],'span':[start+mid,start+len(row['text'])]}]]


def deduplicate(items):
    # Merge only identical structured facts. Differences in attribution/status/time survive.
    found = {}
    for item in items:
        key = digest({k:v for k,v in item.items() if k != 'source_ids'})
        if key not in found:
            found[key] = {**item, 'source_ids':list(item['source_ids'])}
        else:
            found[key]['source_ids'] = list(dict.fromkeys(found[key]['source_ids']+item['source_ids']))
    return list(found.values())


def response_schema(data, overview):
    """Constrain references to supplied originals; grammar is not evidence validation."""
    sources = data.get('primary',[])+data.get('context',[]) if overview else data.get('sources',[])
    ids = list(dict.fromkeys(r['id'] for r in sources))
    if not ids: return {'type':'object'}
    kinds = ['client_report','clinician_observation','documented_clinician_assessment',
             'intervention_delivered_or_discussed','proposed_plan','agreed_plan','unknown_or_unresolved']
    if not overview: kinds.append('ai_proposed_formulation')
    props = {k:{'type':'string'} for k in ('text','attribution','timeframe','uncertainty')}
    props.update(type={'type':'string','enum':kinds},
                 status={'type':'string','enum':['supported','explicit_denial','conflicting']},
                 source_ids={'type':'array','minItems':1,'items':{'type':'string','enum':ids}})
    required = ['text','type','source_ids','attribution']
    if not overview:
        props.update(basis={'type':'string'},missing_information={'type':'array','items':{'type':'string'}})
        required += ['status','timeframe','uncertainty']
    result = {'type':'object','properties':{'items':{'type':'array','items':{
        'type':'object','properties':props,'required':required,'additionalProperties':False}}},
        'required':['items'],'additionalProperties':False}
    if not overview:
        result['properties']['limitations']={'type':'array','items':{'type':'string'}}
        result['required'].append('limitations')
    return result


class Recovery:
    def __init__(self, runtime, folder, revision, version, progress=lambda s:None, *, schema_factory=None, compatible_schema_factory=None, maximum=None):
        self.rt, self.folder = runtime, Path(folder)
        self.log = trace_for(runtime, self.folder)
        self.revision, self.version, self.progress = revision, version, progress
        self.config = settings()
        self.compatible_schema_factory = compatible_schema_factory
        self.schema_factory = schema_factory or (lambda data: response_schema(data, self.version.startswith('compact-overview')))
        if maximum is not None:
            self.config['maximum'] = maximum
        if self.config['initial'] + getattr(runtime,'safety_margin',64) >= runtime.context:
            raise GenerationError('NOTE_OVERVIEW_OUTPUT plus safety margin must leave room for the prompt')
        self.cache = self.folder.parent/'cache-v4'
        self.cache.mkdir(parents=True,exist_ok=True)
        self.ledger = {}
        self.manifest = []
        self.stats = {'calls':0,'retries':0,'splits':0,'cache_hits':0}

    def save(self):
        completed = [span for entry in self.ledger.values() if entry['status']=='completed' for span in entry['primary']]
        unresolved = []
        for source in self.manifest:
            cursor = source['span'][0]
            for span in sorted((r['span'] for r in completed if r['id']==source['id'])):
                if span[0] > cursor: unresolved.append({'id':source['id'],'span':[cursor,span[0]]})
                cursor = max(cursor,span[1])
            if cursor < source['span'][1]: unresolved.append({'id':source['id'],'span':[cursor,source['span'][1]]})
        write(self.folder/('overview-recovery.json' if self.version.startswith('compact-overview') else 'drafting-recovery.json'),
              {'revision':self.revision,'version':self.version,'expected_sources':self.manifest,
               'unresolved':unresolved,'stats':self.stats,'chunks':self.ledger})
        self.rt.metrics.setdefault('recovery', {})[self.version] = self.stats.copy()

    def cache_key(self,system,data,output,schema):
        return digest([self.revision,self.rt.model['sha256'],self.version,system,data,
                       self.rt.context,getattr(self.rt,'safety_margin',64),self.config,output,
                       self.rt.metrics.get('runtime_version'),'temperature=0;seed=42',schema])

    def process(self, system, rows, payload, validate, *, output=None, depth=0, label='overview'):
        self.rt.check()
        self.log.stage = label
        self.log.event('chunk.start', version=self.version, depth=depth, primary=bounds(rows))
        output = output or self.config['initial']
        data = payload(rows)
        schema = self.schema_factory(data)
        key = self.cache_key(system,data,output,schema)
        path = self.cache/(key+'.json')
        entry = {'stage':label, 'primary':bounds(rows), 'status':'unresolved','depth':depth,
                 **({'sections':data['requested_sections']} if 'requested_sections' in data else {})}
        self.ledger[key] = entry
        self.save()
        try:
            if not path.exists() and self.compatible_schema_factory is not None:
                old_key=self.cache_key(system,data,output,self.compatible_schema_factory(data))
                old_path=self.cache/(old_key+'.json')
                if old_path.exists():
                    previous=json.loads(old_path.read_text())
                    if previous.get('key')==old_key and previous.get('status') in {'completed','split'}:
                        if previous['status']=='completed':validate(previous['value'],data)
                        write(path,{**previous,'key':key})
                        self.log.event('chunk.checkpoint_upgraded',key=old_key,new_key=key)
            cached = json.loads(path.read_text()) if path.exists() else {}
            if cached.get('key') != key:
                cached = {}
            if cached.get('status') == 'completed':
                value = cached['value']
                validate(value, data)
                self.stats['cache_hits'] += 1
                self.log.event('chunk.cache_hit', key=key, items=len(value.get('items',[])))
                self.log.block('CACHED CHUNK RESULT', value)
            else:
                if cached.get('status') == 'split':
                    return self.children(system, rows, payload, validate, output, depth, label, entry, path, key)
                if not self.rt.fits(system,data,output):
                    return self.children(system, rows, payload, validate, output, depth, label, entry, path, key)
                try:
                    value = self.call(system,data,output,validate)
                except OutputLimitError:
                    self.log.event('chunk.output_limit', output_reserved=output, depth=depth)
                    # One meaningful increase, bounded by the ACTUAL formatted prompt.
                    larger = min(self.config['maximum'], max(output+256, int(output*1.5)))
                    if hasattr(self.rt,'count') and hasattr(self.rt,'prompt'):
                        available = self.rt.context - self.rt.safety_margin - self.rt.count(self.rt.prompt(system,data))
                        larger = min(larger, available)
                    if larger >= output+256 and self.rt.fits(system,data,larger):
                        self.stats['retries'] += 1
                        self.log.event('chunk.retry_output', previous_output=output, output_reserved=larger)
                        entry['output_retry'] = larger
                        self.save()
                        self.progress('Processing a smaller conversation section.' if depth else 'Retrying conversation section with more output space.')
                        try:
                            value = self.call(system,data,larger,validate)
                        except OutputLimitError:
                            return self.children(system,rows,payload,validate,output,depth,label,entry,path,key)
                        except GenerationError as exc:
                            if self.recover_validation_error(exc,data):
                                self.log.event('chunk.validation_fallback',error=str(exc))
                                return self.children(system,rows,payload,validate,output,depth,label,entry,path,key)
                            raise
                    else:
                        return self.children(system,rows,payload,validate,output,depth,label,entry,path,key)
                except GenerationError as exc:
                    if self.recover_validation_error(exc,data):
                        self.log.event('chunk.validation_fallback',error=str(exc))
                        return self.children(system,rows,payload,validate,output,depth,label,entry,path,key)
                    raise
                write(path,{'key':key,'status':'completed','value':value})
                self.log.event('chunk.checkpoint_saved', key=key, path=str(path), items=len(value.get('items',[])))
            entry['status']='completed'
            self.save()
            return [(rows,value)]
        except GenerationError as exc:
            self.log.event('chunk.failed', depth=depth, primary=bounds(rows), error=str(exc))
            entry['error']=str(exc)
            self.save()
            if isinstance(exc,IncompleteGenerationError):raise
            ranges=bounds(rows)
            def describe(r):return f"{r['id']}:{r['span'][0]}–{r['span'][1]}"
            source=describe(ranges[0]) if len(ranges)==1 else f'{describe(ranges[0])} through {describe(ranges[-1])} ({len(ranges)} source spans)'
            raise IncompleteGenerationError(f'{label} incomplete at {source}. Completed work saved. Retry/resume by generating a new version. Reason: {exc}') from exc

    def recover_validation_error(self, error, data):
        return False

    def call(self, system, data, output, validate):
        # JSON/schema repair has its own single retry; token failures bypass it.
        repair_instruction = ''
        for attempt in range(2):
            self.rt.check()
            if self.stats['calls'] >= self.config['calls']:
                raise GenerationError('Total generation request budget exhausted')
            self.stats['calls'] += 1
            self.save()
            try:
                self.rt.prompt_version = self.version
                self.rt.response_schema = self.schema_factory(data)
                value = self.rt.generate(system+repair_instruction,data,output)
                validate(value,data)
                self.log.event('chunk.validated', items=len(value.get('items',[])))
                return value
            except OutputLimitError:
                raise
            except GenerationError as exc:
                if attempt or not any(x in str(exc) for x in ('Malformed JSON','Invalid output schema','Invalid typed','Invalid factual')):
                    raise
                self.stats['retries'] += 1
                self.log.event('chunk.retry_schema', error=str(exc))
                repair_instruction = '\nCorrect the validation failure in a fresh complete response: '+str(exc)+'. Return the required JSON only.'

    def children(self, system, rows, payload, validate, output, depth, label, entry, path, key):
        self.rt.check()
        if depth >= self.config['depth']:
            raise GenerationError('Split depth budget exhausted')
        parts = split(rows,self.config['minimum'])
        write(path,{'key':key,'status':'split'})
        entry['status']='split'
        self.stats['splits'] += 1
        self.log.event('chunk.split', depth=depth, children=[bounds(part) for part in parts])
        self.save()
        self.progress('Processing a smaller conversation section.')
        result = []
        for part in parts:
            result.extend(self.process(system,part,payload,validate,output=output,depth=depth+1,label=label))
        return result

"""Deterministic orchestration. Overview is navigation, original turns are evidence."""
import json
import os
import re
import time
from pathlib import Path
from services.retrieval.engine import digest, EvidenceRetrievalProvider, SearchScope
from services.retrieval.config import RetrievalSettings
from .terminal import trace_for
from .runtime import GenerationError

from .prompts import POLICY, OVERVIEW, OVERVIEW_VERSION, DRAFT, SECTION_RULES, ALLOWED_TYPES, FALLBACKS
VERSION = 'local-notes-v5'
from .recovery import Recovery, deduplicate, verify_coverage
SECTIONS = ['Subjective','Objective','Assessment','Functioning','Progress','Interventions','Plan']
SEARCHES = ['client concerns symptoms feelings experiences', 'functioning work family sleep context',
            'clinician observations assessment', 'changes progress since recently timeframe',
            'interventions discussed practice', 'agreed plan follow up next session']

def section_prompt(section):
    return DRAFT+'\nRequested section: '+section+'\n'+SECTION_RULES[section]
PLAN = POLICY + 'Return {"requests":[{"op":"search","query":"plain search"},{"op":"expand","source_ids":["id"]}]} or {"requests":[],"stop":true}. Maximum three requests. Resolve gaps, contradictions and unclear agreement using original context. Selected conversation only; no other tools.'


def pack(runtime, system, rows, output=700):
    """Complete turns preferred. Oversized turns get exact, non-overlapping character spans."""
    chunks, current = [], []
    for row in rows:
        candidates = [row]
        if not runtime.fits(system, [row], output):
            text, offset, candidates = row['text'], 0, []
            while offset < len(text):
                lo, hi = 0, len(text)-offset
                while lo < hi:
                    mid = (lo+hi+1)//2
                    span = {**row, 'text':text[offset:offset+mid], 'span':[row.get('span',[0])[0]+offset,row.get('span',[0])[0]+offset+mid]}
                    if runtime.fits(system, [span], output): lo = mid
                    else: hi = mid-1
                if lo == 0:
                    raise GenerationError('Source metadata alone exceeds context budget')
                minimum = int(os.getenv('NOTE_MIN_SPAN_CHARS','64'))
                if lo < minimum and len(text)-offset > lo:
                    raise GenerationError('Minimum useful source span cannot fit context budget')
                candidates.append({**row,'text':text[offset:offset+lo], 'span':[row.get('span',[0])[0]+offset,row.get('span',[0])[0]+offset+lo]})
                offset += lo
        for candidate in candidates:
            if current and not runtime.fits(system, current+[candidate], output):
                chunks.append(current)
                current = []
            current.append(candidate)
    if current: chunks.append(current)
    return chunks


def validate_items(value, allowed, *, overview=False, typed=False):
    if not isinstance(value, dict) or not isinstance(value.get('items'), list) or (not overview and len(value['items']) > 30):
        raise GenerationError('Invalid output schema: items array required')
    for item in value['items']:
        if not isinstance(item, dict) or not isinstance(item.get('text'), str) or not item['text'].strip():
            raise GenerationError('Invalid factual item')
        refs = item.get('source_ids')
        if not isinstance(refs, list) or not refs or any(not isinstance(i, str) or i not in allowed for i in refs):
            raise GenerationError('Missing, fabricated or out-of-scope source IDs; output withheld')
        if any(c in item['text'] for c in ['"','“','”']):
            raise GenerationError('Generated quotation withheld: verbatim quotations are not supported')
        if not overview and item.get('status') not in {'supported','explicit_denial','conflicting'}:
            raise GenerationError('Invalid factual status')
    if typed:
        from services.api.schemas import GeneratedNoteItem
        from pydantic import ValidationError
        for item in value['items']:
            try:
                normalized = GeneratedNoteItem.model_validate({**({'status':'supported','timeframe':'not established','uncertainty':'none'} if overview else {}), **item, 'id':'validation'})
            except ValidationError as exc:
                raise GenerationError('Invalid typed note item: required attributes are missing or invalid') from exc
            if item['type']=='ai_proposed_formulation' and (not item.get('basis','').strip() or overview):
                raise GenerationError('Formulation requires a basis and is not allowed in overview extraction')
            item.clear()
            item.update(normalized.model_dump(exclude={'id','review_status','review_text','review_history'}))
    if not overview and (not isinstance(value.get('limitations'), list) or any(not isinstance(x,str) for x in value['limitations'])):
        raise GenerationError('Limitations array required')
    return value['items']


def requests(value, valid_ids, seen, limit=3):
    if not isinstance(value, dict) or not set(value) <= {'requests','stop'} or ('stop' in value and type(value['stop']) is not bool) or not isinstance(value.get('requests'), list) or len(value['requests']) > limit:
        raise GenerationError('Malformed or excessive retrieval requests')
    out = []
    for r in value['requests']:
        if not isinstance(r, dict): raise GenerationError('Invalid request')
        if r.get('op') == 'search' and set(r) == {'op','query'}:
            if not isinstance(r['query'],str) or not 1 <= len(r['query'].strip()) <= 300:
                raise GenerationError('Search must contain 1..300 characters')
            key = ' '.join(r['query'].casefold().split())
        elif r.get('op') == 'expand' and set(r) == {'op','source_ids'}:
            ids = r['source_ids']
            if not isinstance(ids,list) or not 1 <= len(ids) <= 5 or any(not isinstance(i,str) or i not in valid_ids for i in ids):
                raise GenerationError('Invalid expansion IDs')
            key = digest(ids)
        else: raise GenerationError('Unsupported retrieval operation or scope')
        if key in seen: raise GenerationError('Duplicate retrieval request')
        seen.add(key)
        out.append(r)
    return out


def run(snapshot, runtime, folder, *, writer='qwen', mode='followup', provider='bm25', scope=None, progress=lambda x:None, rounds=2):
    rounds=int(os.getenv('NOTE_FOLLOWUP_ROUNDS',str(rounds)))
    request_limit=int(os.getenv('NOTE_REQUESTS_PER_ROUND','3'))
    if not 1 <= request_limit <= 3: raise GenerationError('NOTE_REQUESTS_PER_ROUND must be 1..3')
    if writer not in {'qwen','omi'} or mode not in {'full','predefined','followup'} or provider not in {'bm25','e5'} or not 0 <= rounds <= 2:
        raise GenerationError('Unsupported workflow configuration')
    start = time.monotonic()
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    log = trace_for(runtime, folder)
    log.event('workflow.start', mode=mode, writer=writer, provider=provider, version=VERSION)
    from services.api.schemas import TranscriptSegment
    segments = [TranscriptSegment.model_validate(s) for s in snapshot['segments']]
    sid = snapshot['session_id']
    if not segments or any(s.session_id != sid for s in segments):
        raise GenerationError('Empty or cross-session transcript')
    rows = [s.model_dump(mode='json') for s in segments]
    ids = {s.id for s in segments}
    revision = digest(rows)
    if snapshot['revision'] != revision: raise GenerationError('Snapshot revision mismatch')
    trace, overview, evidence, timings = [], [], {}, {}
    def stage(name):
        runtime.check()
        log.stage = name
        log.event('stage.start', elapsed_seconds=round(time.monotonic()-start,2))
        progress(name)
        timings[name] = time.monotonic()-start
    def generate(system, data, output=700):
        runtime.response_schema = {'type':'object'}
        last = None
        for _ in range(2):
            runtime.check()
            try: return runtime.generate(system, data, output)
            except GenerationError as exc:
                last = exc
                if 'Malformed JSON' not in str(exc): raise
        raise last
    runtime.load('qwen')
    packet = {'version':VERSION,'mode':mode,'session_id':sid,'revision':revision,'overview':overview,'evidence':evidence,
              'trace':trace,'coverage_limits':[], 'full_transcript_turns':len(rows),'source_order':[r['id'] for r in rows],
              'source_timelines':scope.timeline_ids if scope else {}}
    def search(query):
        log.event('retrieval.search', provider=provider)
        log.block('RETRIEVAL QUERY', query)
        hits = retrieval.search(query, sid, segments, 4, scope=scope or SearchScope())
        found = []
        for hit in hits:
            for src in hit.sources:
                if src.session_id != sid or src.id not in ids: raise GenerationError('Retrieval scope violation')
                entry = evidence.setdefault(src.id, {'source':src.model_dump(mode='json'), 'retrieval':[]})
                entry['retrieval'].append({'query':query,'provider':provider,'rank':hit.rank,'score':hit.score,
                                           'central':src.id == hit.segment_id})
                found.append(src.id)
        log.event('retrieval.results', hits=len(hits), source_ids=list(dict.fromkeys(found)), selected_total=len(evidence))
        trace.append({'op':'search','query':query,'source_ids':list(dict.fromkeys(found))})
    if mode == 'full':
        # Eligibility uses the exact draft prompt and complete input, never truncation.
        for section in SECTIONS:
            if not runtime.fits(section_prompt(section), rows):
                raise GenerationError('Full-transcript baseline ineligible: entire input does not fit')
        evidence.update({r['id']:{'source':r,'retrieval':[{'provider':'full-transcript','central':True}]} for r in rows})
    else:
        stage('overview')
        recovery = Recovery(runtime, folder, revision, OVERVIEW_VERSION, progress)
        # Keep timestamps/revisions; omit unrelated storage metadata from model input.
        sources = [{k:r[k] for k in ('id','speaker_role','text','start_ms','end_ms','revision') if k in r} for r in rows]
        recovery.manifest = [{'id':r['id'],'span':[0,len(r['text'])]} for r in sources]
        recovery.save()
        positions = {r['id']:i for i,r in enumerate(sources)}
        def overview_payload(primary):
            first, last = positions[primary[0]['id']], positions[primary[-1]['id']]
            context = []
            # Bounded neighbour spans are explicitly labelled and never counted as coverage.
            for index in (first-1,last+1):
                if 0 <= index < len(sources):
                    r = sources[index]
                    start = max(0,len(r['text'])-256) if index < first else 0
                    end = min(len(r['text']),start+256)
                    context.append({**r,'text':r['text'][start:end],'span':[start,end]})
            if primary[0].get('span',[0])[0] > 0:
                r = sources[first]; end=primary[0]['span'][0]; start=max(0,end-256)
                context.append({**r,'text':r['text'][start:end],'span':[start,end]})
            return {'primary':primary,'context':context,'transcript_revision':revision}
        class OverviewBudget:
            def fits(self, system, chunk, output):
                return len(chunk) <= recovery.config['turns'] and runtime.fits(system,overview_payload(chunk),output)
        chunks = pack(OverviewBudget(), OVERVIEW, sources, output=recovery.config['initial'])
        log.event('overview.chunks_planned', chunks=len(chunks), turns=len(sources), output_reserved=recovery.config['initial'])
        coverage = []
        def validate_overview(value, data):
            items = validate_items(value,{r['id'] for r in data['primary']+data['context']},overview=True,typed=True)
            if any(re.search(r'\bmedications?\b', r['text'], re.I) for r in data['primary']) and not any(re.search(r'\bmedications?\b', i['text'], re.I) for i in items):
                raise GenerationError('Invalid factual item: medication information omitted; retain its attribution, negation and needed versus completed status')
            # Retain valid neighbour facts as hints; they add no primary coverage.
            # Exact duplicates are merged after all primary ranges complete.
            # Keep compact hints compact after typed validation, too.
            for item in items:
                for field, default in [('basis',''),('missing_information',[]),('timeframe','not established'),('uncertainty','none')]:
                    if item.get(field)==default: item.pop(field,None)
        for chunk in chunks:
            for completed, value in recovery.process(OVERVIEW,chunk,overview_payload,validate_overview):
                overview.extend(value['items'])
                coverage.extend({'id':r['id'],'span':r.get('span',[0,len(r['text'])])} for r in completed)
        verify_coverage(sources,coverage)
        before_dedup = len(overview)
        overview[:] = deduplicate(overview)
        log.event('overview.completed', facts=len(overview), duplicates_merged=before_dedup-len(overview), covered_spans=len(coverage))
        log.block('CONSOLIDATED OVERVIEW', overview)
        packet['overview_coverage'] = coverage
        from .storage import write
        write(folder/'overview.json', {'revision':revision,'version':OVERVIEW_VERSION,'items':overview,'coverage':coverage})
        stage('retrieval')
        retrieval = EvidenceRetrievalProvider(RetrievalSettings(method=provider,e5_local_only=True,context_turns=2))
        # Retrieval model is CPU-only; release encoder before generative calls.
        for query in SEARCHES: search(query)
        for hint in overview[:3]:
            # Model navigation hints become bounded queries, not factual note support.
            query=' '.join(hint['text'].split()[:25])
            try: search(query)
            except ValueError as exc: trace.append({'query':query,'unresolved':str(exc)})
        retrieval._encoder = None
        import gc
        gc.collect()
        if mode == 'followup':
            stage('follow-up')
            seen = set()
            for round_index in range(rounds):
                hints = overview
                data = {'overview':hints,'available_source_ids':list(evidence),'round':round_index+1}
                if not runtime.fits(PLAN,data,400):
                    trace.append({'stop':'planning budget exhausted','round':round_index+1}); break
                try: reqs = requests(generate(PLAN,data,400),ids,seen,request_limit)
                except GenerationError as exc:
                    trace.append({'stop':'invalid planning response','error':str(exc)}); break
                if not reqs:
                    trace.append({'stop':'explicit completion'}); break
                log.event('planning.requests', round=round_index+1, requests=len(reqs))
                log.block('PLANNING REQUESTS', reqs)
                before = set(evidence)
                for req in reqs:
                    if req['op'] == 'search': search(req['query'])
                    else:
                        # Expand using existing context windows, respecting recording/timeline groups.
                        index = retrieval._index(sid,segments,scope or SearchScope())
                        returned = []
                        for window in index.windows:
                            if window.center.id in req['source_ids']:
                                for src in window.sources:
                                    evidence.setdefault(src.id,{'source':src.model_dump(mode='json'),'retrieval':[]})['retrieval'].append({'provider':provider,'expansion':True,'central':src.id==window.center.id})
                                    returned.append(src.id)
                        trace.append({**req,'source_ids_returned':returned})
                retrieval._encoder = None
                gc.collect()
                if set(evidence) == before:
                    trace.append({'stop':'no new evidence'}); break
            else: trace.append({'stop':'round limit'})
        else: trace.append({'stop':'predefined only'})
    # Retrieve every hinted original, not just the first three search strings.
    # This is navigation coverage, not acceptance of generated hints as facts.
    by_id = {r['id']:r for r in rows}
    positions = {r['id']:i for i,r in enumerate(rows)}
    expanded = []
    for hint in overview:
        for source_id in hint['source_ids']:
            if source_id not in by_id: raise GenerationError('Overview source scope violation')
            index = positions[source_id]
            candidates = [source_id]
            if index and (scope is None or scope.timeline_ids.get(source_id)==scope.timeline_ids.get(rows[index-1]['id'])):
                candidates.insert(0, rows[index-1]['id'])
            for source_id in candidates:
                if source_id not in evidence:
                    evidence[source_id]={'source':by_id[source_id], 'retrieval':[{'provider':'overview-navigation','central':True}]}
                    expanded.append(source_id)
    for source_id in list(evidence):
        row=by_id[source_id];index=positions[source_id]
        if index and (len(row['text'].split())<=4 or row['text'].lstrip().startswith('-')):
            previous=rows[index-1]
            if scope is None or scope.timeline_ids.get(source_id)==scope.timeline_ids.get(previous['id']):
                if previous['id'] not in evidence:
                    evidence[previous['id']]={'source':previous,'retrieval':[{'provider':'reply-context','central':False}]}
                    expanded.append(previous['id'])
    trace.insert(0,{'op':'overview-source-expansion','source_ids':expanded})
    log.event('retrieval.overview_sources', added=len(expanded), selected_total=len(evidence))
    packet['coverage_limits'].append('Retrieval evidence is selected; empty sections do not establish absence in the full conversation. Overview items are generated navigation hints.')
    log.event('evidence.saved', source_turns=len(evidence), path=str(folder/'evidence.json'))
    (folder/'evidence.json').write_text(json.dumps(packet,ensure_ascii=False,indent=2))
    # Persist the complete evidence before switching. Owned Qwen PID is waited/reaped.
    if writer == 'omi': runtime.load('omi')
    stage('drafting')
    fields, selections = draft_packet(packet,runtime,folder=folder,progress=progress)
    stage('validation')
    runtime.sample_memory()
    total=time.monotonic()-start
    log.event('workflow.completed', seconds=round(total,2), model_calls=len(runtime.metrics.get('calls',[])), sections=len(fields))
    log.block('ASSEMBLED DRAFT (pending human review)', fields)
    stage_names=list(timings)
    stage_seconds={name:(timings[stage_names[i+1]] if i+1<len(stage_names) else total)-timings[name] for i,name in enumerate(stage_names)}
    return {'version':VERSION,'revision':revision,'writer':writer,'mode':mode,'provider':provider,
            'original':fields,'edits':fields,'review_history':[],'status':'draft','packet':packet,
            'evidence_selection':selections,'metrics':runtime.metrics,'stage_started_seconds':timings,
            'stage_seconds':stage_seconds,'total_seconds':total,'validation':'Structural references checked only. Semantic support, attribution, omissions and clinical accuracy require human review.'}


def draft_packet_sections(packet,runtime,folder=None,progress=lambda s:None):
    evidence=packet['evidence']
    recovery = Recovery(runtime, folder or getattr(runtime,'folder',Path('/tmp/pshynox-draft-cache')/digest(packet)), packet.get('revision',digest(packet)), 'local-notes-v4', progress)
    log = trace_for(runtime, recovery.folder)
    fields, selections, seen = [], [], {}
    originals = sorted([v['source'] for v in evidence.values()], key=lambda r:r.get('start_ms',0))
    # Keep complete source text, roles and stable IDs; unrelated storage metadata costs context.
    originals = [{k:r[k] for k in ('id','speaker_role','text','start_ms','end_ms','revision') if k in r} for r in originals]
    prior = []
    for section in SECTIONS:
        log.stage = 'drafting '+section
        log.event('section.start', section=section)
        system = section_prompt(section)
        def payload(chunk):
            ids = {r['id'] for r in chunk}
            hints = [i for i in packet.get('overview',[]) if set(i['source_ids']) <= ids]
            return {'sources':chunk, 'extracted_hints':hints, 'already_documented':prior}
        class Budget:
            def fits(self, system, rows, output=1100):
                return runtime.fits(system,payload(rows),output)
        if packet.get('mode')=='full':
            if not runtime.fits(system,payload(originals),1100):
                raise GenerationError('Full-transcript baseline ineligible: complete section input does not fit')
            chunks=[originals]
        else:
            chunks = pack(Budget(),system,originals,output=1100)
        log.event('section.chunks_planned', section=section, chunks=len(chunks))
        items, limitations = [], []
        completed_chunks = []
        def validate_draft(value, data):
            validate_items(value,{r['id'] for r in data['sources']},typed=True)
        for chunk in chunks:
            if packet.get('mode')=='full':
                value = runtime.generate(system,payload(chunk),1100)
                validate_draft(value,payload(chunk))
                completed_chunks.append((chunk,value))
            else:
                completed_chunks.extend(recovery.process(system,chunk,payload,validate_draft,output=1100,label='drafting '+section))
        for chunk, value in completed_chunks:
            validated=value['items']
            roles={r['id']:r['speaker_role'] for r in chunk}
            for item in validated:
                kind=item['type']
                if kind not in ALLOWED_TYPES[section]:
                    log.event('item.withheld', reason='misplaced type', item_type=kind, section=section)
                    limitations.append('Withheld misplaced '+kind+' item: '+item['text'])
                    continue
                if kind in {'clinician_observation','documented_clinician_assessment','intervention_delivered_or_discussed','proposed_plan'} and not any(roles[i]=='CLINICIAN' for i in item['source_ids']):
                    log.event('item.withheld', reason='client-only source for clinician item', section=section)
                    limitations.append('Withheld client-only item in '+section+': '+item['text'])
                    continue
                if kind=='agreed_plan' and not {'CLIENT','CLINICIAN'} <= {roles[i] for i in item['source_ids']}:
                    log.event('item.withheld', reason='agreement lacks both roles', section=section)
                    limitations.append('Withheld agreement without both proposal and client response: '+item['text'])
                    continue
                key=digest({k:v for k,v in item.items() if k != 'source_ids'})
                if key in seen:
                    log.event('item.duplicate_merged', section=section)
                    seen[key]['source_ids']=list(dict.fromkeys(seen[key]['source_ids']+item['source_ids']))
                    continue
                item.update(id=digest([section,item]),review_status='pending',review_text=None,review_history=[])
                seen[key]=item
                items.append(item)
            limitations.extend(value['limitations'])
            selections.append({'section':section,'sources':[{'id':r['id'],'span':r.get('span')} for r in chunk]})
        # All prior items are used, never silently truncated; pack refuses if context is insufficient.
        prior.extend(i['text'] for i in items)
        fields.append({'section':section,'items':items,'limitations':list(dict.fromkeys(limitations)),
            'fallback':FALLBACKS.get(section,'Not documented in the supplied material.') if not items else '',
            'no_agreed_plan':section=='Plan' and not any(i['type']=='agreed_plan' for i in items)})
        log.event('section.assembled', section=section, items=len(items), limitations=len(fields[-1]['limitations']))
        log.block('ASSEMBLED SECTION '+section, fields[-1])
    return fields, selections


def draft_packet(packet, runtime, folder=None, progress=lambda s:None, *, strategy=None):
    strategy = strategy or os.getenv('NOTE_DRAFT_STRATEGY','auto')
    if strategy == 'sections':
        return draft_packet_sections(packet,runtime,folder,progress)
    if strategy != 'auto':
        raise GenerationError('NOTE_DRAFT_STRATEGY must be auto or sections')
    from .grouped import draft_grouped
    return draft_grouped(packet,runtime,folder,progress)

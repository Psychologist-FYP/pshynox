"""Versioned local drafts, separate from existing manually authored clinical notes."""
import atexit
from datetime import datetime, timezone
import json
import threading
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from services.note_generation.runtime import Runtime, GenerationError, model_path
from services.note_generation.storage import root, write, memory_slot
from services.note_generation.terminal import trace_for
from services.note_generation.workflow import run, validate_items, SECTIONS
from services.retrieval.engine import digest, SearchScope
from services.retrieval.workspace import ResearchStore

LOCK = threading.Lock()
ACTIVE = {}
SAVE_LOCK=threading.Lock()


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: Literal['clinical','research']
    session_id: str
    dataset: Literal['annomi','primock'] = 'annomi'
    patient_id: str | None = None
    writer: Literal['qwen','omi'] = 'qwen'
    mode: Literal['full','predefined','followup'] = 'followup'
    provider: Literal['bm25','e5'] = 'bm25'
    transcript_reviewed: bool = False
    replace_existing: bool = False


def snapshot(payload):
    scope = SearchScope()
    if payload.kind == 'research':
        # Existing prepared dataset only; never load blind packet, annotations or benchmark outputs.
        ds = ResearchStore().dataset(payload.dataset)
        conv = next((c for c in ds['conversations'] if c['conversation_id']==payload.session_id and c['split']=='dev'),None)
        if not conv: raise HTTPException(403,'Only development conversations are eligible')
        rows = conv['segments']
    else:
        from services.api.main import session_factory, _session_segments, ensure_no_active_transcription
        from services.api.db import SessionRecord, NoteDraftRecord, SegmentSourceRecord, MeetingChunkRecord, TranscriptSegmentRecord, TranscriptionJobRecord
        with session_factory() as db:
            session = db.get(SessionRecord,payload.session_id)
            if not session or session.patient_id != payload.patient_id:
                raise HTTPException(404,'Session not found for selected patient')
            consent = json.loads(session.consent_json)
            if consent.get('independent_final_reserved'):
                raise HTTPException(403, 'This conversation is reserved for independent blind human annotation. Use another AnnoMI demo to test AI notes.')
            note = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id==session.id))
            if note and note.workflow_status != 'DRAFT': raise HTTPException(409,'Decided sessions are read-only')
            active = db.scalar(select(TranscriptionJobRecord).where(TranscriptionJobRecord.session_id==session.id,TranscriptionJobRecord.status.in_(['QUEUED','RUNNING'])))
            if active: raise HTTPException(409,'Wait for transcription and review the completed transcript')
            ensure_no_active_transcription(db,session.id)
            rows = [r.model_dump(mode='json') for r in _session_segments(db,session.id)]
            sources = db.execute(select(SegmentSourceRecord.segment_id,SegmentSourceRecord.recording_id,MeetingChunkRecord.meeting_id)
                .join(TranscriptSegmentRecord,TranscriptSegmentRecord.id==SegmentSourceRecord.segment_id)
                .outerjoin(MeetingChunkRecord,MeetingChunkRecord.recording_id==SegmentSourceRecord.recording_id)
                .where(TranscriptSegmentRecord.session_id==session.id)).all()
            scope = SearchScope(patient_id=session.patient_id,recording_ids={s:r for s,r,_ in sources},
                timeline_ids={s:f'meeting:{m}' if m else f'recording:{r}' for s,r,m in sources})
    if not rows: raise HTTPException(422,'Transcript is empty')
    return {'session_id':payload.session_id,'revision':digest(rows),'segments':rows},scope


def job_path(jid):
    if not jid.isalnum() or len(jid)!=32: raise HTTPException(404,'Unknown generation')
    return root()/jid


def load(jid):
    path = job_path(jid)/'job.json'
    if not path.exists(): raise HTTPException(404,'Unknown generation')
    return json.loads(path.read_text())


def shutdown_notes():
    for event, runtime in list(ACTIVE.values()):
        event.set()
        if runtime.process and runtime.process.poll() is None:
            runtime.process.terminate()


atexit.register(shutdown_notes)


def router():
    api = APIRouter(prefix='/api/v1/note-generation',tags=['local drafts'])

    @api.get('/health')
    def health():
        import os
        return {'qwen_file_exists':model_path('qwen').is_file(),'omi_file_exists':model_path('omi').is_file(),
                'context':int(os.getenv('NOTE_CONTEXT','4096')),'gpu_layers':int(os.getenv('NOTE_GPU_LAYERS','0')),
                'active':bool(ACTIVE),'policy':'Local English drafts; explicit human review required'}

    @api.get('/development')
    def development():
        out=[]
        for name in ['annomi','primock']:
            ds=ResearchStore().dataset(name)
            for c in ds['conversations']:
                if c['split']=='dev' and c['conversation_id'] in ds['selection']['pilot_ids']:
                    out.append({'dataset':name,'id':c['conversation_id'],'hash':digest(c['segments'])})
        return out

    @api.get('/development/{dataset}/{cid}')
    def development_transcript(dataset:Literal['annomi','primock'],cid:str):
        snap,_=snapshot(Start(kind='research',dataset=dataset,session_id=cid))
        return snap

    @api.post('/jobs',status_code=202)
    def start(payload:Start):
        if not payload.transcript_reviewed: raise HTTPException(422,'Explicit transcript review acknowledgement required')
        snap,scope=snapshot(payload)
        target_token = None
        if payload.kind == 'clinical':
            from services.api.main import session_factory, get_note_draft
            from services.api.db import NoteDraftRecord
            from services.note_generation.composer import note_token
            with session_factory() as db:
                get_note_draft(payload.session_id, db)
                note = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == payload.session_id))
                if any(f['text'].strip() for f in json.loads(note.fields_json)) and not payload.replace_existing:
                    raise HTTPException(409, 'Confirm replacement of the existing saved draft before generating')
                target_token = note_token(note)
        if not LOCK.acquire(False): raise HTTPException(409,'One generation job is already running')
        jid=uuid.uuid4().hex
        folder=job_path(jid)
        job={'id':jid,'request':payload.model_dump(),'snapshot':snap,'stage':'queued','status':'running', 'target_note_token':target_token,
             'created_at':datetime.now(timezone.utc).isoformat()}
        cancel=threading.Event()
        try:
            runtime=Runtime(folder,cancel)
        except (GenerationError, ValueError) as exc:
            LOCK.release()
            raise HTTPException(422, str(exc)) from exc
        write(folder/'job.json',job)
        trace_for(runtime,folder)
        ACTIVE[jid]=(cancel,runtime)
        runtime.trace.event('job.queued', turns=len(snap['segments']), mode=payload.mode, writer=payload.writer)
        def worker():
            try:
                runtime.trace.event('memory.waiting')
                with memory_slot(cancel):
                    runtime.trace.event('memory.acquired')
                    def progress(stage):
                        job['stage']=stage
                        write(folder/'job.json',job)
                    result=run(snap,runtime,folder,writer=payload.writer,mode=payload.mode,provider=payload.provider,scope=scope,progress=progress)
                    latest,_=snapshot(payload)
                    if latest['revision']!=snap['revision']: raise GenerationError('Transcript changed during generation; result withheld')
                    runtime.check()
                    write(folder/'draft.json',result)
                    runtime.trace.event('draft.saved', path=str(folder/'draft.json'))
                    # Complete the note even if the user navigates away during inference.
                    if payload.kind == 'clinical':
                        from services.api.main import session_factory
                        from services.note_generation.composer import apply_to_note
                        with SAVE_LOCK:
                            try:
                                with session_factory() as db:
                                    apply_to_note(db, {**job, 'draft':result})
                                runtime.trace.event('note.applied', human_review_required=True)
                                job['applied_at'] = datetime.now(timezone.utc).isoformat()
                            except HTTPException as exc:
                                runtime.trace.event('note.application_conflict', reason=exc.detail)
                                job['application_error'] = exc.detail
                    job['status']='completed'
                    runtime.trace.event('job.completed')
            except Exception as exc:
                runtime.trace.event('job.failed', stage=job['stage'], error=str(exc), cancelled=cancel.is_set())
                job.update(status='cancelled' if cancel.is_set() else 'failed',error=str(exc))
            finally:
                runtime.close()
                job['metrics']=runtime.metrics
                write(folder/'job.json',job)
                ACTIVE.pop(jid,None)
                LOCK.release()
        threading.Thread(target=worker,daemon=True).start()
        return {'id':jid}

    @api.get('/jobs')
    def history(kind:str,session_id:str,dataset:str='annomi',patient_id:str|None=None):
        result=[]
        for p in sorted(root().glob('*/job.json'), key=lambda p:p.stat().st_mtime_ns, reverse=True):
            j=json.loads(p.read_text()); req=j['request']
            if req['kind']==kind and req['session_id']==session_id and (kind!='research' or req['dataset']==dataset) and (kind!='clinical' or req['patient_id']==patient_id):
                result.append({'id':j['id'],'status':j['status'],'stage':j['stage']})
        return result

    @api.get('/jobs/{jid}')
    def detail(jid:str):
        j=load(jid)
        try:
            current,_=snapshot(Start(**j['request']))
            j['stale']=current['revision']!=j['snapshot']['revision']
        except HTTPException: j['stale']=True
        path=job_path(jid)/'draft.json'
        j['draft']=json.loads(path.read_text()) if path.exists() else None
        if j['status']=='running' and jid not in ACTIVE:
            # The worker can finish while snapshot validation is reading the DB.
            latest=load(jid)
            if latest['status']!='running':
                j.update(latest)
            else:
                j.update(status='interrupted',error='API restarted; start a new version')
        j['diagnostics'] = {}
        for name in ('overview-recovery', 'drafting-recovery', 'request-metrics', 'draft-aliases'):
            diagnostic_path = job_path(jid)/(name+'.json')
            if diagnostic_path.exists(): j['diagnostics'][name] = json.loads(diagnostic_path.read_text())
        j['edits_hash']=digest(j['draft']['edits']) if j['draft'] else ''
        return j

    @api.post('/jobs/{jid}/cancel')
    def cancel(jid:str):
        load(jid)
        if jid in ACTIVE:
            event, runtime=ACTIVE[jid]
            event.set()
            if runtime.process and runtime.process.poll() is None: runtime.process.terminate()
        return {'cancel_requested':True}

    @api.post('/jobs/{jid}/apply')
    def apply(jid:str):
        from services.api.main import session_factory
        from services.note_generation.composer import apply_to_note
        with SAVE_LOCK:
            j=detail(jid)
            if j['request']['kind'] != 'clinical' or j['status'] != 'completed' or not j['draft'] or j['stale']:
                raise HTTPException(409, 'Only a completed, current patient-session draft can fill the note')
            if j.get('applied_at'):
                from services.api.main import get_note_draft
                with session_factory() as db:
                    return get_note_draft(j['request']['session_id'], db)
            with session_factory() as db:
                note = apply_to_note(db,j)
            stored = load(jid)
            stored['applied_at'] = datetime.now(timezone.utc).isoformat()
            write(job_path(jid)/'job.json', stored)
            return note

    class Edit(BaseModel):
        edits:list[dict]
        actor:str=Field(min_length=1,max_length=100)
        approve:bool=False
        semantic_reviewed:bool=False
        expected_hash:str

    @api.put('/jobs/{jid}/draft')
    def edit(jid:str,payload:Edit):
        with SAVE_LOCK:
            return save_edit(jid,payload)

    def save_edit(jid,payload):
        j=detail(jid)
        if j['stale']: raise HTTPException(409,'Transcript changed or session locked; regenerate a new version')
        d=j['draft']
        if not d or d['status']=='approved': raise HTTPException(409,'Draft missing or already approved')
        if digest(d['edits'])!=payload.expected_hash: raise HTTPException(409,'Draft edited in another tab; reload')
        if [f.get('section') for f in payload.edits]!=[f.get('section') for f in d['original']]: raise HTTPException(422,'Invalid note sections')
        try:
            for f in payload.edits: validate_items(f,set(d['packet']['evidence']))
        except GenerationError as exc: raise HTTPException(422,str(exc))
        originals = {i.get('id'):i for f in d['original'] for i in f['items'] if i.get('type')=='ai_proposed_formulation'}
        incoming = {i.get('id'):i for f in payload.edits for i in f['items'] if i.get('id') in originals}
        if set(incoming)!=set(originals): raise HTTPException(422,'Retain original formulations; use explicit rejection to exclude one')
        for iid,item in incoming.items():
            original=originals[iid]
            if item.get('type')!='ai_proposed_formulation' or item.get('source_ids')!=original['source_ids']:
                raise HTTPException(422,'Formulation type and original sources must be retained')
            state=item.get('review_status','pending')
            if state not in {'pending','accepted','edited','rejected'}: raise HTTPException(422,'Invalid formulation review action')
            if payload.approve and state=='pending': raise HTTPException(422,'Accept, revise or reject each proposed formulation before approving')
            previous=next(i for f in d['edits'] for i in f['items'] if i.get('id')==iid)
            history=list(previous.get('review_history',[]))
            if state!='pending' and (state!=previous.get('review_status') or item['text']!=previous['text']):
                if state!='rejected': state='accepted' if item['text']==original['text'] else 'edited'
                history.append({'actor':payload.actor,'at':datetime.now(timezone.utc).isoformat(),'action':state,'text':item['text']})
            item.update(review_status=state,review_history=history,review_text=item['text'] if state in {'accepted','edited'} else None)
        if payload.approve and not payload.semantic_reviewed: raise HTTPException(422,'Explicit semantic/source and omission review required')
        d['edits']=payload.edits
        d['review_history'].append({'actor':payload.actor,'at':datetime.now(timezone.utc).isoformat(),'action':'approved' if payload.approve else 'saved','edits':payload.edits})
        if payload.approve: d['status']='approved'
        write(job_path(jid)/'draft.json',d)
        return detail(jid)
    return api

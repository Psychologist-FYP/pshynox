import { useEffect, useRef, useState } from 'react';
import type { NoteDraft } from '../../lib/api';
import './notes.css';

type Item = {text:string; source_ids:string[]; status:string; type?:string; review_status?:string; basis?:string; missing_information?:string[]};
type Section = {section:string; items:Item[]; limitations:string[]; fallback?:string; evidence_status?:'documented'|'not_documented'|'insufficient_evidence'};
type Job = {diagnostics?:unknown;application_error?:string;applied_at?:string;id:string; status:string; stage:string; stale:boolean; error?:string; metrics?:{backend?:string}; edits_hash:string; snapshot:{segments:{id:string;text:string;speaker_role:string;start_ms:number}[]}; draft?:{edits:Section[];status:string;validation:string;packet:{coverage_limits:string[]; trace:unknown[]}}};
async function call(path:string, body?:unknown, method='POST') {
  const r=await fetch('/api/v1/note-generation'+path,body===undefined?{}:{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const v=await r.json(); if(!r.ok) throw Error(typeof v.detail==='string'?v.detail:JSON.stringify(v)); return v;
}
type Props = {sessionId:string;patientId?:string|null;dataset?:string;transcriptRevision?:string;onSource?:(id:string)=>void;onApplied?:(draft:NoteDraft)=>void;onRunning?:(running:boolean)=>void;disabled?:boolean;hasContent?:boolean};
export function LocalDraftPanel({sessionId,patientId=null,dataset,transcriptRevision,onSource,onApplied,onRunning,disabled=false,hasContent=false}:Props) {
  const [job,setJob]=useState<Job|null>(null), [history,setHistory]=useState<{id:string;status:string}[]>([]);
  const [fields,setFields]=useState<Section[]>([]),[mode,setMode]=useState('followup');
  const [reviewed,setReviewed]=useState(false),[semantic,setSemantic]=useState(false),[actor,setActor]=useState(''),[error,setError]=useState('');
  const [busy,setBusy]=useState(false),[dirty,setDirty]=useState(false),[message,setMessage]=useState('');
  const [transcript,setTranscript]=useState<{id:string;text:string;speaker_role:string}[]>([]);
  const [provider,setProvider]=useState('bm25'),[source,setSource]=useState('');
  const autoApply=useRef<string|null>(null);
  const applied=useRef(new Set<string>());
  const previousRevision=useRef(transcriptRevision);
  const running=job?.status==='running';
  const locked=job?.draft?.status==='approved'||job?.stale||busy;
  const request={kind:dataset?'research':'clinical',session_id:sessionId,patient_id:patientId,dataset:dataset||'annomi',writer:'qwen',mode,provider,transcript_reviewed:reviewed};
  async function refreshHistory(){setHistory(await call('/jobs?'+new URLSearchParams({kind:request.kind,session_id:sessionId,dataset:dataset||'annomi',...(patientId?{patient_id:patientId}:{})})));}
  async function open(id:string){const j=await call('/jobs/'+id);setJob(j);setFields(j.draft?.edits||[]);setSemantic(false);setDirty(false);setSource('');return j;}
  async function apply(j:Job) {
    const note=await call('/jobs/'+j.id+'/apply',{});
    applied.current.add(j.id);onApplied?.(note);setMessage('Generated text saved in the structured draft below. Review each section before approval.');
  }
  async function receive(j:Job) {
    setJob(j);setFields(j.draft?.edits||[]);
    if(j.status!=='running') {
      await refreshHistory();
      if(j.status==='completed'&&autoApply.current===j.id) {autoApply.current=null;if(j.application_error)setError(j.application_error);else await apply(j);}
    }
  }
  useEffect(()=>{
    let live=true;
    if(dataset)call('/development/'+dataset+'/'+sessionId).then(v=>{if(live)setTranscript(v.segments);}).catch(e=>{if(live)setError(e.message);});
    call('/jobs?'+new URLSearchParams({kind:dataset?'research':'clinical',session_id:sessionId,dataset:dataset||'annomi',...(patientId?{patient_id:patientId}:{})}))
      .then(async rows=>{if(!live)return;setHistory(rows);if(rows.length){const j=await call('/jobs/'+rows[0].id);if(live){setJob(j);setFields(j.draft?.edits||[]);}}}).catch(e=>{if(live)setError(e.message);});
    return()=>{live=false;};
  },[sessionId,dataset,patientId]);
  useEffect(()=>{onRunning?.(!!running);},[running,onRunning]);
  useEffect(()=>{
    if(!running||!job)return;
    let live=true;
    const timer=setTimeout(()=>{call('/jobs/'+job.id).then(j=>{if(live)return receive(j);}).catch(e=>{if(live)setError(e.message);});},1500);
    return()=>{live=false;clearTimeout(timer);};
  },[job]);
  useEffect(()=>{
    if(previousRevision.current===transcriptRevision)return;
    previousRevision.current=transcriptRevision;setReviewed(false);
    if(job)call('/jobs/'+job.id).then(setJob).catch(e=>setError(e.message));
  },[transcriptRevision]);
  async function action(fn:()=>Promise<void>){if(busy)return;setBusy(true);setError('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  const editor=<>
    {fields.map((f,fi)=><fieldset key={f.section} className="ai-section"><legend>{f.section}</legend>
      {!f.items.length&&<p className="small-note">{f.evidence_status==='insufficient_evidence'?'Insufficient source evidence to establish this section.':f.fallback||'Not documented in the selected evidence.'}</p>}
      {f.items.map((item,ii)=><div key={ii}>{item.type&&<p className="small-note">{item.type==='ai_proposed_formulation'?'AI-proposed formulation — '+(item.review_status||'pending'):item.type.replaceAll('_',' ')}</p>}{item.basis&&<p>Basis: {item.basis}</p>}<textarea readOnly={!dataset || !!locked} aria-label={`${f.section} item ${ii+1}`} value={item.text} onChange={e=>{setDirty(true);setSemantic(false);setFields(fields.map((v,k)=>k===fi?{...v,items:v.items.map((it,n)=>n===ii?{...it,text:e.target.value,...(it.type==='ai_proposed_formulation'?{review_status:'pending'}:{})}:it)}:v));}}/>
      {dataset&&item.type==='ai_proposed_formulation'&&<div className="note-controls"><span>Review: {item.review_status||'pending'}</span>{['accepted','rejected'].map(status=><button key={status} disabled={!!locked} onClick={()=>{setDirty(true);setSemantic(false);setFields(fields.map((v,k)=>k===fi?{...v,items:v.items.map((it,n)=>n===ii?{...it,review_status:status}:it)}:v));}}>{status==='accepted'?'Accept formulation / revision':'Reject formulation'}</button>)}</div>}
      <div className="note-citations">{item.source_ids.map(id=><button className="text-button" key={id} onClick={()=>{setSource(id);onSource?.(id);}}>{id}</button>)}</div></div>)}
      {!!f.limitations.length&&<details><summary>Evidence limitations</summary>{f.limitations.map((l,i)=><p key={i}>{l}</p>)}</details>}
    </fieldset>)}
    {source&&<aside role="region" aria-label="Original exchange" className="note-source"><h4>Original exchange</h4>{(()=>{const rows=job?.snapshot.segments||[];const i=rows.findIndex(s=>s.id===source);return i<0?null:rows.slice(Math.max(0,i-2),i+3).map(s=><p key={s.id}><strong>{s.id} · {s.speaker_role}</strong> {s.text}</p>);})()}</aside>}
    {job?.draft&&<><details><summary>Generation details and coverage</summary><p>{job.draft.validation}</p>{job.draft.packet.coverage_limits.map((l,i)=><p key={i}>{l}</p>)}<pre>{JSON.stringify(job.draft.packet.trace,null,2)}</pre><p>{job.metrics?.backend}</p></details>
    {dataset&&<div className="note-review"><input aria-label="Draft reviewer" placeholder="Reviewer name" value={actor} onChange={e=>setActor(e.target.value)}/><label className="confirm-line"><input type="checkbox" checked={semantic} onChange={e=>setSemantic(e.target.checked)}/> I checked citations, attribution, negation, timeframes, contradictions and omissions against the full transcript</label><div className="note-controls"><button disabled={!!locked||!actor.trim()} onClick={()=>action(async()=>{await call('/jobs/'+job.id+'/draft',{edits:fields,actor,expected_hash:job.edits_hash},'PUT');await open(job.id);})}>Save human edits</button><button disabled={!!locked||!actor.trim()||!semantic} onClick={()=>action(async()=>{await call('/jobs/'+job.id+'/draft',{edits:fields,actor,expected_hash:job.edits_hash,approve:true,semantic_reviewed:semantic},'PUT');await open(job.id);})}>Explicitly approve this version</button></div></div>}
    <button className="text-button" onClick={()=>{const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(job,null,2)],{type:'application/json'}));a.download='local-draft-review.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}}>Download saved evidence JSON</button></>}
  </>;
  return <section className="panel local-draft-panel"><div className="panel-heading"><div><p className="eyebrow">AI assistance</p><h3>Generate a note</h3></div><span className="small-note">Qwen · local processing</span></div>
    <p>{dataset?'Generate a research draft from this conversation.':'Generation updates AI draft sections below. Human-written sections and accepted formulations are preserved; the full new version remains available for comparison.'} Review is required before approval.</p>
    {dataset&&<details><summary>Full development transcript — inspect before generating</summary><div className="transcript-scroll">{transcript.map(s=><p key={s.id}><strong>{s.id} · {s.speaker_role}</strong> {s.text}</p>)}</div></details>}
    <label className="confirm-line"><input type="checkbox" checked={reviewed} onChange={e=>setReviewed(e.target.checked)}/> I checked the transcript text and speaker roles for this generation</label>
    <div className="note-controls"><button className="button button-primary" disabled={disabled||!reviewed||running||busy} onClick={()=>action(async()=>{
      if(dirty&&!window.confirm('Discard unsaved edits to this generated version?'))return;
      if(hasContent&&!window.confirm('Generate a new AI draft version? Human-written sections and accepted formulations will be preserved. Previous AI draft fields remain in the audit history.'))return;
      setMessage('');const j=await call('/jobs',{...request,replace_existing:hasContent});autoApply.current=dataset?null:j.id;setDirty(false);await receive(await call('/jobs/'+j.id));await refreshHistory();
    })}>{running?'Generating…':job&&['failed','cancelled','interrupted'].includes(job.status)?'Retry / resume saved work':'Generate new draft version'}</button>
    {running&&<button disabled={busy} onClick={()=>action(async()=>{await call('/jobs/'+job.id+'/cancel',{});setMessage('Cancellation requested…');})}>Cancel generation</button>}
    {!!history.length&&<label>Saved versions <select disabled={running||busy} value={job?.id||''} onChange={e=>{const id=e.target.value;if(id&&(!dirty||window.confirm('Discard unsaved edits?')))void action(async()=>{await open(id);setMessage('');});}}><option value="">Choose version</option>{history.map(h=><option key={h.id} value={h.id}>{h.id.slice(0,8)} · {h.status}</option>)}</select></label>}</div>
    {disabled&&<p className="small-note">Save pending note edits first. Decided notes cannot be regenerated.</p>}
    <details><summary>Generation settings</summary><div className="note-controls"><label>Evidence collection <select aria-label="Retrieval mode" disabled={running} value={mode} onChange={e=>setMode(e.target.value)}><option value="predefined">Standard</option><option value="followup">Extended follow-up searches</option>{dataset&&<option value="full">Full transcript (must fit)</option>}</select></label><label>Retrieval <select disabled={running} value={provider} onChange={e=>setProvider(e.target.value)}><option value="bm25">BM25</option><option value="e5">E5 (local weights)</option></select></label></div></details>
    {job&&<p role="status">{job.status} · {job.stage}{job.stale&&' · Source changed — regenerate'}{job.draft?.status==='approved'&&' · explicitly approved'}</p>}
    {job?.diagnostics!=null&&<details><summary>Technical generation diagnostics</summary><pre>{JSON.stringify(job.diagnostics,null,2)}</pre></details>}
    {job?.metrics?.backend&&<p className="small-note">Last run: {job.metrics.backend}</p>}
    {message&&<p className="save-message">{message}</p>}{(error||job?.error||job?.application_error)&&<p role="alert" className="form-error">{error||job?.error||job?.application_error}</p>}
    {job?.draft&&!dataset&&<><details><summary>Generated version and source evidence</summary><p>Edit the structured note below; this version is retained for comparison.</p><div className="generated-readonly">{editor}</div></details>
      {!job.applied_at&&!applied.current.has(job.id)&&<button disabled={disabled||busy||!!job.stale} onClick={()=>action(()=>apply(job))}>Fill structured draft from this version</button>}</>}
    {dataset&&editor}
  </section>;
}
export function ResearchNotesPage(){
 const [choices,setChoices]=useState<{dataset:string;id:string;hash:string}[]>([]),[selected,setSelected]=useState(''),[error,setError]=useState('');
 useEffect(()=>{call('/development').then(setChoices).catch(e=>setError(e.message));},[]);
 const c=choices.find(v=>v.id===selected);
 return <main className="section-wrap"><h2>Development research note generation</h2><p>Research only. No clinical records are created.</p><a href="/workspace/research">Research workspace</a><label>Conversation <select aria-label="Development conversation" value={selected} onChange={e=>setSelected(e.target.value)}><option value="">Choose a development conversation</option>{choices.map(c=><option key={c.id} value={c.id}>{c.dataset} · {c.id}</option>)}</select></label>{error&&<p role="alert">{error}</p>}{c&&<LocalDraftPanel key={c.id} sessionId={c.id} dataset={c.dataset}/>}</main>;
}

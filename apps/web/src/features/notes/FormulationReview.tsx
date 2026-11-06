import { useState } from 'react';
import { request, type GeneratedNoteItem, type NoteDraft, type TranscriptSegment } from '../../lib/api';

export function FormulationReview({note,transcript,disabled,onReviewed}:{note:NoteDraft;transcript:TranscriptSegment[];disabled:boolean;onReviewed:(note:NoteDraft)=>void}) {
 const items=note.fields.flatMap(f=>f.generated_items||[]).filter(i=>i.type==='ai_proposed_formulation');
 const [actor,setActor]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 if(!items.length)return null;
 async function review(item:GeneratedNoteItem,action:'accept'|'edit'|'reject',text:string){
  setBusy(true);setError('');
  try{onReviewed(await request<NoteDraft>(`/api/v1/sessions/${note.session_id}/note-draft/formulations/${item.id}`,undefined,{method:'POST',body:JSON.stringify({actor,action,text,expected_updated_at:note.updated_at})}));}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <section className="panel formulation-review"><h3>AI-proposed formulation · clinician review</h3>
 <p>These interpretations are separate from the recorded assessment until you accept or revise them. Saving the note does not accept them.</p>
 <label>Formulation reviewer <input aria-label="Formulation reviewer" value={actor} onChange={e=>setActor(e.target.value)} /></label>
 {items.map(item=><Proposal key={item.id+item.review_status} item={item} transcript={transcript} disabled={disabled||busy||!actor.trim()||note.sources_stale||note.workflow_status!=='DRAFT'} onReview={(action,text)=>review(item,action,text)}/>)}
 {disabled&&<p>Save pending note edits before reviewing a formulation.</p>}{note.sources_stale&&<p>Source text changed. Review and confirm the changed sources first.</p>}
 {error&&<p role="alert" className="form-error">{error}</p>}</section>;
}
function Proposal({item,transcript,disabled,onReview}:{item:GeneratedNoteItem;transcript:TranscriptSegment[];disabled:boolean;onReview:(action:'accept'|'edit'|'reject',text:string)=>void}){
 const [text,setText]=useState(item.text);
 return <article className="formulation-proposal"><strong>AI interpretation · {item.review_status}</strong>
 {item.review_status==='pending'?<><textarea aria-label="Proposed formulation" value={text} onChange={e=>setText(e.target.value)} disabled={disabled}/><div className="note-controls">
 <button disabled={disabled||text!==item.text} onClick={()=>onReview('accept',item.text)}>Accept formulation</button>
 <button disabled={disabled||!text.trim()||text===item.text} onClick={()=>onReview('edit',text)}>Save revised formulation</button>
 <button disabled={disabled} onClick={()=>onReview('reject','')}>Reject formulation</button></div></>:<p>{item.review_text||'Rejected — excluded from the note.'}</p>}
 <p><strong>Basis:</strong> {item.basis}</p>{!!item.missing_information.length&&<p><strong>Missing information:</strong> {item.missing_information.join('; ')}</p>}
 <details><summary>Supporting evidence and original interpretation</summary><p>Original AI proposal: {item.text}</p>
 <p>{item.attribution} · {item.timeframe} · {item.uncertainty}</p>
 {item.source_ids.map(id=>{const index=transcript.findIndex(s=>s.id===id);return <div key={id}><strong>{id}</strong>{index<0?<p>Source unavailable in the current transcript.</p>:transcript.slice(Math.max(0,index-1),index+2).map(s=><p key={s.id}><strong>{s.id===id?'Evidence':'Context'} · {s.speaker_role}</strong> {s.text}</p>)}</div>;})}
 {item.review_history.map((h,i)=><p key={i}>{h.action} by {h.actor} · {new Date(h.at).toLocaleString()}</p>)}</details></article>;
}

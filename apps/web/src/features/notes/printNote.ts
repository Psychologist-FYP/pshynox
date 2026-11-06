import type { NoteDraft, Session } from '../../lib/api';

/** Print only the saved note; textContent prevents transcript text becoming HTML. */
export function printNote(note: NoteDraft, session: Session, patientName: string) {
  const popup=window.open('', '_blank');
  if(!popup) throw new Error('Allow pop-ups for this application to open the print preview.');
  const doc=popup.document;
  doc.title=`Psychology progress note · ${session.id}`;
  const style=doc.createElement('style');
  style.textContent='body{font:12pt Georgia,serif;line-height:1.55;color:#17221d;max-width:800px;margin:32px auto;padding:24px}h1{font-size:22pt}h2{font-size:14pt;border-bottom:1px solid #bbb;padding-bottom:5px;break-after:avoid}p{white-space:pre-wrap;overflow-wrap:anywhere}section{margin:24px 0}header{border-bottom:2px solid #555}.status{font:bold 11pt sans-serif;padding:12px;background:#eee}button{padding:12px 20px;cursor:pointer}footer{margin-top:40px;border-top:1px solid #aaa;font-size:10pt}@page{size:A4;margin:18mm}@media print{body{margin:0;padding:0;max-width:none}button{display:none}}';
  doc.head.append(style);
  function add(parent:HTMLElement,tag:string,text:string){const el=doc.createElement(tag);el.textContent=text;parent.append(el);return el;}
  const header=doc.createElement('header');doc.body.append(header);
  add(header,'h1','Psychology progress note');
  add(header,'p',`${patientName}\nSession: ${session.id}\nRecord created: ${new Date(session.created_at).toLocaleString()}\nNote saved: ${new Date(note.updated_at).toLocaleString()}`);
  const status=add(header,'p',`${session.consent.research_demo?'DEMO / RESEARCH RECORD · ':''}${note.workflow_status==='DRAFT'?'DRAFT — NOT APPROVED':note.workflow_status}${note.sources_stale?' · SOURCE CHANGES REQUIRE REVIEW':''}`);status.className='status';
  add(header,'p','SOAP format with a functioning / progress supplement. This document reflects the saved note.');
  const print=add(doc.body,'button','Print / Save as PDF');print.onclick=()=>popup.print();
  const sections=[['subjective','S — Subjective'],['objective','O — Objective'],['assessment','A — Assessment'],['interventions','Interventions'],['plan','P — Plan'],['functioning',note.fields.some(f=>f.section==='progress')?'Functioning':'Functioning and progress'],['progress','Progress']];
  for(const [key,title] of sections){const field=note.fields.find(f=>f.section===key);if(!field&&(key==='progress'||key==='interventions'))continue;const section=doc.createElement('section');doc.body.append(section);add(section,'h2',title);add(section,'p',field?.text.trim()||'Not documented.');
    for(const item of field?.generated_items||[]){if(item.type==='ai_proposed_formulation'&&item.review_status==='pending'){add(section,'h3','AI-proposed formulation — NOT ACCEPTED');add(section,'p',item.text);add(section,'p','Basis: '+item.basis);add(section,'p','Evidence: '+item.source_ids.join(', '));if(item.missing_information.length)add(section,'p','Missing information: '+item.missing_information.join('; '));}}
  }
  const footer=doc.createElement('footer');doc.body.append(footer);
  add(footer,'p',note.decision_actor_name?`Decision recorded by: ${note.decision_actor_name}\nDecision: ${note.workflow_status}`:'Clinician name / signature: __________________________\nDate: __________________');
  if(note.fields.some(f=>f.provenance==='AI_GENERATED'||f.generated_items?.length))add(footer,'p',note.workflow_status==='APPROVED'?'Prepared with AI assistance and explicitly approved as shown above.':'AI-assisted draft material — not approved. Pending interpretations are labelled separately.');
  popup.focus();
}

import {test,expect} from '@playwright/test';
test('mocked local draft generation, source exchange and explicit review',async({page})=>{
 const rows=[{id:'t1',text:'Have you slept well?',speaker_role:'CLINICIAN',start_ms:0},{id:'t2',text:'No, I sleep four hours.',speaker_role:'CLIENT',start_ms:1000}];
 const fields=[{section:'Subjective',items:[{text:'Client reported sleeping four hours.',status:'supported',source_ids:['t2']}],limitations:['Human review pending']}];
 const job={id:'a'.repeat(32),status:'completed',stage:'validation',stale:false,edits_hash:'mock',snapshot:{segments:rows},draft:{edits:fields,status:'draft',validation:'Structural checks only',packet:{coverage_limits:['Selected evidence'],trace:[{stop:'round limit'}]}}};
 await page.route('**/api/v1/note-generation/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path.endsWith('/development'))return route.fulfill({json:[{dataset:'annomi',id:'fictional-dev',hash:'mock'}]});
  if(path.endsWith('/development/annomi/fictional-dev'))return route.fulfill({json:{segments:rows}});
  if(path.endsWith('/jobs')&&route.request().method()==='POST')return route.fulfill({json:{id:job.id}});
  if(path.endsWith('/jobs'))return route.fulfill({json:[]});
  if(path.endsWith('/draft')){const data=route.request().postDataJSON();job.draft.edits=data.edits;job.draft.status=data.approve?'approved':'draft';}
  return route.fulfill({json:job});
 });
 await page.goto('/workspace/research-notes');await page.getByLabel('Development conversation').selectOption('fictional-dev');
 const generate=page.getByRole('button',{name:'Generate new draft version'});await expect(generate).toBeDisabled();
 await page.getByText('Full development transcript').click();await expect(page.getByText(rows[0].text,{exact:false})).toBeVisible();
 await page.getByLabel('I checked the transcript').check();await generate.click();
 await page.getByRole('button',{name:'t2',exact:true}).click();await expect(page.getByRole('region',{name:'Original exchange'})).toContainText(rows[0].text);
 await page.getByLabel('Subjective item 1').fill('Client reported four hours of sleep.');await page.getByLabel('Draft reviewer').fill('Fictional reviewer');
 await expect(page.getByRole('button',{name:'Explicitly approve this version'})).toBeDisabled();
 await page.getByRole('button',{name:'Save human edits'}).click();await expect(page.getByRole('status')).not.toContainText('explicitly approved');
 await page.getByLabel('I checked citations').check();await page.getByRole('button',{name:'Explicitly approve this version'}).click();await expect(page.getByRole('status')).toContainText('explicitly approved');
});

test('patient generation fills the structured note, preserves edits and prints SOAP fields', async({page})=>{
 const patient=await (await page.request.post('/api/v1/patients',{data:{name:'Fictional note test'}})).json();
 const session=await (await page.request.post('/api/v1/sessions',{data:{patient_id:patient.id,session_type:'FOLLOW_UP',consent:{research_demo:true}}})).json();
 let saved=await (await page.request.get(`/api/v1/sessions/${session.id}/note-draft`)).json();
 const rows=[{id:'demo-t1',session_id:session.id,start_ms:0,end_ms:1000,revision:1,text:'I have had trouble sleeping for two weeks.',speaker_role:'CLIENT'}];
 await page.route(`**/sessions/${session.id}/transcript`,route=>route.fulfill({json:rows}));
 const generated=['Subjective','Objective','Evaluation notes','Progress','Interventions and plan'].map(section=>({section,items:section==='Subjective'?[{text:'Client reported poor sleep for two weeks.',source_ids:['demo-t1'],status:'supported'}]:[],limitations:[]}));
 const job={id:'b'.repeat(32),status:'running',stage:'drafting',stale:false,edits_hash:'mock',snapshot:{segments:rows},draft:null as null|{edits:typeof generated;status:string;validation:string;packet:{coverage_limits:string[];trace:unknown[]}}};
 let started=false,applyCount=0,polls=0;
 await page.route('**/api/v1/note-generation/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path.endsWith('/jobs')&&route.request().method()==='POST'){started=true;return route.fulfill({json:{id:job.id}});}
  if(path.endsWith('/jobs'))return route.fulfill({json:started?[{id:job.id,status:job.status}]:[]});
  if(path.endsWith('/apply')){
   applyCount++;
   const fields=saved.fields.map((f:{section:string})=>({...f,text:f.section==='subjective'?'Client reported poor sleep for two weeks.':'',provenance:'AI_GENERATED',evidence_ids:[],review_status:'PENDING'}));
   saved=await(await page.request.put(`/api/v1/sessions/${session.id}/note-draft`,{data:{fields}})).json();
   return route.fulfill({json:saved});
  }
  polls++;
  if(polls>1){job.status='completed';job.stage='validation';job.draft={edits:generated,status:'draft',validation:'Structural checks only',packet:{coverage_limits:[],trace:[]}};}
  return route.fulfill({json:job});
 });
 await page.goto(`/workspace/sessions/${session.id}`);
 await page.getByLabel('I checked the transcript text').check();
 await page.getByRole('button',{name:'Generate new draft version'}).click();
 const composer=page.locator('.note-composer');
 await expect(composer.getByLabel('Subjective', {exact:false})).toHaveValue('Client reported poor sleep for two weeks.');
 await expect(composer.getByLabel('Objective', {exact:false})).toHaveValue('');
 expect(applyCount).toBe(1);
 await composer.getByLabel('Subjective', {exact:false}).fill('Reviewed sleep concern. <script>not executable</script>');
 await expect(page.getByRole('button',{name:'Generate new draft version'})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Print / Save as PDF'})).toBeDisabled();
 let failSave=true;
 await page.route(`**/sessions/${session.id}/note-draft`,async route=>{
  if(route.request().method()==='PUT'&&failSave){failSave=false;return route.fulfill({status:503,json:{error:{message:'Temporary save failure'}}});}
  return route.continue();
 });
 await composer.getByRole('button',{name:'Save draft',exact:true}).click();
 await expect(composer).toContainText('Temporary save failure');
 await expect(page.getByRole('button',{name:'Generate new draft version'})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Print / Save as PDF'})).toBeDisabled();
 await expect(composer.getByLabel('Subjective',{exact:false})).toHaveValue('Reviewed sleep concern. <script>not executable</script>');
 await composer.getByRole('button',{name:'Save draft',exact:true}).click();
 await expect(page.getByRole('button',{name:'Print / Save as PDF'})).toBeEnabled();
 const opened=page.waitForEvent('popup');
 await page.getByRole('button',{name:'Print / Save as PDF'}).click();
 const print=await opened;
 await expect(print.getByRole('heading',{name:'S — Subjective'})).toBeVisible();
 await expect(print.getByText('Reviewed sleep concern. <script>not executable</script>',{exact:true})).toBeVisible();
 await expect(print.locator('script')).toHaveCount(0);
 await expect(print.getByText('DEMO / RESEARCH RECORD · DRAFT — NOT APPROVED')).toBeVisible();
 await print.pdf({path:test.info().outputPath('fictional-note.pdf'),format:'A4'});
 await print.close();
 await page.reload();
 await expect(page.locator('.note-composer').getByLabel('Subjective',{exact:false})).toHaveValue('Reviewed sleep concern. <script>not executable</script>');
 expect(applyCount).toBe(1);
 await page.screenshot({path:test.info().outputPath('note-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 await expect.poll(async()=>{const b=await page.locator('.sidebar').boundingBox();return b ? Math.round(b.x+b.width) : 0;}).toBeLessThanOrEqual(1);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
 await page.screenshot({path:test.info().outputPath('note-mobile.png'),fullPage:true});
});

for(const action of ['accept','edit','reject'] as const){
 test(`AI formulation ${action} is explicit and retains original evidence`,async({page})=>{
  const patient=await(await page.request.post('/api/v1/patients',{data:{name:'Synthetic formulation review'}})).json();
  const session=await(await page.request.post('/api/v1/sessions',{data:{patient_id:patient.id,session_type:'FOLLOW_UP',consent:{research_demo:true}}})).json();
  const note=await(await page.request.get(`/api/v1/sessions/${session.id}/note-draft`)).json();
  const original='Reported travel demands may limit attendance.';
  const item={id:'f1',text:original,type:'ai_proposed_formulation',source_ids:['c1'],status:'supported',attribution:'AI interpretation',timeframe:'current',uncertainty:'tentative',basis:'Client describes travel limiting attendance.',missing_information:['Feasible schedule is not established.'],review_status:'pending',review_text:null as string|null,review_history:[] as {actor:string;action:string;at:string;text:string|null}[]};
  const assessment=note.fields.find((f:{section:string})=>f.section==='assessment');assessment.provenance='AI_GENERATED';assessment.generated_items=[item];assessment.evidence_ids=['c1'];
  await page.route(`**/sessions/${session.id}/transcript`,r=>r.fulfill({json:[{id:'c1',session_id:session.id,start_ms:0,end_ms:1000,revision:1,speaker_role:'CLIENT',text:'The bus trip makes it hard to attend.'}]}));
  await page.route(`**/sessions/${session.id}/note-draft**`,async route=>{
   if(route.request().method()==='POST'){
    const body=route.request().postDataJSON();expect(body.actor).toBe('Synthetic reviewer');expect(body.action).toBe(action);
    item.review_status=action==='edit'?'edited':action==='accept'?'accepted':'rejected';
    item.review_text=action==='reject'?null:action==='edit'?body.text:original;
    item.review_history=[{actor:body.actor,action,at:new Date().toISOString(),text:item.review_text}];
    assessment.text=item.review_text||'';
   }
   return route.fulfill({json:note});
  });
  await page.goto(`/workspace/sessions/${session.id}`);
  const panel=page.locator('.formulation-review');
  await expect(panel).toContainText('AI interpretation · pending');
  await expect(page.locator('.note-composer').getByLabel('Assessment',{exact:false})).toHaveValue('');
  const preview=page.waitForEvent('popup');await page.getByRole('button',{name:'Print / Save as PDF'}).click();const print=await preview;
  await expect(print.getByText('AI-proposed formulation — NOT ACCEPTED')).toBeVisible();await print.close();
  await panel.getByLabel('Formulation reviewer').fill('Synthetic reviewer');
  if(action==='edit')await panel.getByLabel('Proposed formulation').fill('Travel may be a practical barrier; clarify alternatives.');
  await panel.getByRole('button',{name:action==='accept'?'Accept formulation':action==='edit'?'Save revised formulation':'Reject formulation',exact:true}).click();
  await expect(panel).toContainText(`AI interpretation · ${item.review_status}`);
  await expect(page.locator('.note-composer').getByLabel('Assessment',{exact:false})).toHaveValue(item.review_text||'');
  await panel.getByText('Supporting evidence and original interpretation',{exact:true}).click();
  await expect(panel).toContainText('Original AI proposal: '+original);
  await expect(panel).toContainText('The bus trip makes it hard to attend.');
  await expect(panel).toContainText('Synthetic reviewer');
 });
}

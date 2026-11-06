"""Map generated evidence to the existing note without copying prose across fields."""
import json
from fastapi import HTTPException
from sqlalchemy import select, update
from services.api.db import NoteDraftRecord, utc_now
from services.retrieval.engine import digest

SECTION_MAP = {
    'Subjective': 'subjective', 'Objective': 'objective',
    'Evaluation notes': 'assessment', 'Assessment':'assessment', 'Progress': 'progress',
    'Interventions and plan': 'plan',
    'Functioning':'functioning', 'Interventions':'interventions', 'Plan':'plan',
}


def note_token(note):
    return digest([note.fields_json, str(note.updated_at), note.workflow_status])


def apply_to_note(db, job):
    from services.api.main import _audit, _record_to_draft
    note = db.scalar(select(NoteDraftRecord).where(NoteDraftRecord.session_id == job['request']['session_id']))
    if not note or note.workflow_status != 'DRAFT':
        raise HTTPException(409, 'An editable session note is required')
    if note_token(note) != job.get('target_note_token'):
        raise HTTPException(409, 'The note changed since generation began. Your edits are preserved; compare the generated version before copying any text.')
    fields = {f['section']: f for f in json.loads(note.fields_json)}
    preserved = []
    revisions = json.loads(note.source_revisions_json or '{}')
    for section in job['draft']['edits']:
        target = SECTION_MAP.get(section['section'])
        if section['section']=='Progress' and job['draft'].get('version') != 'local-notes-v3':
            target='functioning'  # Keep old generated versions readable/applicable.
        if not target:
            raise HTTPException(422, 'Custom generated sections cannot be mapped automatically')
        items = section['items']
        old = fields.get(target,{})
        if (old.get('provenance')=='MANUAL' and old.get('text','').strip()) or any(i.get('review_status') in {'accepted','edited'} for i in old.get('generated_items',[])):
            preserved.append(target)
            if target=='assessment':
                # Offer new interpretations for review without replacing human prose.
                known={i['id'] for i in old.get('generated_items',[])}
                additions=[i for i in items if i.get('type')=='ai_proposed_formulation' and i['id'] not in known]
                old['generated_items']=old.get('generated_items',[])+additions
                new_ids=[sid for i in additions for sid in i['source_ids'] if sid not in old.get('evidence_ids',[])]
                old['evidence_ids']=list(dict.fromkeys(old.get('evidence_ids',[])+new_ids))
                current={s['id']:s['revision'] for s in job['snapshot']['segments']}
                revisions.update({sid:current[sid] for sid in new_ids})
            continue
        text='\n'.join(i['text'] for i in items if i.get('type')!='ai_proposed_formulation')
        if not items:
            text=section.get('fallback','')
        if section.get('no_agreed_plan') and items:
            text+='\nNo specific action plan documented as agreed.'
        fields[target] = dict(section=target, text=text,
            evidence_ids=list(dict.fromkeys(s for i in items for s in i['source_ids'])),
            generated_items=[i for i in items if 'type' in i],
            provenance='AI_GENERATED', review_status='PENDING')
        current={s['id']:s['revision'] for s in job['snapshot']['segments']}
        revisions.update({sid:current[sid] for sid in fields[target]['evidence_ids']})
    old_fields = json.loads(note.fields_json)
    result = db.execute(update(NoteDraftRecord).where(NoteDraftRecord.id == note.id,
        NoteDraftRecord.updated_at == note.updated_at, NoteDraftRecord.workflow_status == 'DRAFT')
        .values(fields_json=json.dumps(list(fields.values())), source_revisions_json=json.dumps(revisions),
                sources_stale=note.sources_stale if preserved else False, updated_at=utc_now(), risk_review_status='PENDING',
                reviewer_name=None, risk_review_completed_at=None, review_note=None),
        execution_options={'synchronize_session': False})
    if result.rowcount != 1:
        db.rollback()
        raise HTTPException(409, 'The note changed in another tab; reload before continuing')
    _audit(db, note.session_id, note.id, 'AI_DRAFT_APPLIED', 'AI note generator',
        {'generation_id': job['id'], 'previous_fields': json.dumps(old_fields), 'preserved_sections':json.dumps(preserved)})
    db.commit()
    db.refresh(note)
    return _record_to_draft(note)

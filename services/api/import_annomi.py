"""Create reproducible demo patients/sessions without modifying research datasets."""
import json
from pathlib import Path
from services.api.db import PatientRecord, SessionRecord, TranscriptSegmentRecord, utc_now
from services.api.schemas import PatientInput, TranscriptSegment


def import_conversations(db, dataset, reserved_ids=()):
    counts = {'patients_created': 0, 'sessions_created': 0, 'turns_created': 0, 'sessions_skipped': 0}
    for conversation in dataset['conversations']:
        cid = conversation['conversation_id']
        sid, pid = 'demo-' + cid, 'demo-patient-' + cid
        existing = db.get(SessionRecord, sid)
        if existing:
            if cid in reserved_ids:
                consent = json.loads(existing.consent_json)
                consent['independent_final_reserved'] = True
                existing.consent_json = json.dumps(consent)
            counts['sessions_skipped'] += 1
            continue  # Never overwrite transcript edits, notes or existing patient intake.
        now = utc_now()
        if not db.get(PatientRecord, pid):
            intake = PatientInput(name=f'AnnoMI demo · {cid.removeprefix("annomi-")}', language='English',
                additional_notes=f'Research demonstration record, not an actual patient intake. Source: {cid}. '
                f'Original split: {conversation["split"]}. One record per conversation; identities across videos are unknown. '
                'No demographics, diagnosis or consent inferred. Transcript timestamps are dataset offsets; no audio imported.')
            db.add(PatientRecord(id=pid, name=intake.name, intake_json=intake.model_dump_json(), created_at=now, updated_at=now))
            db.flush()
            counts['patients_created'] += 1
        db.add(SessionRecord(id=sid, patient_id=pid, pseudonym_id=cid, session_type='FOLLOW_UP',
            consent_json=json.dumps({'recording_consent': False, 'research_demo': True,
                'dataset': 'annomi', 'source_conversation_id': cid, 'source_split': conversation['split']}),
            workflow_status='DRAFT', created_at=now))
        db.flush()
        if cid in reserved_ids:
            session = db.get(SessionRecord, sid)
            session.consent_json = json.dumps({**json.loads(session.consent_json), 'independent_final_reserved':True})
        for source in conversation['segments']:
            row = TranscriptSegment.model_validate({**source, 'id': 'demo-' + source['id'], 'session_id': sid})
            db.add(TranscriptSegmentRecord(**row.model_dump(mode='json')))
            counts['turns_created'] += 1
        counts['sessions_created'] += 1
    db.commit()
    return counts


def main():
    import sqlite3
    from datetime import datetime, timezone
    from dotenv import load_dotenv
    from services.api.db import ROOT, make_engine, make_session_factory
    load_dotenv(ROOT / '.env')
    engine = make_engine()
    if engine.dialect.name != 'sqlite':
        raise RuntimeError('This local import command requires SQLite for its automatic backup')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = ROOT / 'data/backups' / f'before-annomi-patients-{stamp}.db'
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(engine.url.database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    dataset = json.loads((ROOT / 'data/research/datasets/annomi.json').read_text())
    packet = ROOT / 'data/research/review-experiments/independent-final/packet.json'
    reserved = [c['conversation_id'] for c in json.loads(packet.read_text())['conversations']] if packet.exists() else []
    with make_session_factory(engine)() as db:
        counts = import_conversations(db, dataset, reserved)
    print(json.dumps({'backup': str(backup), **counts}, indent=2))


if __name__ == '__main__':
    main()

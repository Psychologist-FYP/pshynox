import json
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, func
from services.api.db import Base, make_session_factory, PatientRecord, SessionRecord, TranscriptSegmentRecord
from services.api.import_annomi import import_conversations


def test_demo_import_idempotent_and_preserves_edits(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'demo.db'));Base.metadata.create_all(engine)
    dataset={'conversations':[{'conversation_id':'annomi-1','split':'test','segments':[{'id':'annomi-1-turn-0','session_id':'annomi-1','text':'Source text','speaker_role':'CLIENT','start_ms':2,'end_ms':2,'revision':1}]}]}
    with make_session_factory(engine)() as db:
        counts=import_conversations(db,dataset,{'annomi-1'})
        assert counts['turns_created']==1 and counts['patients_created']==1
        session=db.get(SessionRecord,'demo-annomi-1')
        assert json.loads(session.consent_json)['recording_consent'] is False
        assert json.loads(session.consent_json)['independent_final_reserved'] is True
        turn=db.get(TranscriptSegmentRecord,'demo-annomi-1-turn-0');turn.text='Human corrected';db.commit()
        assert import_conversations(db,dataset)['sessions_skipped']==1
        assert turn.text=='Human corrected'
        assert db.scalar(select(func.count()).select_from(PatientRecord))==1
        assert dataset['conversations'][0]['segments'][0]['text']=='Source text'

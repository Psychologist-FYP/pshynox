"""Add a fictional retrieval patient to the local app without overwriting edits.

Run from the repository root: .venv/bin/python scripts/seed_retrieval_demo.py
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.api.db import (PatientRecord, SessionRecord, TranscriptSegmentRecord,
                             make_engine, make_session_factory, utc_now)
from services.api.schemas import PatientInput, TranscriptSegment

PATIENT_ID = "patient-retrieval-demo"
DEMO_MARKER = "PSHYNOX_RETRIEVAL_DEMO_V1"
SESSIONS = {
    "session-retrieval-demo-001": [
        ("CLINICIAN", "How has your sleep been?"),
        ("CLIENT", "I sleep four hours because of night shifts."),
        ("CLINICIAN", "Have you been sleeping less?"),
        ("CLIENT", "No."),
        ("CLIENT", "I don't feel anxious anymore."),
        ("CLIENT", "My sister has depression."),
        ("CLINICIAN", "How have work and studies been affected?"),
        ("CLIENT", "I missed two classes this week because I felt tired after work."),
        ("CLINICIAN", "What would you like to change?"),
        ("CLIENT", "I want to reduce evening caffeine so I can keep a steadier routine."),
        ("CLIENT", "Neend nahi aati."),
        ("CLIENT", "نیند نہیں آتی۔"),
        ("CLINICIAN", "We practised a breathing exercise and agreed to write down a bedtime routine."),
        ("CLIENT", "I will try the bedtime routine for one week."),
        ("CLINICIAN", "We agreed to follow up next week."),
        ("CLIENT", "I drink water after exercise."),
        ("CLINICIAN", "Have there been changes in mood or enjoyment?"),
        ("CLIENT", "I have felt low for two weeks and stopped enjoying cricket with my friends."),
        ("CLINICIAN", "What is worrying you?"),
        ("CLIENT", "I worry about exams most evenings and find it hard to stop worrying."),
        ("CLINICIAN", "How has your energy been?"),
        ("CLIENT", "I feel tired after night shifts. I have not had unusual bursts of energy."),
    ],
    "session-retrieval-demo-002": [
        ("CLINICIAN", "What has changed since last week?"),
        ("CLIENT", "I now sleep six hours on most nights after changing my work schedule."),
        ("CLIENT", "I went to the watercolour club on Saturday and enjoyed painting."),
        ("CLINICIAN", "Which steps will you keep?"),
        ("CLIENT", "I will keep the bedtime routine and practise the breathing exercise."),
        ("CLINICIAN", "We agreed to review progress in two weeks."),
    ],
}


def seed_demo(factory) -> dict:
    """Insert only missing, reserved demo records in one transaction.

    Existing demo text/roles/revisions/notes are intentionally preserved, so
    rerunning this command cannot erase the user's retrieval experiments.
    """
    created = []
    with factory.begin() as db:
        patient = db.get(PatientRecord, PATIENT_ID)
        if patient is not None and DEMO_MARKER not in patient.intake_json:
            raise ValueError("Reserved demo patient ID is in use by another record")
        if patient is None:
            intake = PatientInput(
                name="Retrieval Demo — Fictional Patient", preferred_name="Retrieval Demo", age=27,
                language="English, Roman Urdu, Urdu (fictional examples)",
                presenting_concerns="Fictional retrieval test: sleep, worry, mood, functioning and treatment-plan mentions.",
                goals="Test literal, Boolean, phrase and multilingual evidence searches; inspect speakers and context.",
                additional_notes=f"{DEMO_MARKER}. Entirely invented engineering test data. Not a real patient or clinical diagnosis. "
                                 "Two transcript-only sessions have synthetic timestamps and no audio. "
                                 "See docs/RETRIEVAL_DEMO.md for queries and expected results.",
            )
            db.add(PatientRecord(id=PATIENT_ID, name=intake.name, intake_json=intake.model_dump_json(),
                                 created_at=utc_now(), updated_at=utc_now()))
            db.flush()
            created.append(PATIENT_ID)
        for ordinal, (sid, turns) in enumerate(SESSIONS.items(), 1):
            existing = db.get(SessionRecord, sid)
            if existing is not None:
                if existing.patient_id != PATIENT_ID or not json.loads(existing.consent_json).get("retrieval_demo"):
                    raise ValueError(f"Reserved demo session {sid} belongs to another record")
                continue
            db.add(SessionRecord(id=sid, patient_id=PATIENT_ID, pseudonym_id=PATIENT_ID,
                                 session_type="INITIAL_ASSESSMENT" if ordinal == 1 else "FOLLOW_UP",
                                 consent_json=json.dumps({"recording_consent": False, "retrieval_demo": True}),
                                 workflow_status="DRAFT", created_at=utc_now()))
            db.flush()
            for i, (role, text) in enumerate(turns, 1):
                segment = TranscriptSegment(id=f"{sid}-turn-{i:03d}", session_id=sid,
                                            start_ms=(i - 1) * 5000, end_ms=(i - 1) * 5000 + 4000,
                                            speaker_role=role, text=text, revision=1)
                db.add(TranscriptSegmentRecord(**segment.model_dump(mode="json")))
            created.append(sid)
    return {"patient_id": PATIENT_ID, "patient_path": f"/workspace/patients/{PATIENT_ID}",
            "sessions": [{"session_id": sid, "path": f"/workspace/sessions/{sid}", "initial_turn_count": len(turns)}
                         for sid, turns in SESSIONS.items()], "created": created,
            "existing_edits_preserved": True, "audio_included": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", help="Defaults to the app's DATABASE_URL or data/pshynox.db")
    args = parser.parse_args()
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = make_engine()
    try:
        result = seed_demo(make_session_factory(engine))
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

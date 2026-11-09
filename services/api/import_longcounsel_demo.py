"""Create clearly labelled LongCounsel-8 demo patients (5 ordered sessions each) from prepared research copies.

    python -m services.api.import_longcounsel_demo --split validation --limit 10
    python -m services.api.import_longcounsel_demo --with-results val12-v2-retrieval-current

Backs up SQLite first, never overwrites existing records, imports transcripts only (no labels, no scores) and refuses
the held-out test split. Synthetic research data: not a real patient record.

--with-results RUN_ID imports that benchmark run's trajectories and attaches the AI outputs the run already produced, so
the demo patients open with a populated dashboard without hours of GPU time: Stage A (domains, PHQ-8 items, GAD-7 items,
risk statements) from the run's Stage A cache, and the Stage B depression hypothesis from its checkpoints. These are real
model outputs (AI-generated, review pending), never ground truth. The benchmark checkpoints do not keep rule-outs,
differentials or missing-information questions, so imported hypotheses have none and say so in their notes; regenerate
from the UI for full output and for the other conditions. Sessions that already have progress values are left alone.
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.api.db import PatientRecord, SessionRecord, TranscriptSegmentRecord, utc_now
from services.api.schemas import PatientInput, TranscriptSegment
from services.progress.evaluation.longcounsel import PREPARED, PRIMARY_EDITION, SPLIT_ALIASES

PREFIX = "lc8-demo-"
BASE_DATE = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


def import_trajectories(db, transcripts: list[dict], edition: str) -> dict:
    counts = {"patients_created": 0, "sessions_created": 0, "turns_created": 0, "trajectories_skipped": 0}
    for t in transcripts:
        if t["split"] == "test":
            raise ValueError("Test-split trajectories are blocked from demo import")
        pid = f"{PREFIX}patient-{t['run_id']}"
        if db.get(PatientRecord, pid):
            counts["trajectories_skipped"] += 1
            continue  # never overwrite edits, observations or reviews
        intake = PatientInput(name=f"LongCounsel demo · {t['run_id']}", language="English",
                              additional_notes=f"Synthetic research demonstration (LongCounsel-8, {edition}, split {t['split']}). Not a real patient. "
                                               "No demographics, diagnosis, questionnaire scores or labels were imported. Timestamps are synthetic; no audio.")
        now = utc_now()
        db.add(PatientRecord(id=pid, name=intake.name, intake_json=intake.model_dump_json(), created_at=now, updated_at=now))
        db.flush()
        counts["patients_created"] += 1
        for session in t["sessions"]:
            sid = f"{PREFIX}{t['run_id']}-s{session['session_index']}"
            db.add(SessionRecord(id=sid, patient_id=pid, pseudonym_id=f"{t['run_id']}-s{session['session_index']}",
                                 session_type="INITIAL_ASSESSMENT" if session["session_index"] == 1 else "FOLLOW_UP",
                                 consent_json=json.dumps({"recording_consent": False, "research_demo": True, "dataset": "longcounsel-8",
                                                          "edition": edition, "source_split": t["split"], "source_run_id": t["run_id"],
                                                          "source_session_index": str(session["session_index"])}),
                                 workflow_status="DRAFT", created_at=BASE_DATE + timedelta(days=7 * (session["session_index"] - 1))))
            db.flush()
            for seg in session["segments"]:
                row = TranscriptSegment.model_validate({"id": PREFIX + seg["id"], "session_id": sid, "start_ms": seg["start_ms"], "end_ms": seg["end_ms"],
                                                        "speaker_role": seg["speaker_role"], "text": seg["text"], "revision": 1})
                db.add(TranscriptSegmentRecord(**row.model_dump(mode="json")))
                counts["turns_created"] += 1
            counts["sessions_created"] += 1
    db.commit()
    return counts


IMPORT_NOTE = ("Imported from benchmark run {run}: rule-outs, differentials and missing-information questions were not stored by "
               "the benchmark. Regenerate to see them.")


def _prefix_ids(value, known: set[str]):
    """Map benchmark segment ids (raw LongCounsel ids) to the demo patient's segment ids."""
    if isinstance(value, dict):
        return {k: _prefix_ids(v, known) for k, v in value.items()}
    if isinstance(value, list):
        return [_prefix_ids(v, known) for v in value]
    if isinstance(value, str) and value in known:
        return PREFIX + value
    return value


def _has_progress_values(db, session_id: str) -> bool:
    from sqlalchemy import select
    from services.api.db import DomainObservationRecord, InstrumentScoreRecord
    return any(db.scalar(select(model.id).where(model.session_id == session_id).limit(1)) for model in (DomainObservationRecord, InstrumentScoreRecord))


def attach_benchmark_results(db, run_id: str, runs: Path, prepared=PREPARED) -> dict:
    """Persist a validation benchmark run's Stage A and Stage B outputs onto already imported demo patients."""
    from sqlalchemy import select
    from services.api.db import ConditionPredictionRecord
    from services.progress import store
    from services.progress.evaluation.longcounsel import load_transcript
    from services.progress.prompts import PREDICT_SCHEMA_VERSION, PREDICT_VERSION
    folder = Path(runs) / run_id
    config = json.loads((folder / "config.json").read_text())
    if config["subset"]["split"] == "test":
        raise ValueError("Test-split benchmark results are blocked from demo import")
    cache = Path(runs) / "_stage-a" / config["evidence"] / config["stage_a_key"]
    job_id = f"lc8-import-{run_id}"
    model_version = f"qwen:{config['model']['sha256'][:16]}"
    counts = {"sessions_with_results": 0, "sessions_skipped_existing": 0, "sessions_missing_patient": 0, "stage_a_missing": 0,
              "predictions_saved": 0, "predictions_failed_in_run": 0, "predictions_skipped_existing": 0}
    store.ensure_registry_versions(db)
    checkpoints = [json.loads(p.read_text()) for p in sorted((folder / "sessions").glob("*.json"))]
    checkpoints.sort(key=lambda c: (c["run_id"], c["session_index"]))
    known_by_traj: dict[str, set[str]] = {}
    for c in checkpoints:
        traj, index = c["run_id"], c["session_index"]
        session = db.get(SessionRecord, f"{PREFIX}{traj}-s{index}")
        if session is None or not db.get(PatientRecord, f"{PREFIX}patient-{traj}"):
            counts["sessions_missing_patient"] += 1
            continue
        if traj not in known_by_traj:
            known_by_traj[traj] = {seg["id"] for s in load_transcript(traj, config["edition"], prepared)["sessions"] for seg in s["segments"]}
        known = known_by_traj[traj]
        cache_file = cache / f"{traj}__s{index}.json"
        if _has_progress_values(db, session.id):
            counts["sessions_skipped_existing"] += 1
        elif not cache_file.exists():
            counts["stage_a_missing"] += 1
        else:
            result = _prefix_ids(json.loads(cache_file.read_text()), known)
            store.persist_extraction(db, session, result, job_id=job_id)
            counts["sessions_with_results"] += 1
        b = c["stage_b"]
        if b.get("status") != "completed":
            counts["predictions_failed_in_run"] += 1
            continue
        exists = db.scalar(select(ConditionPredictionRecord.id).where(ConditionPredictionRecord.patient_id == session.patient_id,
            ConditionPredictionRecord.as_of_session_id == session.id, ConditionPredictionRecord.condition_key == config["condition"]).limit(1))
        if exists:
            counts["predictions_skipped_existing"] += 1
            continue
        b = _prefix_ids(b, known)
        notes = list(b.get("notes", []))
        downgraded = next((m for m in (re.search(r"Label '(\w+)' downgraded", n) for n in notes) if m), None)
        store.persist_prediction(db, session.patient_id, {
            "as_of_session_id": session.id, "condition_key": config["condition"], "subtype_key": None, "prediction": b["prediction"],
            "criteria_met_count": b["criteria_met_count"], "criteria_required": b["criteria_required"],
            "duration_status": b["duration_status"], "impairment_status": b["impairment_status"],
            "rule_outs": [], "differentials": [], "missing_information": [], "rationale": b["rationale"],
            "findings": [{**f, "duration_note": f.get("duration_note", "")} for f in b["findings"]],
            "postprocess": {"model_label": downgraded.group(1) if downgraded else b["prediction"], "model_subtype": None,
                            "notes": notes + [IMPORT_NOTE.format(run=run_id)], "removed_sentences": [], "requirements": []},
            "original": {"imported_from_benchmark_run": run_id, "history": config["history"]},
            "model_version": model_version, "prompt_version": PREDICT_VERSION, "schema_version": PREDICT_SCHEMA_VERSION,
            "registry_version": store.registry().combined_tag}, job_id=job_id)
        counts["predictions_saved"] += 1
    db.commit()
    return counts


def run_trajectories(run_id: str, runs: Path, edition: str = PRIMARY_EDITION, prepared=PREPARED) -> list[dict]:
    from services.progress.evaluation.longcounsel import load_transcript
    config = json.loads((Path(runs) / run_id / "config.json").read_text())
    if config["subset"]["split"] == "test":
        raise SystemExit("Test-split benchmark results are blocked from demo import.")
    ids = sorted({json.loads(p.read_text())["run_id"] for p in (Path(runs) / run_id / "sessions").glob("*.json")})
    return [load_transcript(i, config["edition"], prepared) for i in ids]


def select_transcripts(split: str, limit: int, edition: str = PRIMARY_EDITION, prepared=PREPARED) -> list[dict]:
    split = SPLIT_ALIASES.get(split, split)
    if split == "test":
        raise SystemExit("The held-out test split is blocked from demo import.")
    folder = prepared / edition / "transcripts"
    if not folder.is_dir():
        raise SystemExit(f"{folder} missing: run `python -m services.progress.evaluation.longcounsel prepare` first")
    rows = [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]
    return [r for r in rows if r["split"] == split][:limit]


def main():
    import sqlite3
    from dotenv import load_dotenv
    from services.api.db import ROOT, make_engine, make_session_factory
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", default="validation", choices=["validation", "val", "train"])
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--edition", default=PRIMARY_EDITION)
    parser.add_argument("--with-results", metavar="RUN_ID",
                        help="import this validation benchmark run's trajectories and attach its AI outputs (instead of --split/--limit)")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    from services.progress.evaluation.benchmark import RUNS
    if args.with_results:
        transcripts = run_trajectories(args.with_results, RUNS, args.edition)
    else:
        transcripts = select_transcripts(args.split, args.limit, args.edition)
    engine = make_engine()
    if engine.dialect.name != "sqlite":
        raise RuntimeError("This local import command requires SQLite for its automatic backup")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = ROOT / "data/backups" / f"before-longcounsel-demo-{stamp}.db"
    backup.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(engine.url.database) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    from services.api.main import migrate
    migrate()
    with make_session_factory(engine)() as db:
        counts = import_trajectories(db, transcripts, args.edition)
        if args.with_results:
            counts["results"] = attach_benchmark_results(db, args.with_results, RUNS)
    print(json.dumps({"backup": str(backup), **counts}, indent=2))


if __name__ == "__main__":
    main()

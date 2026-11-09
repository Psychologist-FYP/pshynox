"""Stage B: patient-level, criterion-by-criterion condition hypotheses with transcript citations.

``predict_condition`` is DB-free (session inputs in, post-processed result out) and is shared by the clinical
job (``predict_patient``) and the LongCounsel-8 benchmark. One call per condition within the 4,096-token budget.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from services.note_generation.recovery import Recovery
from services.note_generation.runtime import GenerationError
from services.note_generation.terminal import trace_for
from services.retrieval.engine import digest

from . import postprocess
from .evidence import Retriever, model_row
from .prompts import PREDICT_CRITERIA, PREDICT_SCHEMA_VERSION, PREDICT_SYNTH, PREDICT_VERSION
from .registry import registry
from .validation import Checker, other_person_only

RETRIEVAL_K = 3


def condition_brief(condition) -> dict:
    return {"key": condition.key, "label": condition.label,
            "criteria": [{"id": c.id, "kind": c.kind, "core": c.core, "summary": c.summary} for c in condition.assessed_criteria],
            "requirements": [f"{r.label}: at least {r.min_met} of {', '.join(r.criteria)}" + (f" including one of {', '.join(r.must_include_any_of)}" if r.must_include_any_of else "")
                             + (f"; {r.min_met_if_only.min_met} if only {r.min_met_if_only.criterion}" if r.min_met_if_only else "") for r in condition.requirements],
            "duration": condition.duration_summary, "impairment_required": condition.impairment_required,
            "rule_outs": [{"key": r.key, "summary": r.summary} for r in condition.rule_outs],
            "differentials": [{"condition": d.condition, "why": d.why} for d in condition.differentials],
            "subtypes": [{"key": s.key, "summary": s.summary} for s in condition.subtypes]}


def relevant_domains(condition) -> set[str]:
    return {d for c in condition.criteria for d in c.domains}


def _cite(aliases: list[str]) -> dict:
    return {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False, "required": ["ref", "basis"],
            "properties": {"ref": {"type": "string", "enum": aliases}, "basis": {"type": "string", "maxLength": 160}}}}


def criteria_schema(data: dict) -> dict:
    """Call 1..n: findings for the requested criteria only (planned in chunks that fit the budget)."""
    aliases = [r["id"] for r in data["sources"]] or ["A0"]
    requested = data["requested_criteria"]
    criterion = {"type": "object", "additionalProperties": False,
                 "required": ["id", "status", "evidence", "counter_evidence", "timeframe", "duration_note"],
                 "properties": {"id": {"type": "string", "enum": requested},
                                "status": {"type": "string", "enum": ["met", "not_met", "insufficient_evidence", "conflicting"]},
                                "evidence": _cite(aliases), "counter_evidence": _cite(aliases),
                                "timeframe": {"type": "string", "enum": ["current", "past", "both", "unclear"]},
                                "duration_note": {"type": "string", "maxLength": 140}}}
    return {"type": "object", "additionalProperties": False, "required": ["criteria"],
            "properties": {"criteria": {"type": "array", "minItems": len(requested), "maxItems": len(requested), "items": criterion}}}


def synthesis_schema(data: dict) -> dict:
    """Final call: label, statuses, rule-outs, differentials, missing information and cited rationale."""
    aliases = [r["id"] for r in data["sources"]] or ["A0"]
    cond = data["condition"]
    tri = {"type": "string", "enum": ["met", "not_met", "unknown"]}
    refs = {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": aliases}}
    subtype = {"anyOf": [{"type": "string", "enum": [s["key"] for s in cond["subtypes"]]}, {"type": "null"}]} if cond["subtypes"] else {"type": "null"}
    return {"type": "object", "additionalProperties": False,
            "required": ["duration_status", "impairment_status", "rule_outs", "differentials", "missing_information", "prediction", "subtype", "rationale"],
            "properties": {
                "duration_status": tri, "impairment_status": tri,
                "rule_outs": {"type": "array", "maxItems": len(cond["rule_outs"]), "items": {"type": "object", "additionalProperties": False,
                              "required": ["consideration", "status", "refs"], "properties": {
                                  "consideration": {"type": "string", "enum": [r["key"] for r in cond["rule_outs"]]},
                                  "status": {"type": "string", "enum": ["not_assessed", "evidence_present", "evidence_absent"]}, "refs": refs}}},
                "differentials": {"type": "array", "maxItems": 2, "items": {"type": "object", "additionalProperties": False,
                                  "required": ["condition", "why_considered", "distinguishing_present", "distinguishing_missing"], "properties": {
                                      "condition": {"type": "string", "maxLength": 60}, "why_considered": {"type": "string", "maxLength": 140},
                                      "distinguishing_present": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 100}},
                                      "distinguishing_missing": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 100}}}}},
                "missing_information": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 140}},
                "prediction": {"type": "string", "enum": ["likely", "possible", "unlikely", "insufficient_evidence"]},
                "subtype": subtype,
                "rationale": {"type": "array", "maxItems": 5, "items": {"type": "object", "additionalProperties": False, "required": ["sentence", "refs"],
                              "properties": {"sentence": {"type": "string", "maxLength": 200}, "refs": refs}}}}}


def response_schema(data: dict) -> dict:
    return criteria_schema(data) if "requested_criteria" in data else synthesis_schema(data)


# Output reservations measured in the pilot: findings with up to 3 cited turns ~110 tokens per criterion.
CRITERION_OUTPUT, CRITERIA_BASE, SYNTHESIS_OUTPUT, PACKET_OUTPUT = 115, 120, 800, 700


def criteria_output(tasks: list[dict]) -> int:
    return CRITERIA_BASE + CRITERION_OUTPUT * len(tasks)


def build_packet(sessions: list[dict], condition, runtime, *, history: str, retriever: Retriever, output: int, notes: list[str]):
    """Sessions oldest→newest, last is as_of. Returns (payload, alias map). Oldest raw sources dropped first to fit."""
    if history not in {"prior", "current"}:
        raise GenerationError("history must be prior or current")
    chosen = sessions if history == "prior" else sessions[-1:]
    domains = relevant_domains(condition)
    queries = list(dict.fromkeys(condition.retrieval_queries + [q for d in domains for q in registry().domains.get(d).queries]))
    alias_of, rows_by_alias = {}, {}
    counter = 0
    for s in chosen:
        for seg in s["segments"]:
            alias_of[seg["id"]] = f"A{counter}"
            rows_by_alias[f"A{counter}"] = {**seg, "session_index": s["index"]}
            counter += 1
    observations, questionnaires, per_session_sources = [], [], []
    for s in chosen:
        cited = []
        for o in s.get("observations", []):
            if o["domain_key"] not in domains or o.get("review_status") in {"rejected", "stale"}:
                continue
            refs = [alias_of[i] for i in o.get("evidence_segment_ids", []) if i in alias_of]
            cited += [i for i in o.get("evidence_segment_ids", []) if i in alias_of]
            observations.append({"session": s["index"], "domain": o["domain_key"], "status": o["status_value"], "rating": o.get("rating"),
                                 "timeframe": o.get("timeframe", "current"), **({"duration": o["duration_text"]} if o.get("duration_text") else {}), "refs": refs})
        for q in s.get("scores", []):
            if q.get("review_status") in {"rejected", "stale"}:
                continue
            questionnaires.append({"session": s["index"], "instrument": q["instrument_key"], "value": q.get("value"),
                                   "metric": q.get("metric"), "source": "administered" if q.get("provenance") == "MANUAL" else "AI estimate"})
        retrieved = retriever.select(s["session_id"], s["segments"], queries, RETRIEVAL_K) if s["segments"] else []
        ids = list(dict.fromkeys(cited + retrieved))
        order = {seg["id"]: i for i, seg in enumerate(s["segments"])}
        per_session_sources.append([alias_of[i] for i in sorted(ids, key=lambda i: order.get(i, 0)) if i in alias_of])
    brief = condition_brief(condition)

    def payload(source_lists):
        sources = [model_row(a, rows_by_alias[a], rows_by_alias[a]["session_index"]) for group in source_lists for a in group]
        return {"condition": brief, "sessions": [{"index": s["index"], "as_of": s is chosen[-1]} for s in chosen],
                "observations": observations, "questionnaires": questionnaires, "sources": sources}
    lists = list(per_session_sources)
    data = payload(lists)
    dropped = 0
    while not runtime.fits(PREDICT_CRITERIA, {**data, "requested_criteria": [c["id"] for c in brief["criteria"][:4]]}, output):
        target = next((i for i, group in enumerate(lists[:-1]) if group), None)
        if target is None:
            target = len(lists) - 1 if lists and len(lists[-1]) > 1 else None
        if target is None:
            raise GenerationError(f"{condition.key}: packet does not fit the context budget even without older sources")
        lists[target] = lists[target][1:] if target == len(lists) - 1 else lists[target][:-1]
        dropped += 1
        data = payload(lists)
    if dropped:
        notes.append(f"{condition.key}: {dropped} retrieved source turns from older sessions were left out to fit the context budget; their recorded observations were kept")
    return data, rows_by_alias


def _cites(items, checker: Checker, notes: list[str], label: str) -> list[dict]:
    out = []
    for item in items or []:
        if not isinstance(item, dict) or item.get("ref") not in checker.aliases:
            notes.append(f"{label}: dropped a citation to a source that was not supplied")
            continue
        row = checker.aliases[item["ref"]]
        if row["speaker_role"] == "CLIENT" and other_person_only(row["text"]):
            notes.append(f"{label}: dropped a citation describing another person")
            continue
        basis = str(item.get("basis") or "")[:300]
        if not checker.text_ok("citation", label, [basis], [row]):
            basis = ""
        out.append({"segment_id": row["id"], "session_id": row["session_id"], "start_ms": row["start_ms"], "end_ms": row["end_ms"],
                    "speaker_role": row["speaker_role"], "basis": basis})
    return out


def validate_criteria(value, data: dict) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("criteria"), list):
        raise GenerationError("Invalid output schema: criteria list required")


def validate_synthesis(value, data: dict) -> None:
    if not isinstance(value, dict) or value.get("prediction") not in postprocess.LABEL_ORDER:
        raise GenerationError("Invalid output schema: prediction required")


def validate_output(value, data: dict) -> None:
    if not isinstance(value, dict) or not isinstance(value.get("criteria"), list) or value.get("prediction") not in postprocess.LABEL_ORDER:
        raise GenerationError("Invalid output schema: criteria list and prediction required")


def map_output(value: dict, checker: Checker, notes: list[str]) -> dict:
    findings = []
    for c in value["criteria"]:
        if not isinstance(c, dict) or "id" not in c:
            continue
        findings.append({"criterion_id": c["id"], "status": c.get("status", "insufficient_evidence"),
                         "evidence": _cites(c.get("evidence"), checker, notes, c["id"]),
                         "counter_evidence": _cites(c.get("counter_evidence"), checker, notes, c["id"]),
                         "timeframe": c.get("timeframe", "unclear"), "duration_note": str(c.get("duration_note") or "")[:300]})
    rationale = []
    for r in value.get("rationale", []):
        refs = [checker.aliases[a]["id"] for a in r.get("refs", []) if a in checker.aliases]
        sentence = str(r.get("sentence") or "").strip()[:300]
        if sentence and not checker.text_ok("rationale", "sentence", [sentence], []):
            refs = []
        rationale.append({"sentence": sentence, "segment_ids": refs})
    rule_outs = [{"consideration": r.get("consideration"), "status": r.get("status", "not_assessed"),
                  "segment_ids": [checker.aliases[a]["id"] for a in r.get("refs", []) if a in checker.aliases]}
                 for r in value.get("rule_outs", []) if isinstance(r, dict)]
    return {"findings": findings, "duration_status": value.get("duration_status", "unknown"), "impairment_status": value.get("impairment_status", "unknown"),
            "rule_outs": rule_outs, "differentials": [d for d in value.get("differentials", []) if isinstance(d, dict)][:3],
            "missing_information": [str(m)[:200] for m in value.get("missing_information", [])][:5], "prediction": value["prediction"],
            "subtype": value.get("subtype"), "rationale": rationale}


def predict_condition(sessions: list[dict], condition_key: str, runtime, folder, *, history: str = "prior", names: list[str] | None = None,
                      retriever: Retriever | None = None, progress=lambda s: None) -> dict:
    """Run Stage B for one condition as of the last session in ``sessions``. Returns a stored-shape result or raises."""
    condition = registry().conditions.get(condition_key)
    folder = Path(folder)
    log = trace_for(runtime, folder)
    started = time.monotonic()
    notes: list[str] = []
    runtime.load("qwen")
    data, rows = build_packet(sessions, condition, runtime, history=history, retriever=retriever or Retriever(), output=PACKET_OUTPUT, notes=notes)
    revision = digest([[s["session_id"], digest(s["segments"])] for s in sessions] + [history])
    recovery = Recovery(runtime, folder, revision, PREDICT_VERSION, progress, schema_factory=response_schema)
    tasks = [{"id": f"criterion:{c['id']}", "text": c["id"], "criterion": c["id"]} for c in data["condition"]["criteria"]]

    def criteria_payload(chunk):
        return {**data, "requested_criteria": [t["criterion"] for t in chunk]}
    plan, current = [], []
    for task in tasks:  # greedy chunks of criteria that fit prompt + reserved output
        if runtime.fits(PREDICT_CRITERIA, criteria_payload(current + [task]), criteria_output(current + [task])):
            current.append(task)
            continue
        if not current:
            raise GenerationError(f"{condition_key}: a single criterion does not fit the context budget")
        plan.append(current)
        current = [task]
    if current:
        plan.append(current)
    criteria = []
    for chunk in plan:
        for _done, value in recovery.process(PREDICT_CRITERIA, chunk, criteria_payload, validate_criteria, output=criteria_output(chunk),
                                             label=f"predict {condition_key} criteria"):
            criteria += value["criteria"]
    # Synthesis sees the findings and only the turns they (or the recorded observations) cite.
    cited = {e["ref"] for c in criteria for e in (c.get("evidence") or []) + (c.get("counter_evidence") or []) if isinstance(e, dict)}
    cited |= {a for o in data["observations"] for a in o.get("refs", [])}
    synth_sources = [r for r in data["sources"] if r["id"] in cited]
    synth = {"condition": data["condition"], "sessions": data["sessions"], "observations": data["observations"],
             "questionnaires": data["questionnaires"], "sources": synth_sources,
             "findings": [{"id": c.get("id"), "status": c.get("status"), "refs": [e["ref"] for e in c.get("evidence") or [] if isinstance(e, dict)],
                           "counter_refs": [e["ref"] for e in c.get("counter_evidence") or [] if isinstance(e, dict)],
                           "timeframe": c.get("timeframe"), "duration_note": c.get("duration_note")} for c in criteria]}
    while not runtime.fits(PREDICT_SYNTH, synth, SYNTHESIS_OUTPUT) and synth["sources"]:
        synth["sources"] = synth["sources"][1:]
        notes.append(f"{condition_key}: an older cited turn was left out of the synthesis call to fit the context budget")
    summary = recovery.process(PREDICT_SYNTH, [{"id": f"synthesis:{condition_key}", "text": condition_key}], lambda _t: synth,
                               validate_synthesis, output=SYNTHESIS_OUTPUT, label=f"predict {condition_key} synthesis")[0][1]
    value = {"criteria": criteria, **summary}
    validate_output(value, data)
    allowed = {r["id"]: {**rows[r["id"]], "alias": r["id"]} for r in data["sources"]}
    checker = Checker(allowed, names)
    mapped = map_output(value, checker, notes)
    depressive = any(o["domain"] in {"low_mood", "interest"} and o["status"] == "rated" and (o["rating"] or 0) >= 2 for o in data["observations"])
    result = postprocess.apply(condition, mapped, depressive_features=depressive)
    result["postprocess"]["notes"] = notes + checker.limitations + result["postprocess"]["notes"]
    result.update(condition_key=condition_key, as_of_session_id=sessions[-1]["session_id"], history=history,
                  rule_outs=mapped["rule_outs"], differentials=mapped["differentials"], missing_information=mapped["missing_information"],
                  prompt_version=PREDICT_VERSION, schema_version=PREDICT_SCHEMA_VERSION, registry_version=registry().combined_tag,
                  model_version=f"qwen:{getattr(runtime, 'model', {}).get('sha256', '')[:16]}", seconds=round(time.monotonic() - started, 3),
                  original=value, source_turns=len(data["sources"]))
    log.event("progress.prediction.completed", condition=condition_key, label=result["prediction"], subtype=result["subtype_key"],
              met=result["criteria_met_count"], required=result["criteria_required"], seconds=result["seconds"])
    return result


# ---------------------------------------------------------------- clinical job (DB layer)

def session_inputs(db, patient_id: str, as_of_session_id: str) -> list[dict]:
    import json
    from sqlalchemy import select
    from services.api.db import DomainObservationRecord, InstrumentScoreRecord
    from services.api.main import _session_segments
    from services.progress import store
    sessions = store.patient_sessions(db, patient_id)
    index = next(i for i, s in enumerate(sessions) if s.id == as_of_session_id)
    out = []
    for i, s in enumerate(sessions[: index + 1]):
        obs = db.scalars(select(DomainObservationRecord).where(DomainObservationRecord.session_id == s.id))
        scores = db.scalars(select(InstrumentScoreRecord).where(InstrumentScoreRecord.session_id == s.id, InstrumentScoreRecord.is_active.is_(True)))
        out.append({"session_id": s.id, "index": i + 1, "segments": [r.model_dump(mode="json") for r in _session_segments(db, s.id)],
                    "observations": [{"domain_key": o.domain_key, "status_value": o.status_value, "rating": o.rating, "timeframe": o.timeframe,
                                      "duration_text": o.duration_text, "review_status": o.review_status,
                                      "evidence_segment_ids": json.loads(o.evidence_segment_ids_json)} for o in obs],
                    "scores": [{"instrument_key": q.instrument_key, "provenance": q.provenance, "review_status": q.review_status,
                                "metric": "phq8" if q.instrument_key == "phq9" else "total",
                                "value": json.loads(q.subtotals_json).get("phq8", {}).get("total") if q.instrument_key == "phq9" else q.total} for q in scores]})
    return out


def predict_patient(job: dict, runtime, folder, as_of_session_id: str, *, condition_keys=None, progress=lambda s: None):
    import json
    from services.api.db import PatientRecord, SessionRecord
    from services.api.main import session_factory
    from services.progress import store
    with session_factory() as db:
        session = db.get(SessionRecord, as_of_session_id)
        patient = db.get(PatientRecord, session.patient_id)
        names = [n for n in (patient.name, json.loads(patient.intake_json).get("preferred_name", "")) if n]
        inputs = session_inputs(db, session.patient_id, as_of_session_id)
    keys = condition_keys or registry().conditions.enabled_conditions
    counts, limitations = {"predictions_saved": 0, "predictions_without_evidence": 0, "predictions_failed": 0}, []
    for key in keys:
        progress(f"predicting {key}")
        if hasattr(runtime, "deadline"):  # NOTE_JOB_TIMEOUT applies per condition, not to the whole multi-stage job
            runtime.deadline = time.monotonic() + float(os.getenv("NOTE_JOB_TIMEOUT", "1800"))
        try:
            result = predict_condition(inputs, key, runtime, Path(folder), history="prior", names=names, progress=progress)
        except GenerationError as exc:
            if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
                raise
            counts["predictions_failed"] += 1
            limitations.append(f"{key}: prediction incomplete ({str(exc)[:200]})")
            continue
        limitations += [f"{key}: {n}" for n in result["postprocess"]["notes"] if "left out to fit" in n]
        if not result["has_evidence"]:
            counts["predictions_without_evidence"] += 1
            limitations.append(f"{key}: no cited evidence, so no hypothesis was stored")
            continue
        with session_factory() as db:
            store.persist_prediction(db, session.patient_id, result, job_id=job["id"])
            db.commit()
        counts["predictions_saved"] += 1
    return counts, limitations

"""Stage B, registry v2 (``condition-predict-dsm-v1``): the model proposes feature statuses across the patient's sessions
(with citations) and groups them into periods; ``rules.py`` computes every label. The model's own labels are logged
for comparison only.

``predict_dsm`` is DB-free (session inputs in, results out). ``predict_patient_dsm`` is the clinical job layer.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from services.note_generation.recovery import Recovery
from services.note_generation.runtime import GenerationError
from services.note_generation.terminal import trace_for
from services.retrieval.engine import digest

from services.progress.evidence import Retriever, model_row
from services.progress.validation import Checker, other_person_only

from . import rules
from .extraction import DEGREES, FREQUENCIES
from .prompts import PREDICT_DSM_FEATURES, PREDICT_DSM_PERIODS, PREDICT_DSM_SCHEMA_VERSION, PREDICT_DSM_VERSION
from .registry import STATUSES, DsmRegistry, dsm_registry

RETRIEVAL_K = 2
DURATION_QUERY = "started began since ago lasted weeks months years days period episode before first time hospital admitted"
DURATION_K = 3
# Measured on the role-play run (Qwen3-4B): one feature with three cited turns can take ~270+ tokens, so a call always
# reserves room for a complete answer and older retrieved turns are dropped instead.
FEATURE_OUTPUT, BASE_OUTPUT, MIN_OUTPUT, PERIODS_OUTPUT = 230, 150, 640, 650


def features_output(n: int) -> int:
    return max(MIN_OUTPUT, BASE_OUTPUT + FEATURE_OUTPUT * n)
TIME_FRAMES = ["current", "past", "both", "unclear"]
LABELS = list(rules.LABEL_TEXT)
ORDER_GROUPS = ["depressive", "elevated", "anxiety", "social", "impairment", "exclusion"]


def wanted_conditions(reg: DsmRegistry, keys: list[str] | None = None) -> list[str]:
    return keys or (reg.conditions.enabled_conditions + reg.conditions.differential_conditions)


def needed_features(reg: DsmRegistry, keys: list[str]) -> list[str]:
    wanted: set[str] = set()
    for key in reg.conditions.episode_conditions + keys:
        wanted |= set(reg.condition_features(key))
    wanted |= {"feat.keyed_up_tense", "feat.restlessness", "feat.fear_awful", "feat.fear_losing_control", "feat.performance_only"}  # specifiers
    return [f.key for g in ORDER_GROUPS for f in reg.features.features if f.group == g and f.key in wanted]


def _cites(aliases, n=3):
    return {"type": "array", "maxItems": n, "items": {"type": "object", "additionalProperties": False, "required": ["ref", "basis"],
            "properties": {"ref": {"type": "string", "enum": aliases}, "basis": {"type": "string", "maxLength": 140}}}}


def features_schema(data: dict) -> dict:
    reg = dsm_registry()
    aliases = [r["id"] for r in data["sources"]] or ["A0"]
    nullable = lambda values: {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]} if values else {"type": "null"}  # noqa: E731
    props = {}
    for f in data["requested_features"]:
        feat = reg.feature(f["key"])
        props[f["key"]] = {"type": "object", "additionalProperties": False,
                           "required": ["status", "contexts", "degree", "qualifier", "frequency", "time_frame", "period_note", "evidence", "counter_evidence"],
                           "properties": {"status": {"type": "string", "enum": list(STATUSES)},
                                          "contexts": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": reg.features.contexts}},
                                          "degree": nullable(DEGREES if feat.degree else []), "qualifier": nullable(feat.qualifiers),
                                          "frequency": {"type": "string", "enum": FREQUENCIES}, "time_frame": {"type": "string", "enum": TIME_FRAMES},
                                          "period_note": {"type": "string", "maxLength": 80}, "evidence": _cites(aliases),
                                          "counter_evidence": {"type": "array", "maxItems": 3, "items": {"type": "object", "additionalProperties": False,
                                                               "required": ["ref", "kind", "basis"], "properties": {
                                                                   "ref": {"type": "string", "enum": aliases}, "kind": {"type": "string", "enum": ["denial", "alternative"]},
                                                                   "basis": {"type": "string", "maxLength": 140}}}}}}
    return {"type": "object", "additionalProperties": False, "required": ["features"],
            "properties": {"features": {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}}}


def periods_schema(data: dict) -> dict:
    aliases = [r["id"] for r in data["sources"]] or ["A0"]
    keys = [p["feature"] for p in data["proposals"]] or ["none"]
    return {"type": "object", "additionalProperties": False, "required": ["periods", "model_labels"], "properties": {
        "periods": {"type": "array", "maxItems": 4, "items": {"type": "object", "additionalProperties": False,
                    "required": ["label", "time_frame", "duration_days", "hospitalised", "duration_refs", "features"],
                    "properties": {"label": {"type": "string", "maxLength": 60}, "time_frame": {"type": "string", "enum": ["current", "past"]},
                                   "duration_days": {"anyOf": [{"type": "integer", "minimum": 0, "maximum": 20000}, {"type": "null"}]},
                                   "hospitalised": {"type": "boolean"}, "duration_refs": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": aliases}},
                                   "features": {"type": "array", "maxItems": 40, "items": {"type": "string", "enum": keys}}}}},
        "model_labels": {"type": "object", "additionalProperties": False, "required": data["conditions"],
                         "properties": {k: {"type": "string", "enum": LABELS} for k in data["conditions"]}}}}


def response_schema(data: dict) -> dict:
    return features_schema(data) if "requested_features" in data else periods_schema(data)


def _validate_features(value, data):
    if not isinstance(value, dict) or not isinstance(value.get("features"), dict) or set(f["key"] for f in data["requested_features"]) - set(value["features"]):
        raise GenerationError("Invalid output schema: every requested feature required")


def _validate_periods(value, data):
    if not isinstance(value, dict) or not isinstance(value.get("periods"), list):
        raise GenerationError("Invalid output schema: periods list required")


def build_sources(sessions: list[dict], reg: DsmRegistry, features: list[str], retriever: Retriever):
    """Patient-wide aliases A<n>; per session: turns cited by active findings of these features, plus BM25 retrieval."""
    alias_of, rows = {}, {}
    n = 0
    for s in sessions:
        for seg in s["segments"]:
            alias_of[seg["id"]] = f"A{n}"
            rows[f"A{n}"] = {**seg, "session_index": s["index"], "session_id": s["session_id"]}
            n += 1
    queries = list(dict.fromkeys(q for key in features for q in reg.feature(key).queries + reg.feature(key).counter_queries))
    per_session = []
    for s in sessions:
        cited = [i for f in s.get("findings", []) if f["feature_key"] in features and f.get("review_status") not in {"rejected", "stale"}
                 for i in f.get("evidence_segment_ids", []) + [c["segment_id"] for c in f.get("counter_evidence", [])]]
        retrieved = retriever.select(s["session_id"], s["segments"], queries, RETRIEVAL_K) if s["segments"] else []
        order = {seg["id"]: i for i, seg in enumerate(s["segments"])}
        ids = [i for i in dict.fromkeys(cited + retrieved) if i in alias_of]
        per_session.append([alias_of[i] for i in sorted(ids, key=lambda i: order.get(i, 0))])
    return alias_of, rows, per_session


def compact_findings(sessions: list[dict], features: list[str], alias_of: dict) -> list[dict]:
    out = []
    for s in sessions:
        for f in s.get("findings", []):
            if f["feature_key"] not in features or f.get("review_status") in {"rejected", "stale"} or f["status"] == "insufficient_evidence":
                continue
            out.append({"session": s["index"], "feature": f["feature_key"], "status": f["status"], "frequency": f.get("frequency"),
                        "time_frame": f.get("time_frame"), **({"period": f["period_label"]} if f.get("period_label") else {}),
                        **({"onset": f["onset_or_duration_text"]} if f.get("onset_or_duration_text") else {}),
                        **({"contexts": f["contexts"]} if f.get("contexts") else {}),
                        "refs": [alias_of[i] for i in f.get("evidence_segment_ids", []) if i in alias_of][:3]})
    return out


def fit_payload(runtime, system, make, lists, output, notes, label):
    data = make(lists)
    dropped = 0
    while not runtime.fits(system, data, output):
        target = next((i for i, group in enumerate(lists[:-1]) if group), None)
        if target is None:
            target = len(lists) - 1 if lists and len(lists[-1]) > 1 else None
        if target is None:
            raise GenerationError(f"{label}: packet does not fit the context budget even without older sources")
        lists[target] = lists[target][1:] if target == len(lists) - 1 else lists[target][:-1]
        dropped += 1
        data = make(lists)
    if dropped:
        notes.append(f"{label}: {dropped} retrieved turns from older sessions were left out to fit the context budget; recorded findings were kept")
    return data, lists


def _cite(row: dict, basis: str = "", kind: str | None = None) -> dict:
    out = {"segment_id": row["id"], "session_id": row["session_id"], "start_ms": row["start_ms"], "end_ms": row["end_ms"],
           "speaker_role": row["speaker_role"], "basis": basis}
    if kind:
        out["kind"] = kind
    return out


def map_proposal(key: str, item: dict, checker: Checker, notes: list[str]) -> dict:
    reg = dsm_registry()
    feat = reg.feature(key)
    evidence, counter = [], []
    for c in item.get("evidence") or []:
        row = checker.aliases.get(c.get("ref")) if isinstance(c, dict) else None
        if row is None:
            notes.append(f"{key}: dropped a citation to a source that was not supplied")
            continue
        if row["speaker_role"] == "CLIENT" and other_person_only(row["text"]):
            notes.append(f"{key}: dropped a citation describing another person")
            continue
        basis = str(c.get("basis") or "")[:300]
        evidence.append(_cite(row, basis if checker.text_ok("citation", key, [basis], [row]) else ""))
    for c in item.get("counter_evidence") or []:
        row = checker.aliases.get(c.get("ref")) if isinstance(c, dict) else None
        if row is None:
            continue
        kind = c.get("kind") if c.get("kind") in {"denial", "alternative"} else "alternative"
        if kind == "denial" and row["speaker_role"] != "CLIENT":
            kind = "alternative"
        basis = str(c.get("basis") or "")[:300]
        counter.append(_cite(row, basis if checker.text_ok("citation", key, [basis], [row]) else "", kind))
    status = item.get("status") if item.get("status") in STATUSES else "insufficient_evidence"
    if status != "insufficient_evidence" and not evidence:
        notes.append(f"{key}: {status} without a valid citation was treated as insufficient evidence")
        status = "insufficient_evidence"
    return {"status": status, "contexts": [c for c in item.get("contexts") or [] if c in reg.features.contexts],
            "degree": item.get("degree") if feat.degree and item.get("degree") in DEGREES else None,
            "qualifier": item.get("qualifier") if item.get("qualifier") in feat.qualifiers else None,
            "frequency": item.get("frequency") if item.get("frequency") in FREQUENCIES else "unclear",
            "time_frame": item.get("time_frame") if item.get("time_frame") in TIME_FRAMES else "unclear",
            "period_note": str(item.get("period_note") or "")[:120], "evidence": evidence, "counter_evidence": counter}


def add_stage_a_denials(proposals: dict[str, dict], sessions: list[dict], rows_by_id: dict[str, dict]) -> list[str]:
    """A cited client denial recorded in Stage A, in a session the proposal cites, contradicts a 'present' proposal."""
    notes = []
    for s in sessions:
        for f in s.get("findings", []):
            p = proposals.get(f["feature_key"])
            if f["status"] != "explicit_denial" or p is None or p["status"] != "present" or f.get("review_status") in {"rejected", "stale"}:
                continue
            if s["session_id"] not in {e["session_id"] for e in p["evidence"]}:
                continue
            for sid in f.get("evidence_segment_ids", []):
                row = rows_by_id.get(sid)
                if row and row["speaker_role"] == "CLIENT" and sid not in {c["segment_id"] for c in p["counter_evidence"]}:
                    p["counter_evidence"].append(_cite(row, f.get("basis", ""), "denial"))
                    notes.append(f"{f['feature_key']}: a client denial recorded in session {s['index']} was added as counter-evidence")
    return notes


def build_periods(proposals: dict[str, dict], value: dict, checker: Checker) -> list[dict]:
    periods, assigned = [], set()
    for i, p in enumerate(value.get("periods") or []):
        if not isinstance(p, dict):
            continue
        refs = [checker.aliases[a] for a in p.get("duration_refs") or [] if a in checker.aliases]
        features = [k for k in p.get("features") or [] if k in proposals and k not in assigned]
        assigned |= set(features)
        days = p.get("duration_days") if isinstance(p.get("duration_days"), int) else None
        periods.append({"id": f"P{i + 1}", "label": str(p.get("label") or f"Period {i + 1}")[:80], "time_frame": p.get("time_frame") if p.get("time_frame") in {"current", "past"} else "current",
                        "duration_days": days if refs else None, "hospitalised": bool(p.get("hospitalised")) and bool(refs),
                        "duration_evidence": [_cite(r) for r in refs], "proposals": {k: proposals[k] for k in features},
                        **({"uncited_duration_days": days} if days is not None and not refs else {})})
    current = next((p for p in periods if p["time_frame"] == "current"), None)
    if current is None:
        current = {"id": "current", "label": "Current", "time_frame": "current", "duration_days": None, "hospitalised": False, "duration_evidence": [], "proposals": {}}
        periods.insert(0, current)
    past_undated = None
    for key, p in proposals.items():
        if key in assigned:
            continue
        if p["time_frame"] == "past":
            if past_undated is None:
                past_undated = {"id": "past", "label": "Past (undated)", "time_frame": "past", "duration_days": None, "hospitalised": False, "duration_evidence": [], "proposals": {}}
                periods.append(past_undated)
            past_undated["proposals"][key] = p
        else:
            current["proposals"][key] = p
    return periods


def predict_dsm(sessions: list[dict], runtime, folder, *, keys: list[str] | None = None, names: list[str] | None = None,
                retriever: Retriever | None = None, a9: dict | None = None, progress=lambda s: None) -> dict:
    """Stage B as of the last session in ``sessions``. Returns {'results': {condition: result}, 'periods', 'proposals', ...}."""
    reg = dsm_registry()
    keys = wanted_conditions(reg, keys)
    folder = Path(folder)
    log = trace_for(runtime, folder)
    started = time.monotonic()
    notes: list[str] = []
    runtime.load("qwen")
    retriever = retriever or Retriever()
    features = needed_features(reg, keys)
    used = [[f for f in s.get("findings", []) if f["status"] != "insufficient_evidence" and f.get("review_status") not in {"rejected", "stale"}] for s in sessions]
    revision = digest([[s["session_id"], digest(s["segments"])] for s in sessions] + [digest(used)])  # only what Stage B reads
    recovery = Recovery(runtime, folder, revision, PREDICT_DSM_VERSION, progress, schema_factory=response_schema)
    rows_by_id = {seg["id"]: {**seg, "session_id": s["session_id"]} for s in sessions for seg in s["segments"]}
    proposals: dict[str, dict] = {}
    allowed_all: dict[str, dict] = {}
    groups = [[k for k in features if reg.feature(k).group == g] for g in ORDER_GROUPS]
    for group_features in [g for g in groups if g]:
        alias_of, rows, per_session = build_sources(sessions, reg, group_features, retriever)
        session_meta = [{"index": s["index"], "as_of": s is sessions[-1]} for s in sessions]
        tasks = [{"id": f"feature:{k}", "text": k, "key": k} for k in group_features]
        plan, current = [], []
        probe = lambda chunk, lists: {"sessions": session_meta, "requested_features": [{"key": t["key"], **_brief(reg, t["key"])} for t in chunk],  # noqa: E731
                                      "findings": compact_findings(sessions, [t["key"] for t in chunk], alias_of),
                                      "sources": [model_row(a, rows[a], rows[a]["session_index"]) for g in lists for a in g]}
        for task in tasks:
            if not current or runtime.fits(PREDICT_DSM_FEATURES, probe(current + [task], per_session), features_output(len(current) + 1)):
                current.append(task)
            else:
                plan.append(current)
                current = [task]
        if current:
            plan.append(current)
        for chunk in plan:
            output = features_output(len(chunk))
            data, lists = fit_payload(runtime, PREDICT_DSM_FEATURES, lambda ls, c=chunk: probe(c, ls), [list(g) for g in per_session], output, notes,
                                      f"dsm {chunk[0]['key']}")
            allowed = {r["id"]: {**rows[r["id"]], "alias": r["id"]} for r in data["sources"]}
            allowed_all.update(allowed)
            checker = Checker(allowed, names)
            for done, value in recovery.process(PREDICT_DSM_FEATURES, chunk, lambda c, ls=lists: probe(c, ls), _validate_features, output=output,
                                                label=f"predict dsm {chunk[0]['key']}"):
                for key, item in value["features"].items():
                    if key in {t["key"] for t in done}:
                        proposals[key] = map_proposal(key, item, checker, notes)
            notes += checker.limitations
    notes += add_stage_a_denials(proposals, sessions, rows_by_id)
    # Periods: group the cited findings into episodes; the model's own labels are logged only.
    cited_aliases = {a for a, r in allowed_all.items() if r["id"] in {e["segment_id"] for p in proposals.values() for e in p["evidence"]}}
    reverse = {r["id"]: a for a, r in allowed_all.items()}
    compact = [{"feature": k, "status": p["status"], "time_frame": p["time_frame"], "period_note": p["period_note"],
                "refs": [reverse[e["segment_id"]] for e in p["evidence"] if e["segment_id"] in reverse][:3]}
               for k, p in proposals.items() if p["status"] in {"present", "subthreshold", "conflicting"}]
    model_labels = {}
    if compact:
        # Duration and onset turns are rarely cited as symptom evidence: retrieve them for the periods call.
        alias_of, rows, _ = build_sources(sessions, reg, [], retriever)
        duration_set: set[str] = set()
        for s in sessions:
            for sid in retriever.select(s["session_id"], s["segments"], [DURATION_QUERY], DURATION_K) if s["segments"] else []:
                if sid in reverse:
                    duration_set.add(reverse[sid])
                elif sid in alias_of:
                    alias = "D" + alias_of[sid][1:]
                    allowed_all[alias] = {**rows[alias_of[sid]], "alias": alias}
                    reverse[sid] = alias
                    duration_set.add(alias)
        evidence = sorted(cited_aliases - duration_set, key=lambda a: int(a[1:]))
        duration = sorted(duration_set, key=lambda a: int(a[1:]))

        def periods_data():
            chosen = sorted(evidence + duration, key=lambda a: int(a[1:]))
            return {"sessions": [{"index": s["index"], "as_of": s is sessions[-1]} for s in sessions], "conditions": keys, "proposals": compact,
                    "sources": [model_row(a, allowed_all[a], allowed_all[a]["session_index"]) for a in chosen]}
        data = periods_data()
        while not runtime.fits(PREDICT_DSM_PERIODS, data, PERIODS_OUTPUT) and (evidence or duration):
            (evidence or duration).pop(0)  # older symptom turns go first; onset and duration turns are kept longest
            notes.append("periods: an older cited turn was left out to fit the context budget")
            data = periods_data()
        value = recovery.process(PREDICT_DSM_PERIODS, [{"id": "periods", "text": "periods"}], lambda _c: data, _validate_periods,
                                 output=PERIODS_OUTPUT, label="predict dsm periods")[0][1]
        checker = Checker(allowed_all, names)
        periods = build_periods(proposals, value, checker)
        model_labels = {k: v for k, v in (value.get("model_labels") or {}).items() if v in LABELS}
    else:
        periods = build_periods(proposals, {"periods": []}, Checker(allowed_all, names))
    extras = {"global": {k: p for k, p in proposals.items() if reg.feature(k).kind == "exclusion"}, "a9": a9 or {}}
    results = rules.evaluate_all(reg, periods, extras, keys)
    for key, res in results.items():
        res.update(as_of_session_id=sessions[-1]["session_id"], prompt_version=PREDICT_DSM_VERSION, schema_version=PREDICT_DSM_SCHEMA_VERSION,
                   registry_version=reg.tag, model_version=f"qwen:{getattr(runtime, 'model', {}).get('sha256', '')[:16]}",
                   periods=[{k: v for k, v in p.items() if k != "proposals"} | {"features": list(p["proposals"])} for p in periods],
                   proposals={k: {kk: vv for kk, vv in p.items() if kk not in {"evidence", "counter_evidence"}} for k, p in proposals.items()},
                   postprocess={"model_label": model_labels.get(key), "notes": list(dict.fromkeys(notes))})
    seconds = round(time.monotonic() - started, 3)
    log.event("progress.dsm_prediction.completed", seconds=seconds, conditions={k: r["label"] for k, r in results.items()})
    return {"results": results, "periods": periods, "proposals": proposals, "notes": notes, "seconds": seconds, "model_labels": model_labels}


def _brief(reg: DsmRegistry, key: str) -> dict:
    feat = reg.feature(key)
    return {"label": feat.label, "definition": feat.definition,
            "criterion_items": [{"id": i.id, "paraphrase": i.paraphrase, **({"notes": i.notes} if i.notes else {})} for i in reg.items_for_feature(key)]}


# ---------------------------------------------------------------- clinical job (DB layer)

def session_inputs_dsm(db, patient_id: str, as_of_session_id: str) -> list[dict]:
    import json
    from sqlalchemy import select
    from services.api.db import DsmFindingRecord
    from services.api.main import _session_segments
    from services.progress import store
    sessions = store.patient_sessions(db, patient_id)
    index = next(i for i, s in enumerate(sessions) if s.id == as_of_session_id)
    out = []
    for i, s in enumerate(sessions[: index + 1]):
        findings = db.scalars(select(DsmFindingRecord).where(DsmFindingRecord.session_id == s.id, DsmFindingRecord.is_active.is_(True)))
        out.append({"session_id": s.id, "index": i + 1, "segments": [r.model_dump(mode="json") for r in _session_segments(db, s.id)],
                    "findings": [{"feature_key": f.feature_key, "status": f.status, "frequency": f.frequency, "time_frame": f.time_frame,
                                  "period_label": f.period_label, "onset_or_duration_text": f.onset_or_duration_text, "contexts": json.loads(f.contexts_json),
                                  "degree": f.degree, "qualifier": f.qualifier, "basis": f.basis, "review_status": f.review_status,
                                  "evidence_segment_ids": json.loads(f.evidence_segment_ids_json), "counter_evidence": json.loads(f.counter_evidence_json)}
                                 for f in findings]})
    return out


def predict_patient_dsm(job: dict, runtime, folder, as_of_session_id: str, *, condition_keys=None, batch_id=None, progress=lambda s: None):
    import json
    from services.api.db import PatientRecord, SessionRecord
    from services.api.main import session_factory
    from . import store as dsm_store
    with session_factory() as db:
        session = db.get(SessionRecord, as_of_session_id)
        patient = db.get(PatientRecord, session.patient_id)
        names = [n for n in (patient.name, json.loads(patient.intake_json).get("preferred_name", "")) if n]
        inputs = session_inputs_dsm(db, session.patient_id, as_of_session_id)
        a9 = dsm_store.a9_status(db, as_of_session_id)
    if hasattr(runtime, "deadline"):
        runtime.deadline = time.monotonic() + float(os.getenv("NOTE_JOB_TIMEOUT", "1800"))
    counts, limitations = {"predictions_saved": 0, "predictions_without_evidence": 0, "predictions_failed": 0}, []
    try:
        out = predict_dsm(inputs, runtime, Path(folder), keys=condition_keys, names=names, a9=a9, progress=progress)
    except GenerationError as exc:
        if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
            raise
        counts["predictions_failed"] += 1
        return counts, [f"DSM hypotheses incomplete ({str(exc)[:200]})"]
    limitations += [n for n in out["notes"] if "left out to fit" in n]
    with session_factory() as db:
        episodes = set(dsm_registry().conditions.episode_conditions)
        for key, result in out["results"].items():
            if not result["has_evidence"]:  # principle 2: a hypothesis with no cited evidence is never stored
                if key not in episodes:
                    counts["predictions_without_evidence"] += 1
                    limitations.append(f"{key}: no cited evidence, so no hypothesis was stored")
                continue
            dsm_store.persist_dsm_prediction(db, session.patient_id, result, job_id=job["id"], batch_id=batch_id)
            counts["predictions_saved"] += 1
        db.commit()
    return counts, limitations

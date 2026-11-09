"""Stage A, registry v2 (``progress-extract-dsm-v1``): per-session criterion-feature findings with counter-evidence,
DSM risk categories, and (separately, unchanged) the PHQ-9 / GAD-7 item estimates and plan follow-ups.

DB-free: a transcript snapshot in, a validated result out. Reuses the v1 runtime, Recovery (checkpoint cache, one
repair, output-limit split), the T<n> aliases, BM25 retrieval and the v1 Checker. The v1 modules are imported, never
changed (they are bound to the LongCounsel-8 benchmark hash).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from services.note_generation.recovery import Recovery
from services.note_generation.runtime import GenerationError
from services.note_generation.terminal import trace_for
from services.retrieval.engine import digest

from services.progress import extraction as v1x
from services.progress.evidence import PLAN_QUERY, Retriever, aliases_for, model_row
from services.progress.prompts import EXTRACT_V2, EXTRACT_V2_SCHEMA_VERSION, EXTRACT_V2_VERSION
from services.progress.registry import registry as v1_registry
from services.progress.validation import Checker, validate_extraction

from .prompts import EXTRACT_DSM, EXTRACT_DSM_SCHEMA_VERSION, EXTRACT_DSM_VERSION
from .registry import STATUSES, DsmRegistry, dsm_registry

GROUP_ORDER = ["depressive", "elevated", "anxiety", "social", "impairment", "exclusion"]
RETRIEVAL_K = (4, 3, 2, 1)
FEATURE_OUTPUT, RISK_OUTPUT, BASE_OUTPUT, MIN_OUTPUT = 190, 300, 150, 320
FREQUENCIES = ["nearly every day", "more days than not", "most of the day", "occasional", "unclear"]
TIME_FRAMES = ["current_episode", "past_episode", "unclear"]
DEGREES = ["marked", "clinically_significant"]


def chunk_output(tasks: list[dict]) -> int:
    return max(MIN_OUTPUT, BASE_OUTPUT + sum(RISK_OUTPUT if t["kind"] == "risk" else FEATURE_OUTPUT for t in tasks))


def plan_chunks(runtime, payload, tasks: list[dict], system: str) -> list[list[dict]] | None:
    chunks, current = [], []
    for task in tasks:
        candidate = current + [task]
        if runtime.fits(system, payload(candidate), chunk_output(candidate)):
            current = candidate
            continue
        if not current or not runtime.fits(system, payload([task]), chunk_output([task])):
            return None
        chunks.append(current)
        current = [task]
    if current:
        chunks.append(current)
    return chunks


def group_tasks(reg: DsmRegistry, group: str) -> list[dict]:
    tasks = [{"id": f"feature:{f.key}", "text": f.key, "kind": "feature", "key": f.key} for f in reg.features.features if f.group == group]
    if group == "exclusion":
        tasks.append({"id": "risk", "text": "risk", "kind": "risk", "key": "risk"})
    return tasks


def feature_brief(reg: DsmRegistry, key: str) -> dict:
    feat = reg.feature(key)
    items = [{"id": i.id, "paraphrase": i.paraphrase, **({"notes": i.notes} if i.notes else {})} for i in reg.items_for_feature(key)]
    return {"key": key, "label": feat.label, "definition": feat.definition, "criterion_items": items,
            **({"qualifiers": feat.qualifiers} if feat.qualifiers else {}), **({"degree": DEGREES} if feat.degree else {})}


def requested(reg: DsmRegistry, tasks: list[dict]) -> dict:
    out = {"features": [feature_brief(reg, t["key"]) for t in tasks if t["kind"] == "feature"]}
    if any(t["kind"] == "risk" for t in tasks):
        out["risk_statements"] = [{"key": r["key"], "definition": r["definition"]} for r in reg.risk["flags"] + reg.risk["supporting"]]
    return out


def group_queries(reg: DsmRegistry, tasks: list[dict]) -> list[str]:
    queries = []
    for t in tasks:
        if t["kind"] == "feature":
            feat = reg.feature(t["key"])
            queries += feat.queries + feat.counter_queries
        elif t["kind"] == "risk":
            queries += reg.risk["queries"]
    return list(dict.fromkeys(queries))


def response_schema(data: dict) -> dict:
    reg = dsm_registry()
    aliases = [r["id"] for r in data["sources"]] or ["T0"]
    refs = {"type": "array", "maxItems": 6, "items": {"type": "string", "enum": aliases}}
    text = lambda n: {"type": "string", "maxLength": n}  # noqa: E731
    props, required = {}, []
    features = {}
    for f in data["requested"].get("features", []):
        feat = reg.feature(f["key"])
        nullable = lambda values: {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]} if values else {"type": "null"}  # noqa: E731
        features[f["key"]] = {"type": "object", "additionalProperties": False,
                              "required": ["status", "who", "frequency", "onset_or_duration", "time_frame", "period_label", "qualifier", "degree",
                                           "contexts", "refs", "counter_refs", "counter_check", "basis"],
                              "properties": {"status": {"type": "string", "enum": list(STATUSES)},
                                             "who": {"type": "string", "enum": ["client_report", "clinician_observation"]},
                                             "frequency": {"type": "string", "enum": FREQUENCIES}, "onset_or_duration": text(80),
                                             "time_frame": {"type": "string", "enum": TIME_FRAMES}, "period_label": text(60),
                                             "qualifier": nullable(feat.qualifiers), "degree": nullable(DEGREES if feat.degree else []),
                                             "contexts": {"type": "array", "maxItems": 3, "items": {"type": "string", "enum": reg.features.contexts}},
                                             "refs": refs,
                                             "counter_refs": {"type": "array", "maxItems": 4, "items": {"type": "object", "additionalProperties": False,
                                                              "required": ["ref", "kind"], "properties": {"ref": {"type": "string", "enum": aliases},
                                                                                                         "kind": {"type": "string", "enum": ["denial", "alternative"]}}}},
                                             "counter_check": {"type": "object", "additionalProperties": False, "required": ["searched", "alternative_explanation"],
                                                               "properties": {"searched": {"type": "boolean"}, "alternative_explanation": text(120)}},
                                             "basis": text(160)}}
    if features:
        props["features"] = {"type": "object", "additionalProperties": False, "required": list(features), "properties": features}
        required.append("features")
    if data["requested"].get("risk_statements"):
        cats = [r["key"] for r in reg.risk["flags"] + reg.risk["supporting"]]
        props["risk_statements"] = {"type": "array", "maxItems": 10, "items": {"type": "object", "additionalProperties": False, "required": ["category", "refs", "basis"],
                                    "properties": {"category": {"type": "string", "enum": cats}, "refs": {**refs, "minItems": 1}, "basis": text(200)}}}
        required.append("risk_statements")
    props["limitations"] = {"type": "array", "maxItems": 4, "items": text(160)}
    required.append("limitations")
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


V1_STATUS = {"present": "rated", "subthreshold": "rated", "explicit_denial": "explicit_denial", "conflicting": "conflicting"}


def validate_findings(value, data: dict, checker: Checker) -> dict:
    """Structural problems raise (so Recovery repairs once); content problems withhold only that feature."""
    reg = dsm_registry()
    if not isinstance(value, dict):
        raise GenerationError("Invalid output schema: object required")
    keys = [f["key"] for f in data["requested"].get("features", [])]
    out = {"findings": {}, "risk_statements": []}
    if keys:
        given = value.get("features")
        if not isinstance(given, dict) or set(keys) - set(given):
            raise GenerationError("Invalid output schema: every requested feature required")
        for key in keys:
            item = given[key]
            if not isinstance(item, dict) or item.get("status") not in STATUSES:
                checker.withhold("feature", key, "invalid status", item)
                continue
            status = item["status"]
            sources = checker.refs("feature", key, item.get("refs", []))
            if sources is None:
                continue
            if status != "insufficient_evidence" and not sources:
                checker.withhold("feature", key, f"{status} needs cited turns", item)
                continue
            who = item.get("who") or "client_report"
            if status in V1_STATUS and not checker.attributable("feature", key, V1_STATUS[status], who, sources):
                continue
            if status == "absent" and not sources:
                continue
            counter_check = item.get("counter_check") if isinstance(item.get("counter_check"), dict) else {}
            if status == "present" and not counter_check.get("searched"):
                checker.withhold("feature", key, "a present finding needs the counter-evidence check", item)
                continue
            counter = []
            for c in item.get("counter_refs") or []:
                if not isinstance(c, dict) or c.get("ref") not in checker.aliases:
                    checker.limitations.append(f"{key}: dropped counter-evidence that cites an unsupplied turn")
                    continue
                row = checker.aliases[c["ref"]]
                kind = c.get("kind") if c.get("kind") in {"denial", "alternative"} else "alternative"
                if kind == "denial" and row["speaker_role"] != "CLIENT":
                    kind = "alternative"  # only the client can deny their own experience
                counter.append({"segment_id": row["id"], "kind": kind, "basis": ""})
            feat = reg.feature(key)
            qualifier = item.get("qualifier") if item.get("qualifier") in feat.qualifiers else None
            degree = item.get("degree") if feat.degree and item.get("degree") in DEGREES else None
            texts = [str(item.get(k) or "")[:300] for k in ("basis", "onset_or_duration", "period_label")] + [str(counter_check.get("alternative_explanation") or "")[:200]]
            if not checker.text_ok("feature", key, texts, sources):
                continue
            out["findings"][key] = {
                "status": status, "frequency": item.get("frequency") if item.get("frequency") in FREQUENCIES else "unclear",
                "onset_or_duration_text": texts[1], "time_frame": item.get("time_frame") if item.get("time_frame") in TIME_FRAMES else "unclear",
                "period_label": texts[2], "qualifier": qualifier, "degree": degree,
                "contexts": [c for c in item.get("contexts") or [] if c in reg.features.contexts][:3], "who": who,
                "evidence_segment_ids": [s["id"] for s in sources], "counter_evidence": list({c["segment_id"]: c for c in counter}.values()),
                "counter_check": {"searched": bool(counter_check.get("searched")), "found": [c["segment_id"] for c in counter],
                                  "alternative_explanation": texts[3]},
                "basis": texts[0]}
    if data["requested"].get("risk_statements"):
        cats = {r["key"]: "flag" for r in reg.risk["flags"]} | {r["key"]: "supporting" for r in reg.risk["supporting"]}
        given = value.get("risk_statements")
        if not isinstance(given, list):
            raise GenerationError("Invalid output schema: risk_statements required")
        for i, item in enumerate(given):
            if not isinstance(item, dict) or item.get("category") not in cats:
                checker.withhold("risk statement", str(i), "invalid category", item)
                continue
            sources = checker.refs("risk statement", str(i), item.get("refs", []))
            if not sources:
                if sources is not None:
                    checker.withhold("risk statement", str(i), "a flagged statement needs cited turns", item)
                continue
            basis = str(item.get("basis") or "")[:300]
            if not checker.text_ok("risk statement", str(i), [basis], sources):
                continue
            out["risk_statements"].append({"category": item["category"], "basis": basis, "segment_ids": [s["id"] for s in sources]})
    for note in value.get("limitations", []) if isinstance(value.get("limitations"), list) else []:
        if isinstance(note, str) and note.strip():
            checker.limitations.append("Model note: " + note.strip()[:200])
    return out


# ---------------------------------------------------------------- risk de-duplication

def _norm(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.casefold()))


def dedupe_risks(reg: DsmRegistry, flags: list[dict], rows: dict[str, dict]) -> list[dict]:
    """One statement per segment group: flags sharing a segment, or citing near-identical turns close in time (the same
    words captured twice by overlapping recordings), merge into one row listing every category."""
    order = [r["key"] for r in reg.risk["flags"] + reg.risk["supporting"]]
    flag_keys = {r["key"] for r in reg.risk["flags"]}
    groups: list[dict] = []

    def similar(a: str, b: str) -> bool:
        ra, rb = rows.get(a), rows.get(b)
        if not ra or not rb or abs(ra["start_ms"] - rb["start_ms"]) > 30_000:
            return False
        wa, wb = _norm(ra["text"]), _norm(rb["text"])
        return bool(wa and wb) and (len(wa & wb) / len(wa | wb) >= 0.6 or wa <= wb or wb <= wa)

    for flag in flags:
        target = next((g for g in groups if set(g["segment_ids"]) & set(flag["segment_ids"])
                       or any(similar(a, b) for a in g["segment_ids"] for b in flag["segment_ids"])), None)
        if target is None:
            groups.append({"segment_ids": list(flag["segment_ids"]), "categories": [flag["category"]], "bases": [flag["basis"]]})
            continue
        target["segment_ids"] = list(dict.fromkeys(target["segment_ids"] + flag["segment_ids"]))
        target["categories"] = list(dict.fromkeys(target["categories"] + [flag["category"]]))
        if flag["basis"] and flag["basis"] not in target["bases"]:
            target["bases"].append(flag["basis"])
    out = []
    for g in groups:
        cats = sorted(g["categories"], key=order.index)
        flagged = [c for c in cats if c in flag_keys]
        out.append({"categories": flagged + [c for c in cats if c not in flag_keys], "note_kind": "flag" if flagged else "supporting",
                    "segment_ids": sorted(g["segment_ids"], key=lambda s: rows.get(s, {}).get("start_ms", 0)), "basis": " ".join(g["bases"])[:600]})
    return out


# ---------------------------------------------------------------- Stage A

def _packet(runtime, system, tasks, payload_for, full_rows, snapshot, aliases, by_id, queries, retriever, scope, evidence_mode):
    plan = plan_chunks(runtime, payload_for(full_rows), tasks, system) if evidence_mode in {"auto", "full"} else None
    if plan is not None:
        return full_rows, plan, "full"
    if evidence_mode == "full":
        return [], None, "full"
    rows = []
    for k in RETRIEVAL_K:
        ids = retriever.select(snapshot["session_id"], snapshot["segments"], queries, k, scope=scope)
        rows = [model_row(by_id[i], aliases[by_id[i]]) for i in ids]
        plan = plan_chunks(runtime, payload_for(rows), tasks, system)
        if plan is not None:
            return rows, plan, "retrieval"
    return rows, None, "retrieval"


def extract_session_dsm(snapshot: dict, runtime, folder, *, evidence_mode: str = "auto", plans: list[dict] | None = None,
                        names: list[str] | None = None, progress=lambda s: None, scope=None, retriever: Retriever | None = None) -> dict:
    """Run DSM Stage A for one session snapshot {'session_id','revision','segments':[...]}. Returns the validated result."""
    if evidence_mode not in {"auto", "full", "retrieval"}:
        raise GenerationError("evidence_mode must be auto, full or retrieval")
    segments = snapshot["segments"]
    if not segments:
        raise GenerationError("Transcript is empty")
    if digest(segments) != snapshot["revision"]:
        raise GenerationError("Snapshot revision mismatch")
    reg = dsm_registry()
    folder = Path(folder)
    log = trace_for(runtime, folder)
    started = time.monotonic()
    aliases = aliases_for(segments)
    by_id = {row["id"]: alias for alias, row in aliases.items()}
    full_rows = [model_row(a, r) for a, r in aliases.items()]
    retriever = retriever or Retriever()
    result = {"version": EXTRACT_DSM_VERSION, "schema_version": EXTRACT_DSM_SCHEMA_VERSION, "registry_version": reg.tag,
              "session_id": snapshot["session_id"], "revision": snapshot["revision"], "evidence_mode": evidence_mode,
              "findings": {}, "risk_statements": [], "phq_items": {}, "gad7_items": {}, "plan_followups": [],
              "limitations": [], "withheld": [], "groups": {}}
    runtime.load("qwen")
    log.event("progress.dsm_extraction.start", session=snapshot["session_id"], turns=len(segments), version=EXTRACT_DSM_VERSION)
    raw_risks = []
    for group in GROUP_ORDER:
        tasks = group_tasks(reg, group)
        progress(f"extracting DSM findings: {group}")

        def payload_for(chosen):
            return lambda chunk: {"sources": chosen, "requested": requested(reg, chunk)}
        rows, plan, mode = _packet(runtime, EXTRACT_DSM, tasks, payload_for, full_rows, snapshot, aliases, by_id,
                                   group_queries(reg, tasks), retriever, scope, evidence_mode)
        if plan is None:
            result["groups"][group] = {"status": "failed" if mode == "retrieval" else "ineligible", "evidence": mode,
                                       "error": "Evidence does not fit the context budget"}
            result["limitations"].append(f"{group}: evidence does not fit the {runtime.context}-token budget; not extracted")
            continue
        payload = payload_for(rows)
        allowed = {r["id"]: {**aliases[r["id"]], "alias": r["id"]} for r in rows}
        checker = Checker(allowed, names)
        recovery = Recovery(runtime, folder, snapshot["revision"], EXTRACT_DSM_VERSION, progress, schema_factory=response_schema)
        try:
            chunks = []
            for planned in plan:
                chunks += recovery.process(EXTRACT_DSM, planned, payload, lambda v, d: validate_findings(v, d, Checker(allowed, names)),
                                           output=chunk_output(planned), label=f"progress dsm {group}")
        except GenerationError as exc:
            if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
                raise
            result["groups"][group] = {"status": "failed", "evidence": mode, "error": str(exc)[:500]}
            result["limitations"].append(f"{group}: extraction incomplete ({str(exc)[:200]})")
            continue
        for chunk, value in chunks:
            accepted = validate_findings(value, payload(chunk), checker)
            result["findings"].update(accepted["findings"])
            raw_risks += accepted["risk_statements"]
        result["limitations"] += checker.limitations
        result["withheld"] += checker.withheld
        result["groups"][group] = {"status": "completed", "evidence": mode, "source_turns": len(rows), "planned_chunks": len(plan), "stats": recovery.stats}
    result["risk_statements"] = dedupe_risks(reg, raw_risks, {r["id"]: r for r in segments})
    _instruments_and_plans(result, snapshot, runtime, folder, aliases, by_id, full_rows, plans or [], names, progress, scope, retriever, evidence_mode)
    result["limitations"] = list(dict.fromkeys(result["limitations"]))
    result["seconds"] = round(time.monotonic() - started, 3)
    result["model_sha256"] = getattr(runtime, "model", {}).get("sha256")
    log.event("progress.dsm_extraction.completed", seconds=result["seconds"], findings=len(result["findings"]),
              withheld=len(result["withheld"]), risk_flags=len(result["risk_statements"]))
    return result


def _instruments_and_plans(result, snapshot, runtime, folder, aliases, by_id, full_rows, plans, names, progress, scope, retriever, evidence_mode):
    """PHQ-9 items 1-8, GAD-7 items 1-7 and plan follow-ups: separate calls with the unchanged v2 item prompt (user decision)."""
    reg = v1_registry()
    phq, gad = reg.instruments.get("phq9"), reg.instruments.get("gad7")
    tasks = ([{"id": f"phq9:{i}", "text": i, "kind": "phq9", "key": i, "label": phq.item(i).label} for i in phq.ai_estimable_items]
             + [{"id": f"gad7:{i}", "text": i, "kind": "gad7", "key": i, "label": gad.item(i).label} for i in gad.ai_estimable_items]
             + [{"id": f"plan:{p['plan_id']}", "text": p["plan_id"], "kind": "plan", "key": p["plan_id"], "label": p["text"]} for p in plans])
    progress("estimating questionnaire items")

    def payload_for(chosen):
        return lambda chunk: {"sources": chosen, "requested": v1x.requested(chunk), "rating_scale": reg.domains.rating_scale}
    queries = v1x.group_queries([t for t in tasks if t["kind"] != "plan"]) + ([PLAN_QUERY] if plans else [])
    plan = v1x.plan_chunks(runtime, payload_for(full_rows), tasks, EXTRACT_V2) if evidence_mode in {"auto", "full"} else None
    rows, mode = full_rows, "full"
    if plan is None:
        mode = "retrieval"
        for k in v1x.RETRIEVAL_K:
            ids = retriever.select(snapshot["session_id"], snapshot["segments"], queries, k, scope=scope)
            rows = [model_row(by_id[i], aliases[by_id[i]]) for i in ids]
            plan = v1x.plan_chunks(runtime, payload_for(rows), tasks, EXTRACT_V2)
            if plan is not None:
                break
    if plan is None:
        result["groups"]["questionnaires"] = {"status": "failed", "evidence": mode, "error": "Evidence does not fit the context budget"}
        result["limitations"].append("questionnaires: evidence does not fit the context budget; not estimated")
        return
    payload = payload_for(rows)
    allowed = {r["id"]: {**aliases[r["id"]], "alias": r["id"]} for r in rows}
    checker = Checker(allowed, names)
    recovery = Recovery(runtime, folder, snapshot["revision"], EXTRACT_V2_VERSION, progress, schema_factory=lambda data: v1x.response_schema(data, "v2"))
    try:
        chunks = []
        for planned in plan:
            chunks += recovery.process(EXTRACT_V2, planned, payload,
                                       lambda v, d: validate_extraction(v, {"tasks": v1x.validation_tasks(d)}, Checker(allowed, names), version="v2"),
                                       output=v1x.chunk_output(planned), label="progress questionnaires")
    except GenerationError as exc:
        if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
            raise
        result["groups"]["questionnaires"] = {"status": "failed", "evidence": mode, "error": str(exc)[:500]}
        result["limitations"].append(f"questionnaires: estimation incomplete ({str(exc)[:200]})")
        return
    for chunk, value in chunks:
        accepted = validate_extraction(value, {"tasks": v1x.validation_tasks(payload(chunk))}, checker, version="v2")
        result["phq_items"].update(accepted["phq_items"])
        result["gad7_items"].update(accepted["gad7_items"])
        result["plan_followups"] += accepted["plan_followups"]
    result["limitations"] += checker.limitations
    result["withheld"] += checker.withheld
    result["groups"]["questionnaires"] = {"status": "completed", "evidence": mode, "source_turns": len(rows), "planned_chunks": len(plan),
                                          "version": EXTRACT_V2_VERSION, "schema_version": EXTRACT_V2_SCHEMA_VERSION}

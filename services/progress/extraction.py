"""Stage A: per-session extraction of domain observations, questionnaire item estimates, risk statements and
plan follow-ups. DB-free: a transcript snapshot in, a validated result out.

Reuses the note-generation Runtime (budgeting, finish-reason checks, truncation discard) and Recovery (checkpoint
cache, one repair retry, output-limit retry and splitting). Tasks, not transcript turns, are the split unit, so a
truncated response splits the task group and never splices partial output.
"""
from __future__ import annotations

import time
from pathlib import Path

from services.note_generation.recovery import Recovery
from services.note_generation.runtime import GenerationError
from services.note_generation.terminal import trace_for
from services.retrieval.engine import digest

from .evidence import PLAN_QUERY, RISK_QUERY, Retriever, aliases_for, model_row
import os

from .prompts import (EXTRACT, EXTRACT_SCHEMA_VERSION, EXTRACT_V2, EXTRACT_V2_ATTEMPT1, EXTRACT_V2_ATTEMPT1_VERSION, EXTRACT_V2_SCHEMA_VERSION,
                      EXTRACT_V2_VERSION, EXTRACT_VERSION)
from .registry import registry
from .validation import Checker, validate_extraction

GROUP_ORDER = ["depressive", "manic", "anxiety", "functioning"]
PROMPTS = {"v1": (EXTRACT, EXTRACT_VERSION, EXTRACT_SCHEMA_VERSION), "v2": (EXTRACT_V2, EXTRACT_V2_VERSION, EXTRACT_V2_SCHEMA_VERSION),
           "v2-attempt1": (EXTRACT_V2_ATTEMPT1, EXTRACT_V2_ATTEMPT1_VERSION, EXTRACT_V2_SCHEMA_VERSION)}
SHORT_FIELDS = {"v2"}  # revision 1 shortened basis fields (cost); attempt 1 kept its original lengths


def default_prompt() -> str:
    """v2 unless PROGRESS_EXTRACT_PROMPT selects v1 (kept for reproducing earlier runs)."""
    value = os.getenv("PROGRESS_EXTRACT_PROMPT", "v2")
    if value not in PROMPTS:
        raise GenerationError("PROGRESS_EXTRACT_PROMPT must be v1, v2 or v2-attempt1")
    return value
RETRIEVAL_K = (5, 3, 2, 1)
# Output reserved per task (JSON with refs and a one-sentence basis); a chunk reserves base + its tasks. Planning
# chunks this way lets a full 40-turn transcript (~3k tokens) fit the 4,096-token context with fewer tasks per call;
# the identical source prefix is reused by llama.cpp's prompt cache across chunks.
# Calibrated on the pilot (Qwen3-4B on LongCounsel-8): ~95-reserved chunks hit the limit, observed ~120-140 per domain.
TASK_OUTPUT = {"domain": 140, "phq9": 70, "gad7": 70, "risk": 260, "plan": 90}
BASE_OUTPUT, MIN_CHUNK_OUTPUT = 150, 320


def chunk_output(tasks: list[dict]) -> int:
    return max(MIN_CHUNK_OUTPUT, BASE_OUTPUT + sum(TASK_OUTPUT[t["kind"]] for t in tasks))


def plan_chunks(runtime, payload, tasks: list[dict], system: str = EXTRACT) -> list[list[dict]] | None:
    """Greedy, order-preserving task chunks that fit prompt + reserved output; None if one task alone cannot fit."""
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


def group_tasks(group: str, plans: list[dict]) -> list[dict]:
    reg = registry()
    tasks = [{"id": f"domain:{d.key}", "text": d.key, "kind": "domain", "key": d.key, "label": d.label, "directions": d.directions}
             for d in reg.domains.by_group(group)]
    if group == "depressive":
        phq = reg.instruments.get("phq9")
        tasks += [{"id": f"phq9:{i}", "text": i, "kind": "phq9", "key": i, "label": phq.item(i).label} for i in phq.ai_estimable_items]
    if group == "anxiety":
        gad = reg.instruments.get("gad7")
        tasks += [{"id": f"gad7:{i}", "text": i, "kind": "gad7", "key": i, "label": gad.item(i).label} for i in gad.ai_estimable_items]
    if group == "functioning":
        tasks.append({"id": "risk", "text": "risk", "kind": "risk", "key": "risk"})
        tasks += [{"id": f"plan:{p['plan_id']}", "text": p["plan_id"], "kind": "plan", "key": p["plan_id"], "label": p["text"]} for p in plans]
    return tasks


def requested(tasks: list[dict]) -> dict:
    out: dict = {}
    domains = [{"key": t["key"], "label": t["label"], **({"directions": t["directions"]} if t["directions"] else {})} for t in tasks if t["kind"] == "domain"]
    if domains:
        out["domains"] = domains
    for kind, field in (("phq9", "phq_items"), ("gad7", "gad7_items")):
        rows = [{"id": t["key"], "label": t["label"]} for t in tasks if t["kind"] == kind]
        if rows:
            out[field] = rows
    if any(t["kind"] == "risk" for t in tasks):
        out["risk_statements"] = True
    plans = [{"plan_id": t["key"], "text": t["label"]} for t in tasks if t["kind"] == "plan"]
    if plans:
        out["plans"] = plans
    return out


def response_schema(data: dict, prompt: str = "v1") -> dict:
    """llama.cpp grammar: refs restricted to the supplied aliases; requested keys required."""
    short = prompt in SHORT_FIELDS
    aliases = [r["id"] for r in data["sources"]]
    ref_list = {"type": "array", "items": {"type": "string", "enum": aliases or ["T0"]}, "maxItems": 8}
    text = lambda n: {"type": "string", "maxLength": n}  # noqa: E731
    nullable_rating = {"anyOf": [{"type": "integer", "minimum": 0, "maximum": 3}, {"type": "null"}]}
    req = data["requested"]
    props, required = {}, []
    if "domains" in req:
        domains = {}
        for d in req["domains"]:
            direction = {"anyOf": [{"type": "string", "enum": d["directions"]}, {"type": "null"}]} if d.get("directions") else {"type": "null"}
            domains[d["key"]] = {"type": "object", "additionalProperties": False,
                                 "required": ["status", "rating", "who", "timeframe", "direction", "frequency", "intensity", "duration", "refs", "basis"],
                                 "properties": {"status": {"type": "string", "enum": ["rated", "explicit_denial", "conflicting", "insufficient_evidence"]},
                                                "rating": nullable_rating, "who": {"type": "string", "enum": ["client_report", "clinician_observation"]},
                                                "timeframe": {"type": "string", "enum": ["current", "past", "unclear"]}, "direction": direction,
                                                "frequency": text(80), "intensity": text(80), "duration": text(80), "refs": ref_list,
                                                "basis": text(160 if short else 240)}}
        props["domains"] = {"type": "object", "additionalProperties": False, "required": list(domains), "properties": domains}
        required.append("domains")
    v2 = prompt.startswith("v2")
    for field in ("phq_items", "gad7_items"):
        if field in req:
            if v2:
                item = {"type": "object", "additionalProperties": False, "required": ["status", "score", "evidence_strength", "refs", "basis"],
                        "properties": {"status": {"type": "string", "enum": ["rated", "explicit_denial", "insufficient_evidence"]}, "score": nullable_rating,
                                       "evidence_strength": {"anyOf": [{"type": "string", "enum": ["direct", "indirect"]}, {"type": "null"}]},
                                       "refs": ref_list, "basis": text(140 if short else 200)}}
            else:
                item = {"type": "object", "additionalProperties": False, "required": ["status", "score", "refs", "basis"],
                        "properties": {"status": {"type": "string", "enum": ["rated", "insufficient_evidence"]}, "score": nullable_rating,
                                       "refs": ref_list, "basis": text(200)}}
            items = {i["id"]: item for i in req[field]}
            props[field] = {"type": "object", "additionalProperties": False, "required": list(items), "properties": items}
            required.append(field)
    if req.get("risk_statements"):
        props["risk_statements"] = {"type": "array", "maxItems": 10, "items": {"type": "object", "additionalProperties": False, "required": ["category", "refs", "basis"],
                                    "properties": {"category": {"type": "string", "enum": ["self_harm", "suicidal_ideation", "harm_to_others", "other_safety"]},
                                                   "refs": {**ref_list, "minItems": 1}, "basis": text(200)}}}
        required.append("risk_statements")
    if "plans" in req:
        props["plan_followups"] = {"type": "array", "maxItems": len(req["plans"]), "items": {"type": "object", "additionalProperties": False,
                                   "required": ["plan_id", "status", "refs", "basis"],
                                   "properties": {"plan_id": {"type": "string", "enum": [p["plan_id"] for p in req["plans"]]},
                                                  "status": {"type": "string", "enum": ["discussed_done", "discussed_partial", "discussed_not_done", "not_discussed"]},
                                                  "refs": ref_list, "basis": text(200)}}}
        required.append("plan_followups")
    props["limitations"] = {"type": "array", "maxItems": 4, "items": text(160)}
    required.append("limitations")
    return {"type": "object", "additionalProperties": False, "required": required, "properties": props}


def validation_tasks(data: dict) -> list[dict]:
    req = data["requested"]
    tasks = [{"kind": "domain", "key": d["key"], "directions": d.get("directions", [])} for d in req.get("domains", [])]
    tasks += [{"kind": "phq9", "key": i["id"]} for i in req.get("phq_items", [])]
    tasks += [{"kind": "gad7", "key": i["id"]} for i in req.get("gad7_items", [])]
    if req.get("risk_statements"):
        tasks.append({"kind": "risk", "key": "risk"})
    tasks += [{"kind": "plan", "key": p["plan_id"]} for p in req.get("plans", [])]
    return tasks


def group_queries(tasks: list[dict]) -> list[str]:
    reg = registry()
    queries = [q for t in tasks if t["kind"] == "domain" for q in reg.domains.get(t["key"]).queries]
    for t in tasks:
        if t["kind"] in {"phq9", "gad7"}:
            domain = reg.instruments.get(t["kind"]).item(t["key"]).domain
            if domain:
                queries += reg.domains.get(domain).queries
        elif t["kind"] == "risk":
            queries.append(RISK_QUERY)
        elif t["kind"] == "plan":
            queries += [PLAN_QUERY, " ".join(t["label"].split()[:20])]
    return list(dict.fromkeys(queries))


def extract_session(snapshot: dict, runtime, folder, *, evidence_mode: str = "auto", plans: list[dict] | None = None,
                    names: list[str] | None = None, progress=lambda s: None, scope=None, retriever: Retriever | None = None,
                    prompt: str | None = None) -> dict:
    """Run Stage A for one session snapshot {'session_id','revision','segments':[...]}. Returns the validated result."""
    if evidence_mode not in {"auto", "full", "retrieval"}:
        raise GenerationError("evidence_mode must be auto, full or retrieval")
    prompt = prompt or default_prompt()
    if prompt not in PROMPTS:
        raise GenerationError("prompt must be v1, v2 or v2-attempt1")
    system, version, schema_version = PROMPTS[prompt]
    segments = snapshot["segments"]
    if not segments:
        raise GenerationError("Transcript is empty")
    if digest(segments) != snapshot["revision"]:
        raise GenerationError("Snapshot revision mismatch")
    folder = Path(folder)
    log = trace_for(runtime, folder)
    started = time.monotonic()
    plans = plans or []
    aliases = aliases_for(segments)
    by_id = {row["id"]: alias for alias, row in aliases.items()}
    full_rows = [model_row(a, r) for a, r in aliases.items()]
    reg = registry()
    rating_scale = reg.domains.rating_scale
    retriever = retriever or Retriever()
    result = {"version": version, "schema_version": schema_version, "prompt": prompt, "registry_version": reg.combined_tag,
              "session_id": snapshot["session_id"], "revision": snapshot["revision"], "evidence_mode": evidence_mode,
              "domains": {}, "phq_items": {}, "gad7_items": {}, "risk_statements": [], "plan_followups": [],
              "limitations": [], "withheld": [], "groups": {}}
    runtime.load("qwen")
    log.event("progress.extraction.start", session=snapshot["session_id"], turns=len(segments), evidence_mode=evidence_mode, version=version)
    for group in GROUP_ORDER:
        tasks = group_tasks(group, plans)
        progress(f"extracting {group}")
        rows = full_rows
        mode_used = "full"

        def payload_for(chosen_rows):
            def payload(task_chunk):
                return {"sources": chosen_rows, "requested": requested(task_chunk), "rating_scale": rating_scale}
            return payload
        plan = plan_chunks(runtime, payload_for(full_rows), tasks, system) if evidence_mode in {"auto", "full"} else None
        if evidence_mode == "full" and plan is None:
            result["groups"][group] = {"status": "ineligible", "evidence": "full", "error": "Full transcript does not fit the context budget even one task at a time"}
            result["limitations"].append(f"{group}: full transcript does not fit the {runtime.context}-token budget; not extracted")
            continue
        if plan is None:
            mode_used = "retrieval"
            queries = group_queries(tasks)
            for k in RETRIEVAL_K:
                ids = retriever.select(snapshot["session_id"], segments, queries, k, scope=scope)
                rows = [model_row(by_id[i], aliases[by_id[i]]) for i in ids]
                plan = plan_chunks(runtime, payload_for(rows), tasks, system)
                if plan is not None:
                    break
            if not rows:
                result["limitations"].append(f"{group}: retrieval found no candidate turns; all {group} values insufficient by default")
            if plan is None:
                result["groups"][group] = {"status": "failed", "evidence": "retrieval", "error": "Retrieved evidence does not fit the context budget"}
                result["limitations"].append(f"{group}: retrieved evidence does not fit the {runtime.context}-token budget; not extracted")
                continue
        payload = payload_for(rows)
        allowed = {r["id"]: {**aliases[r["id"]], "alias": r["id"]} for r in rows}
        checker = Checker(allowed, names)
        recovery = Recovery(runtime, folder, snapshot["revision"], version, progress, schema_factory=lambda data: response_schema(data, prompt))

        def validate(value, data):
            validate_extraction(value, {"tasks": validation_tasks(data)}, Checker(allowed, names), version="v2" if prompt.startswith("v2") else "v1")

        try:
            chunks = []
            for planned in plan:
                chunks += recovery.process(system, planned, payload, validate, output=chunk_output(planned), label=f"progress {group}")
        except GenerationError as exc:
            if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
                raise  # a cancelled job is never recorded (or cached) as a completed extraction
            result["groups"][group] = {"status": "failed", "evidence": mode_used, "error": str(exc)[:500]}
            result["limitations"].append(f"{group}: extraction incomplete ({str(exc)[:200]})")
            log.event("progress.group_failed", group=group, error=str(exc)[:300])
            continue
        for task_chunk, value in chunks:
            accepted = validate_extraction(value, {"tasks": validation_tasks(payload(task_chunk))}, checker, version="v2" if prompt.startswith("v2") else "v1")
            result["domains"].update(accepted["domains"])
            result["phq_items"].update(accepted["phq_items"])
            result["gad7_items"].update(accepted["gad7_items"])
            result["risk_statements"] += accepted["risk_statements"]
            result["plan_followups"] += accepted["plan_followups"]
        result["limitations"] += checker.limitations
        result["withheld"] += checker.withheld
        result["groups"][group] = {"status": "completed", "evidence": mode_used, "source_turns": len(rows), "planned_chunks": len(plan), "stats": recovery.stats}
        log.event("progress.group_completed", group=group, evidence=mode_used, source_turns=len(rows),
                  domains=sum(1 for t in tasks if t["kind"] == "domain"), withheld=len(checker.withheld))
    # Deduplicate risk statements flagged in several chunks.
    seen, risks = set(), []
    for r in result["risk_statements"]:
        key = (r["category"], tuple(sorted(r["segment_ids"])))
        if key not in seen:
            seen.add(key)
            risks.append(r)
    result["risk_statements"] = risks
    result["limitations"] = list(dict.fromkeys(result["limitations"]))
    result["seconds"] = round(time.monotonic() - started, 3)
    result["model_sha256"] = getattr(runtime, "model", {}).get("sha256")
    log.event("progress.extraction.completed", seconds=result["seconds"], domains=len(result["domains"]),
              withheld=len(result["withheld"]), risk_flags=len(result["risk_statements"]))
    return result

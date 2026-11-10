"""Stage A, fast path (``progress-extract-dsm-fast-v1``): whole transcript in cached windows, one call per feature group.

Per session:
1. **Turns** become compact lines ``<id> <C|T|U>: text`` (id = transcript position, the same number as the ``T<n>``
   alias, so the v1 ``Checker`` and all guards are reused unchanged).
2. **Windows.** Consecutive turns that fit the context with the static system block and the largest output
   reservation; consecutive windows overlap by a few turns. Every turn is in at least one window (plan §4.1, Q3).
3. **Calls.** Per window: the risk + plans call (never gated), then one call per feature group that triage did not
   skip. The prompt prefix (system block + the window's turns) is identical for every call of a window, so the server's
   prompt cache processes only the short task line after the first call.
4. **Validation.** Each compact row is mapped to the ``dsm-v1`` finding shape and checked by ``validate_findings``
   (aliases and foreign ids, attribution, other-person guard, copy and name guards, uncited absence → gap).
5. **Merge** rows and windows per feature in code (§4.5), de-duplicate risk statements, derive questionnaires.

Every model call is logged by ``FastRuntime`` (``calls.jsonl``). DB-free: a snapshot in, a result out.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from services.note_generation.runtime import GenerationError, OutputLimitError
from services.note_generation.storage import write
from services.note_generation.terminal import trace_for
from services.retrieval.engine import digest

from services.progress.evidence import aliases_for
from services.progress.validation import Checker

from ..extraction import FREQUENCIES, dedupe_risks, validate_findings
from ..registry import dsm_registry
from .config import fast_config, group_features
from .prompts import SCHEMA_VERSION, VERSION, group_schema, group_task, risk_check_task, risk_schema, risk_task, system_prompt
from .questionnaires import derive
from .runtime import CallLog
from .triage import triage

ROLE = {"CLIENT": "C", "CLINICIAN": "T"}
STATUS_RANK = {"conflicting": 5, "present": 4, "subthreshold": 3, "explicit_denial": 2, "absent": 1, "insufficient_evidence": 0}
FREQ_RANK = {"nearly every day": 4, "more days than not": 3, "most of the day": 2, "occasional": 1, "unclear": 0}
MAX_EVIDENCE = 6


def turn_line(i: int, seg: dict) -> str:
    return f"{i} {ROLE.get(seg['speaker_role'], 'U')}: {' '.join((seg.get('text') or '').split())}"


def output_for(group: str | None, n_plans: int = 0, n_features: int | None = None) -> int:
    o = fast_config()["output"]
    if group is None:
        return min(o["max"], o["risk_base"] + o["risk_per_plan"] * n_plans)
    n = n_features if n_features is not None else min(o["codes_per_call"], len(group_features(group)))
    return min(o["max"], o["base"] + o["per_feature"] * n)


def code_chunks(group: str) -> list[list[str]]:
    """The group's codes in calls of at most ``codes_per_call`` (balanced), so one answer never runs long."""
    codes = [f["code"] for f in group_features(group)]
    size = fast_config()["output"]["codes_per_call"]
    n = -(-len(codes) // size)
    return [codes[i * len(codes) // n:(i + 1) * len(codes) // n] for i in range(n)]


def plan_windows(runtime, lines: list[str], system: str, output: int, longest_task: str) -> list[tuple[int, int]]:
    """[(start, end)) windows of turn indices that fit; consecutive windows overlap by ``overlap_turns``."""
    cfg = fast_config()["window"]
    base = runtime.count(runtime.prompt(system, {"turns": [], "task": longest_task}))
    budget = runtime.context - runtime.safety_margin - output - base
    if budget < 64:
        raise GenerationError("The fast pipeline's system block leaves no room for transcript turns")
    sizes = [runtime.count(json.dumps(line, ensure_ascii=False)) + 1 for line in lines]
    windows, start = [], 0
    while start < len(lines):
        end, used = start, 0
        while end < len(lines) and used + sizes[end] <= min(budget, cfg["max_turn_tokens"]):
            used += sizes[end]
            end += 1
        if end == start:
            raise GenerationError(f"Turn {start} alone does not fit the context budget")
        while end > start + 1 and not runtime.fits(system, {"turns": lines[start:end], "task": longest_task}, output):
            end -= 1  # the estimate was optimistic: shrink until the real prompt fits
        windows.append((start, end))
        if end >= len(lines):
            break
        start = max(start + 1, end - cfg["overlap_turns"])
    return windows


# ---------------------------------------------------------------- rows → dsm-v1 findings

def _alias(i: int) -> str:
    return f"T{i}"


def row_to_item(row: dict, segments: list[dict]) -> tuple[str, dict]:
    cfg, reg = fast_config(), dsm_registry()
    feat = cfg["by_code"][row["k"]]
    key = feat["key"]
    refs = [_alias(i) for i in dict.fromkeys(row.get("e") or []) if isinstance(i, int)]
    roles = [segments[i]["speaker_role"] for i in dict.fromkeys(row.get("e") or []) if isinstance(i, int) and 0 <= i < len(segments)]
    who = "clinician_observation" if roles and all(r == "CLINICIAN" for r in roles) else "client_report"
    counter = [{"ref": _alias(i), "kind": "denial"} for i in row.get("x") or [] if isinstance(i, int)]
    counter += [{"ref": _alias(i), "kind": "alternative"} for i in row.get("a") or [] if isinstance(i, int)]
    rf = reg.feature(key)
    qualifier = row.get("v") if row.get("v") in rf.qualifiers else None
    degree = cfg["degrees"].get(row.get("g")) if rf.degree else None
    return key, {"status": cfg["statuses"].get(row.get("s"), "insufficient_evidence"), "who": who,
                 "frequency": cfg["frequencies"].get(row.get("q"), "unclear"), "onset_or_duration": str(row.get("d") or "")[:80],
                 "time_frame": cfg["time_frames"].get(row.get("t"), "unclear"), "period_label": str(row.get("p") or "")[:60],
                 "qualifier": qualifier, "degree": degree, "contexts": [cfg["contexts"][c] for c in dict.fromkeys(row.get("c") or []) if c in cfg["contexts"]],
                 "refs": refs, "counter_refs": counter, "counter_check": {"searched": True, "alternative_explanation": ""},
                 "basis": str(row.get("b") or "")[:160]}


def validate_rows(value, segments: list[dict], allowed: dict[str, dict], names, codes: list[str]) -> tuple[list[tuple[str, dict]], Checker]:
    """Structural problems raise (one repair); each row is checked on its own and withheld on its own."""
    if not isinstance(value, dict) or not isinstance(value.get("f"), dict) or set(codes) - set(value["f"]):
        raise GenerationError("Invalid output schema: f must answer every requested code")
    checker = Checker(allowed, names)
    out = []
    for code, answer in value["f"].items():
        if code not in codes:
            checker.withhold("feature", str(code), "unrequested code", answer)
            continue
        if answer == "N" or (isinstance(answer, dict) and answer.get("s") == "N"):
            continue  # not discussed: recorded as a gap by the caller
        if not isinstance(answer, dict):
            checker.withhold("feature", str(code), "invalid answer", answer)
            continue
        key, item = row_to_item({**answer, "k": code}, segments)
        accepted = validate_findings({"features": {key: item}}, {"requested": {"features": [{"key": key}]}}, checker)
        if key in accepted["findings"]:
            f = accepted["findings"][key]
            f["counter_check"]["method"] = "single-pass-compact"
            out.append((key, f))
    return out, checker


def merge(a: dict | None, b: dict) -> dict:
    """Combine two findings for one feature (rows or windows of the same session)."""
    if a is None:
        return b
    sa, sb = a["status"], b["status"]
    positive, negative = {"present", "subthreshold"}, {"explicit_denial"}
    if "conflicting" in {sa, sb} or ({sa, sb} & positive and {sa, sb} & negative):
        status = "conflicting"
    else:
        status = sa if STATUS_RANK[sa] >= STATUS_RANK[sb] else sb
    win, other = (a, b) if STATUS_RANK[sa] >= STATUS_RANK[sb] else (b, a)
    counter = list({c["segment_id"]: c for c in a["counter_evidence"] + b["counter_evidence"]}.values())
    if status == "conflicting":
        for f in (a, b):
            if f["status"] == "explicit_denial":
                counter += [{"segment_id": s, "kind": "denial", "basis": ""} for s in f["evidence_segment_ids"] if s not in {c["segment_id"] for c in counter}]
    freq = max((f["frequency"] for f in (a, b) if f["status"] in positive | {"conflicting"}), key=lambda q: FREQ_RANK.get(q, 0), default=win["frequency"])
    contexts = list(dict.fromkeys(win["contexts"] + other["contexts"]))[:6]
    degree = "marked" if "marked" in {a.get("degree"), b.get("degree")} else (win.get("degree") or other.get("degree"))
    return {**win, "status": status, "frequency": freq, "contexts": contexts, "degree": degree,
            "qualifier": win.get("qualifier") or other.get("qualifier"),
            "time_frame": "current_episode" if "current_episode" in {a["time_frame"], b["time_frame"]} else win["time_frame"],
            "onset_or_duration_text": win["onset_or_duration_text"] or other["onset_or_duration_text"],
            "period_label": win["period_label"] or other["period_label"],
            "evidence_segment_ids": list(dict.fromkeys(win["evidence_segment_ids"] + other["evidence_segment_ids"]))[:MAX_EVIDENCE],
            "counter_evidence": counter[:MAX_EVIDENCE], "basis": win["basis"] or other["basis"],
            "counter_check": {**win["counter_check"], "found": [c["segment_id"] for c in counter[:MAX_EVIDENCE]]}}


def validate_risk(value, allowed, names, plan_ids: dict[str, str]) -> tuple[list[dict], list[dict], Checker]:
    cfg = fast_config()
    if not isinstance(value, dict) or not isinstance(value.get("r"), list) or (plan_ids and not isinstance(value.get("pl"), list)):
        raise GenerationError("Invalid output schema: r (and pl when plans are listed) required")
    checker = Checker(allowed, names)
    raw = [{"category": cfg["risk_codes"].get(r.get("c")), "refs": [_alias(i) for i in r.get("e") or [] if isinstance(i, int)], "basis": str(r.get("b") or "")[:200]}
           for r in value["r"] if isinstance(r, dict)]
    risks = validate_findings({"risk_statements": raw}, {"requested": {"risk_statements": True}}, checker)["risk_statements"]
    plans = []
    for p in value.get("pl") or []:
        if not isinstance(p, dict) or p.get("id") not in plan_ids or p.get("s") not in cfg["plan_codes"]:
            checker.withhold("plan follow-up", str(p.get("id") if isinstance(p, dict) else "?"), "unknown plan or status", p)
            continue
        status = cfg["plan_codes"][p["s"]]
        sources = checker.refs("plan follow-up", p["id"], [_alias(i) for i in p.get("e") or [] if isinstance(i, int)])
        if sources is None:
            continue
        if status != "not_discussed" and not sources:
            checker.withhold("plan follow-up", p["id"], "a discussed plan needs cited turns", p)
            continue
        basis = str(p.get("b") or "")[:300]
        if not checker.text_ok("plan follow-up", p["id"], [basis], sources):
            continue
        plans.append({"plan_id": plan_ids[p["id"]], "status": status, "basis": basis, "evidence_segment_ids": [s["id"] for s in sources]})
    return risks, plans, checker


def attach_questions(risks: list[dict], segments: list[dict], lexicon: set[str], reach: int = 2) -> list[dict]:
    """Cite the clinician question that drew out a flagged client answer.

    For each client turn a risk row cites, the clinician turns right before it (at most ``reach`` turns back, stopping
    at the first client turn) are added when they carry a risk-lexicon word ("have you ever hurt yourself?"). The
    model's own citations stay first. Runs after de-duplication, so separate statements answering one question stay
    separate rows.
    """
    pos = {s["id"]: i for i, s in enumerate(segments)}
    for row in risks:
        extra = []
        for sid in row["segment_ids"]:
            i = pos.get(sid)
            if i is None or segments[i]["speaker_role"] != "CLIENT":
                continue
            for j in range(i - 1, max(-1, i - 1 - reach), -1):
                if segments[j]["speaker_role"] == "CLIENT":
                    break
                if segments[j]["id"] in lexicon:
                    extra.append(segments[j]["id"])
        if extra:
            ids = list(dict.fromkeys(row["segment_ids"] + extra))
            row["segment_ids"] = sorted(ids, key=pos.get)
    return risks


# ---------------------------------------------------------------- calls

class Caller:
    """One checkpointed call: cache hit, or generate with one schema repair and one larger-output retry."""

    def __init__(self, runtime, cache: Path, revision: str, log):
        self.rt, self.cache, self.revision, self.log = runtime, cache, revision, log
        self.cache.mkdir(parents=True, exist_ok=True)
        self.stats = {"calls": 0, "cache_hits": 0, "retries": 0, "splits": 0}
        settings = getattr(runtime, "settings", None)
        self.server = settings.sha256() if settings is not None else "unchanged"

    def __call__(self, system, data, schema, output, validate, split_on_limit=False):
        key = digest([self.revision, VERSION, SCHEMA_VERSION, fast_config()["sha256"], system, data, schema, output,
                      getattr(self.rt, "model", {}).get("sha256"), self.server, self.rt.context])
        path = self.cache / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text())
            if cached.get("key") == key:
                validate(cached["value"])
                self.stats["cache_hits"] += 1
                return cached["value"]
        self.rt.response_schema = schema
        self.rt.prompt_version = VERSION
        repair, larger = "", output
        for attempt in range(3):
            self.rt.check()
            self.stats["calls"] += 1
            try:
                value = self.rt.generate(system + repair, data, larger)
                validate(value)
                write(path, {"key": key, "value": value})
                return value
            except OutputLimitError:
                if split_on_limit:
                    raise  # the caller halves the code list; the cached prefix is kept
                available = self.rt.context - self.rt.safety_margin - self.rt.count(self.rt.prompt(system, data))
                bigger = min(available, max(larger + 256, int(larger * 1.6)))
                if attempt or bigger < larger + 128:
                    raise
                self.stats["retries"] += 1
                larger = bigger
            except GenerationError as exc:
                if repair or not any(x in str(exc) for x in ("Malformed JSON", "Invalid output schema")):
                    raise
                self.stats["retries"] += 1
                repair = "\nYour previous answer was invalid (" + str(exc)[:120] + "). Return complete JSON matching the schema only."
        raise GenerationError("fast Stage A call failed after retries")


def _group_call(caller, system, lines, lo, hi, group, codes, segments, allowed, names, log, ctx, pointers=None) -> tuple[list, Checker | None]:
    data = {"turns": lines, "task": group_task(group, codes, pointers)}
    schema = group_schema(group, lo, hi, codes)
    checked: dict = {}

    def validate(value):
        checked["rows"], checked["checker"] = validate_rows(value, allowed["segments"], allowed["rows"], names, codes)
    try:
        with log.context(**ctx, group=group):
            caller(system, data, schema, output_for(group, n_features=len(codes)), validate, split_on_limit=len(codes) > 1)
        return checked["rows"], checked["checker"]
    except OutputLimitError:
        if len(codes) < 2:
            raise
        caller.stats["splits"] += 1
        mid = len(codes) // 2
        rows, checker = [], Checker(allowed["rows"], names)
        for part in (codes[:mid], codes[mid:]):
            r, c = _group_call(caller, system, lines, lo, hi, group, part, segments, allowed, names, log, ctx, pointers)
            rows += r
            checker.limitations += c.limitations
            checker.withheld += c.withheld
        return rows, checker


def extract_session_fast(snapshot: dict, runtime, folder, *, plans: list[dict] | None = None, names: list[str] | None = None,
                         progress=lambda s: None, use_e5: bool | None = None) -> dict:
    """Fast DSM Stage A for one snapshot {'session_id','revision','segments'}. Same result shape as ``dsm-v1``."""
    segments = snapshot["segments"]
    if not segments:
        raise GenerationError("Transcript is empty")
    if digest(segments) != snapshot["revision"]:
        raise GenerationError("Snapshot revision mismatch")
    reg, cfg = dsm_registry(), fast_config()
    folder = Path(folder)
    trace_for(runtime, folder)
    log = getattr(runtime, "calllog", None) or CallLog(None)
    started = time.monotonic()
    result = {"version": VERSION, "schema_version": SCHEMA_VERSION, "registry_version": reg.tag, "pipeline": "dsm-fast", "config_version": cfg["version"],
              "session_id": snapshot["session_id"], "revision": snapshot["revision"], "evidence_mode": "full-windows",
              "findings": {}, "risk_statements": [], "phq_items": {}, "gad7_items": {}, "plan_followups": [],
              "limitations": [], "withheld": [], "groups": {}}
    with log.context(session_id=snapshot["session_id"], stage="A"):
        with log.phase("triage"):
            tri = triage(segments, snapshot["session_id"], use_e5=use_e5, calllog=log)
        result["triage"] = {g: {"call": v["call"], "reason": v["reason"]} for g, v in tri["groups"].items()}
        result["triage_lexicon_turns"] = tri["lexicon_turns"]
        if tri["note"]:
            result["limitations"].append(tri["note"])
        progress("analysing the session (fast DSM)")
        runtime.load("qwen")
        system = system_prompt()
        lines = [turn_line(i, s) for i, s in enumerate(segments)]
        plan_ids = {f"P{i + 1}": p["plan_id"] for i, p in enumerate(plans or [])}
        plan_list = [{"id": pid, "text": next(p["text"] for p in plans if p["plan_id"] == real)} for pid, real in plan_ids.items()]
        groups = [g["key"] for g in cfg["groups"]]
        biggest = max([output_for(g) for g in groups] + [output_for(None, len(plan_list))])
        longest = max([group_task(g, c, tri["pointers"]) for g in groups for c in code_chunks(g)] + [risk_task(plan_list)], key=len)
        with log.phase("windows"):
            windows = plan_windows(runtime, lines, system, biggest, longest)
        result["windows"] = [[a, b] for a, b in windows]
        aliases = aliases_for(segments)
        caller = Caller(runtime, folder.parent / "fast-cache", snapshot["revision"], log)
        merged: dict[str, dict] = {}
        raw_risks, plan_rows = [], {}
        failed = []
        for w, (lo, hi) in enumerate(windows):
            window_lines = lines[lo:hi]
            allowed = {"rows": {f"T{i}": {**aliases[f"T{i}"], "alias": f"T{i}"} for i in range(lo, hi)}, "segments": segments}
            prefix = max(0, runtime.count(runtime.prompt(system, {"turns": window_lines, "task": ""})) - 12)
            ctx = {"window": w, "prefix_tokens": prefix}
            progress(f"fast DSM: window {w + 1} of {len(windows)}")
            # risk + plans first: never gated
            try:
                data = {"turns": window_lines, "task": risk_task(plan_list)}
                got: dict = {}

                def validate(value):
                    got["risks"], got["plans"], got["checker"] = validate_risk(value, allowed["rows"], names, plan_ids)
                with log.context(**ctx, group="risk"):
                    caller(system, data, risk_schema(lo, hi - 1, plan_list), output_for(None, len(plan_list)), validate)
                raw_risks += got["risks"]
                for p in got["plans"]:
                    if p["plan_id"] not in plan_rows or plan_rows[p["plan_id"]]["status"] == "not_discussed":
                        plan_rows[p["plan_id"]] = p
                result["limitations"] += got["checker"].limitations
                result["withheld"] += got["checker"].withheld
                # Risk recheck (deterministic trigger): risk-lexicon turns of this window that no accepted row cites (±1 turn)
                # get one more short call on the same cached window. The model still decides; code never invents a flag.
                index = {s["id"]: i for i, s in enumerate(segments)}
                cited = {index[sid] for r in raw_risks for sid in r["segment_ids"] if sid in index}
                lexicon = [index[sid] for sid in tri["lexicon_turns"] if lo <= index[sid] < hi]
                missing = [i for i in lexicon if not any(abs(i - c) <= 1 for c in cited)]
                if missing:
                    try:
                        check: dict = {}

                        def validate_check(value):
                            check["risks"], _, check["checker"] = validate_risk({"r": value.get("r") if isinstance(value, dict) else None}, allowed["rows"], names, {})
                        with log.context(**ctx, group="risk-check"):
                            caller(system, {"turns": window_lines, "task": risk_check_task(missing)}, risk_schema(lo, hi - 1, []), output_for(None), validate_check)
                        raw_risks += check["risks"]
                        result["limitations"] += check["checker"].limitations
                        result.setdefault("risk_rechecks", []).append({"window": w, "turns": missing, "rows": len(check["risks"])})
                    except GenerationError as exc:
                        if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
                            raise
                        result["limitations"].append(f"risk recheck: window {w + 1} incomplete ({str(exc)[:160]}); review turns {missing} directly")
            except GenerationError as exc:
                if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
                    raise
                failed.append(f"risk window {w + 1}")
                result["limitations"].append(f"risk statements: window {w + 1} incomplete ({str(exc)[:160]}); review the transcript directly")
            for group in groups:
                if not tri["groups"][group]["call"]:
                    continue
                for codes in code_chunks(group):
                    try:
                        window_pointers = {k: [i for i in v if lo <= i < hi] for k, v in tri["pointers"].items()}
                        rows, checker = _group_call(caller, system, window_lines, lo, hi - 1, group, codes, segments, allowed, names, log, ctx, window_pointers)
                    except GenerationError as exc:
                        if getattr(runtime, "cancel", None) is not None and runtime.cancel.is_set():
                            raise
                        failed.append(f"{group} window {w + 1}")
                        result["limitations"].append(f"{group} ({' '.join(codes)}): window {w + 1} incomplete ({str(exc)[:160]})")
                        continue
                    for key, finding in rows:
                        merged[key] = merge(merged.get(key), finding)
                    result["limitations"] += checker.limitations
                    result["withheld"] += checker.withheld
        for group in groups:
            features = group_features(group)
            called = tri["groups"][group]["call"]
            for f in features:
                if f["key"] in merged:
                    result["findings"][f["key"]] = merged[f["key"]]
                    continue
                note = "not found in the turns" if called else "not discussed (no candidate turns found)"
                result["findings"][f["key"]] = {"status": "insufficient_evidence", "frequency": "unclear", "onset_or_duration_text": "",
                                                "time_frame": "unclear", "period_label": "", "qualifier": None, "degree": None, "contexts": [],
                                                "who": "client_report", "evidence_segment_ids": [], "counter_evidence": [],
                                                "counter_check": {"searched": called, "found": [], "alternative_explanation": "", "method": "single-pass-compact"},
                                                "basis": note}
            result["groups"][group] = {"status": "failed" if any(x.startswith(group) for x in failed) else "completed" if called else "skipped",
                                       "evidence": "full-windows", "windows": len(windows), "reason": tri["groups"][group]["reason"]}
        result["risk_statements"] = attach_questions(dedupe_risks(reg, raw_risks, {r["id"]: r for r in segments}), segments, set(tri["lexicon_turns"]))
        result["plan_followups"] = list(plan_rows.values())
        result["phq_items"], result["gad7_items"] = derive(result["findings"])
        result["groups"]["questionnaires"] = {"status": "completed", "evidence": "derived-from-findings", "version": "dsm-fast-derived-v1"}
        result["groups"]["risk"] = {"status": "failed" if any(x.startswith("risk") for x in failed) else "completed", "windows": len(windows)}
        result["calls"] = caller.stats
    result["limitations"] = list(dict.fromkeys(result["limitations"]))
    result["seconds"] = round(time.monotonic() - started, 3)
    result["model_sha256"] = getattr(runtime, "model", {}).get("sha256")
    return result

"""Clinician-labelled vignette set: schema, validation, a resumable evaluation run and a report (user-run).

    python -m services.progress.evaluation.vignettes validate [--root data/research/vignettes]
    python -m services.progress.evaluation.vignettes run --run-id <id> [--root ...] [--evidence retrieval|full|auto]
    python -m services.progress.evaluation.vignettes status --run-id <id>
    python -m services.progress.evaluation.vignettes report --run-id <id> [--root ...]

Layout (see docs/VIGNETTE_AUTHORING_GUIDE.md):

    <root>/<case_id>/case.json            synthetic case metadata, no labels
    <root>/<case_id>/sessions/s<n>.json   ordered sessions: turns with speaker CLIENT/CLINICIAN and text only
    <root>/<case_id>/labels.json          consultant labels, written blind to model output

The run reads case.json and sessions only; labels are read by `validate` and `report`, never by `run`, and never reach a
prompt or the clinical database. Stage A runs on every session; Stage B runs once per case for every enabled condition
(bipolar, GAD, social anxiety, depressive episode) as of the last session, with prior-session history. Checkpoints are
written per case; rerunning the same command resumes, and Ctrl+C stops safely (second Ctrl+C forces).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from services.retrieval.engine import digest

from .longcounsel import ROOT, canonical

VIGNETTES = Path(os.getenv("PROGRESS_VIGNETTES_DIR", str(ROOT / "data/research/vignettes")))
RUNS = Path(os.getenv("PROGRESS_VIGNETTE_RUNS_DIR", str(ROOT / "data/research/vignette-runs")))
LABEL = "Clinician-labelled vignette set (synthetic cases): development evaluation of all four target conditions."
CASE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
TURN_FIELDS = {"turn_id", "speaker", "text"}
CONDITION_STATUS = ("present", "subthreshold", "absent")
CRITERION_STATUS = ("met", "not_met", "insufficient_evidence")
POSITIVE = {"possible": {"likely", "possible"}, "likely": {"likely"}}


# ---------------------------------------------------------------- schema

class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")   # a label or score field inside a transcript is a leakage error
    turn_id: int = Field(ge=1)
    speaker: Literal["CLIENT", "CLINICIAN"]
    text: str = Field(min_length=1, max_length=4000)


class SessionFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_index: int = Field(ge=1, le=20)
    days_since_first: int = Field(ge=0, default=0)
    turns: list[Turn] = Field(min_length=2)

    @model_validator(mode="after")
    def turn_order(self):
        ids = [t.turn_id for t in self.turns]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("turn_id must run 1, 2, 3, ... in order")
        return self


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    title: str = Field(min_length=1, max_length=120)
    synthetic: Literal[True]
    language: str = "en"
    author: str = Field(min_length=1)
    created: date
    summary: str = Field(default="", max_length=600)   # neutral description, no diagnosis (it is visible to anyone running cases)

    @field_validator("case_id")
    @classmethod
    def case_id_format(cls, v):
        if not CASE_ID.match(v):
            raise ValueError("case_id: lowercase letters, digits and '-' only (2-41 characters)")
        return v


class ConditionLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["present", "subthreshold", "absent"]
    subtype_key: str | None = None
    criteria: dict[str, Literal["met", "not_met", "insufficient_evidence"]] = Field(default_factory=dict)
    note: str = ""


class DsmConditionLabel(BaseModel):
    """Consultant label against registry v2: the DSM criteria pattern and, optionally, criterion items."""
    model_config = ConfigDict(extra="forbid")
    label: Literal["likely", "possible", "insufficient_evidence", "not_met"]
    items: dict[str, Literal["present", "subthreshold", "absent", "explicit_denial", "insufficient_evidence", "conflicting"]] = Field(default_factory=dict)
    note: str = ""


class DsmLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registry_version: str = Field(min_length=1)
    conditions: dict[str, DsmConditionLabel] = Field(default_factory=dict)
    session_items: dict[str, dict[str, Literal["present", "subthreshold", "absent", "explicit_denial", "insufficient_evidence", "conflicting"]]] = Field(default_factory=dict)


class Labels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    labelled_by: str = Field(min_length=1)
    labelled_on: date
    blind_to_model_output: Literal[True]
    conditions: dict[str, ConditionLabel]
    session_domains: dict[str, dict[str, int | Literal["insufficient_evidence"]]] = Field(default_factory=dict)
    dsm: DsmLabels | None = None  # registry v2 labels (DSM-5-TR criterion ids); the v1 runner ignores them
    notes: str = ""


def _registry():
    from services.progress.registry import registry
    return registry()


def check_labels(labels: Labels, sessions: list[SessionFile]) -> list[str]:
    """Registry cross-checks: known conditions, criteria of that condition, subtypes, domains, sessions and 0-3 ratings."""
    reg = _registry()
    errors = []
    enabled = {c.key: c for c in reg.conditions.enabled}
    for key, lab in labels.conditions.items():
        cond = enabled.get(key)
        if cond is None:
            errors.append(f"unknown or disabled condition {key!r} (enabled: {', '.join(enabled)})")
            continue
        ids = {c.id for c in cond.criteria}
        errors += [f"{key}: criterion {cid!r} does not belong to this condition" for cid in lab.criteria if cid not in ids]
        subtypes = {s.key for s in cond.subtypes}
        if lab.subtype_key and lab.subtype_key not in subtypes:
            errors.append(f"{key}: unknown subtype {lab.subtype_key!r}")
        if lab.subtype_key and lab.status == "absent":
            errors.append(f"{key}: a subtype needs status present or subthreshold")
    indexes = {str(s.session_index) for s in sessions}
    domain_keys = {d.key for d in reg.domains.domains}
    for idx, ratings in labels.session_domains.items():
        if idx not in indexes:
            errors.append(f"session_domains: no session {idx}")
        for dk, v in ratings.items():
            if dk not in domain_keys:
                errors.append(f"session_domains[{idx}]: unknown domain {dk!r}")
            if isinstance(v, int) and not 0 <= v <= 3:
                errors.append(f"session_domains[{idx}].{dk}: rating must be 0-3")
    if labels.dsm is not None:
        errors += check_dsm_labels(labels.dsm, indexes)
    return errors


def check_dsm_labels(dsm, indexes: set[str]) -> list[str]:
    """DSM labels use registry v2 ids. Only the registry (JSON) is read here; the benchmark code never imports it."""
    from services.progress.dsm.registry import dsm_registry
    reg = dsm_registry()
    errors = []
    conditions = {c.key: c for c in reg.conditions.conditions}
    item_ids = {i.id for i in reg.items.items}
    for key, lab in dsm.conditions.items():
        cond = conditions.get(key)
        if cond is None:
            errors.append(f"dsm: unknown condition {key!r}")
            continue
        own = {i for e in cond.elements for i in ([e.item] if e.item else []) + e.items}
        errors += [f"dsm.{key}: item {i!r} does not belong to this condition" for i in lab.items if i not in own]
    for idx, items in dsm.session_items.items():
        if idx not in indexes:
            errors.append(f"dsm.session_items: no session {idx}")
        errors += [f"dsm.session_items[{idx}]: unknown item {i!r}" for i in items if i not in item_ids]
    return errors


def load_case(folder: Path, *, with_labels: bool) -> dict:
    """Validated case. ``with_labels=False`` is the only form the run uses."""
    folder = Path(folder)
    case = Case.model_validate_json((folder / "case.json").read_text())
    if case.case_id != folder.name:
        raise ValueError(f"{folder.name}: case_id {case.case_id!r} must equal the folder name")
    files = sorted((folder / "sessions").glob("s*.json"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    if not files:
        raise ValueError(f"{case.case_id}: no sessions/s<n>.json files")
    sessions = [SessionFile.model_validate_json(p.read_text()) for p in files]
    if [s.session_index for s in sessions] != list(range(1, len(sessions) + 1)):
        raise ValueError(f"{case.case_id}: session_index must be 1..{len(sessions)} matching the file names")
    days = [s.days_since_first for s in sessions]
    if days != sorted(days) or days[0] != 0:
        raise ValueError(f"{case.case_id}: days_since_first must start at 0 and never decrease")
    out = {"case": case.model_dump(mode="json"), "sessions": [s.model_dump() for s in sessions],
           "content_sha256": canonical({"case": case.model_dump(mode="json"), "sessions": [s.model_dump() for s in sessions]})}
    if with_labels:
        path = folder / "labels.json"
        if not path.exists():
            raise ValueError(f"{case.case_id}: labels.json missing")
        labels = Labels.model_validate_json(path.read_text())
        if labels.case_id != case.case_id:
            raise ValueError(f"{case.case_id}: labels.json case_id does not match")
        errors = check_labels(labels, sessions)
        if errors:
            raise ValueError(f"{case.case_id}: " + "; ".join(errors))
        out["labels"] = labels.model_dump(mode="json")
        out["labels_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def case_folders(root: Path) -> list[Path]:
    root = Path(root)
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "case.json").exists()) if root.is_dir() else []


def validate(root: Path = VIGNETTES) -> dict:
    """Validate every case with its labels and summarise the case mix. Raises on the first invalid case."""
    cases = [load_case(f, with_labels=True) for f in case_folders(root)]
    if not cases:
        raise SystemExit(f"No cases under {root} (each case is a folder with case.json, sessions/ and labels.json)")
    mix: dict[str, dict[str, int]] = {}
    for c in cases:
        for key, lab in c["labels"]["conditions"].items():
            mix.setdefault(key, {s: 0 for s in CONDITION_STATUS})[lab["status"]] += 1
    return {"root": str(root), "cases": len(cases), "sessions": sum(len(c["sessions"]) for c in cases),
            "condition_mix": mix, "subtypes": sorted({lab["subtype_key"] for c in cases for lab in c["labels"]["conditions"].values() if lab["subtype_key"]}),
            "set_sha256": canonical([c["content_sha256"] for c in cases])}


# ---------------------------------------------------------------- pipeline inputs (labels never touched here)

def snapshot(case_id: str, session: dict) -> dict:
    """Synthetic timestamps as in LongCounsel-8: turn k (0-based) starts at k*10 s and lasts 9 s."""
    sid = f"vig:{case_id}:s{session['session_index']}"
    rows = [{"id": f"vig-{case_id}-s{session['session_index']}-t{t['turn_id']}", "session_id": sid, "start_ms": k * 10000,
             "end_ms": k * 10000 + 9000, "speaker_role": t["speaker"], "text": t["text"], "revision": 1} for k, t in enumerate(session["turns"])]
    return {"session_id": sid, "revision": digest(rows), "segments": rows}


def stage_b_inputs(case_id: str, sessions: list[dict], stage_a: list[dict]) -> list[dict]:
    from .benchmark import phq8_total
    out = []
    for session, result in zip(sessions, stage_a):
        snap = snapshot(case_id, session)
        total, rated = phq8_total(result)
        out.append({"session_id": snap["session_id"], "index": session["session_index"], "segments": snap["segments"],
                    "observations": [{"domain_key": k, "status_value": v["status_value"], "rating": v["rating"], "timeframe": v["timeframe"],
                                      "duration_text": v.get("duration_text", ""), "review_status": "pending",
                                      "evidence_segment_ids": v["evidence_segment_ids"]} for k, v in result.get("domains", {}).items()],
                    "scores": [{"instrument_key": "phq9", "metric": "phq8", "value": total, "provenance": "AI_GENERATED", "review_status": "pending"}] if rated else []})
    return out


def compact_stage_a(result: dict) -> dict:
    return {"domains": {k: {"status": v["status_value"], "rating": v["rating"], "timeframe": v["timeframe"]} for k, v in result.get("domains", {}).items()},
            "phq_items": {k: v.get("score") for k, v in result.get("phq_items", {}).items()},
            "gad7_items": {k: v.get("score") for k, v in result.get("gad7_items", {}).items()},
            "risk_statements": len(result.get("risk_statements", [])),
            "groups": {g: v.get("status") for g, v in result.get("groups", {}).items()}, "limitations": len(result.get("limitations", []))}


def compact_prediction(r: dict) -> dict:
    return {"status": "completed", "prediction": r["prediction"], "subtype_key": r.get("subtype_key"), "criteria_met_count": r["criteria_met_count"],
            "criteria_required": r["criteria_required"], "duration_status": r["duration_status"], "impairment_status": r["impairment_status"],
            "notes": r["postprocess"]["notes"][:20],
            "findings": [{"criterion_id": f["criterion_id"], "status": f["status"], "timeframe": f["timeframe"],
                          "evidence": [{"segment_id": e["segment_id"], "speaker_role": e["speaker_role"], "basis": e["basis"]} for e in f["evidence"]],
                          "counter_evidence": [{"segment_id": e["segment_id"], "basis": e["basis"]} for e in f["counter_evidence"]]}
                         for f in r["findings"]],
            "rationale": r["rationale"], "seconds": r.get("seconds")}


def run_config(root: Path, evidence: str, model: dict, conditions: list[str]) -> dict:
    from .benchmark import STAGE_A_FILES, STAGE_B_FILES, extract_prompt, file_hashes, runtime_settings
    contents = [load_case(f, with_labels=False)["content_sha256"] for f in case_folders(root)]
    return {"kind": "vignettes", "evidence": evidence, "history": "prior", "conditions": conditions, "extract_prompt": extract_prompt(),
            "cases": {"root": str(Path(root)), "n": len(contents), "content_sha256": canonical(contents)},
            "code_stage_a": file_hashes(STAGE_A_FILES), "code_stage_b": file_hashes(STAGE_B_FILES), "model": model, "runtime": runtime_settings()}


def run(run_id: str, *, root: Path = VIGNETTES, runs: Path = RUNS, evidence: str = "retrieval", model: dict | None = None,
        install_signals: bool = True, echo: bool = True) -> int:
    from services.note_generation.runtime import GenerationError
    from services.note_generation.storage import memory_slot
    from services.progress.prediction import predict_condition
    from . import benchmark as bm
    if not run_id.replace("-", "").replace("_", "").isalnum():
        raise SystemExit("run-id may contain letters, digits, '-' and '_' only")
    if evidence not in {"retrieval", "full", "auto"}:
        raise SystemExit("--evidence must be retrieval, full or auto")
    folders = case_folders(root)
    if not folders:
        raise SystemExit(f"No cases under {root}")
    conditions = list(_registry().conditions.enabled_conditions)
    model = model or bm.model_identity()
    folder = Path(runs) / run_id
    config = run_config(root, evidence, model, conditions)
    log = bm.RunLog(folder / "run.log", echo)
    with bm.run_lock(folder):
        if (folder / "config.json").exists():
            diff = bm.config_diff(json.loads((folder / "config.json").read_text()), config)
            if diff:
                raise SystemExit("Refusing to mix configurations within one run-id. Differences:\n  " + "\n  ".join(diff[:20]) +
                                 "\nUse a new --run-id for a new configuration.")
            log(f"resume vignette run={run_id}")
        else:
            bm.write_json(folder / "config.json", config)
            log(f"start vignette run={run_id} cases={len(folders)} evidence={evidence} conditions={','.join(conditions)}")
        done = {p.stem for p in (folder / "cases").glob("*.json")}
        status = {"state": "running", "run_id": run_id, "completed": len(done), "total": len(folders), "pid": os.getpid(),
                  "started_at": datetime.now(timezone.utc).isoformat()}
        bm.write_json(folder / "status.json", status)
        if len(done) == len(folders):
            log(f"already complete: {len(folders)}/{len(folders)} cases")
            bm.write_json(folder / "status.json", {**status, "state": "completed"})
            return 0
        stopper = bm.Stopper(log)
        if install_signals:
            stopper.install()
        work = folder / "_work"
        archive: list = []
        exit_code, runtime = 0, None
        try:
            with memory_slot(stopper.cancel, timeout=600):
                runtime = bm.RUNTIME_FACTORY(work, stopper.cancel)
                stopper.runtime = runtime
                runtime.load("qwen")
                log(f"model loaded backend={runtime.metrics.get('backend')}")
                for case_folder in folders:
                    case = load_case(case_folder, with_labels=False)
                    cid = case["case"]["case_id"]
                    if cid in done:
                        continue
                    started = time.monotonic()
                    stage_a, compact = [], []
                    for session in case["sessions"]:
                        if stopper.cancel.is_set():
                            raise GenerationError("Cancelled by user")
                        bm.reset_budget(runtime, archive)
                        cache = folder / "stage-a" / f"{cid}__s{session['session_index']}.json"
                        result, _state = bm.run_stage_a(snapshot(cid, session), evidence, runtime, work, cache)
                        stage_a.append(result)
                        compact.append({"session_index": session["session_index"], **compact_stage_a(result)})
                    inputs = stage_b_inputs(cid, case["sessions"], stage_a)
                    predictions = {}
                    for key in conditions:
                        bm.reset_budget(runtime, archive)
                        try:
                            predictions[key] = compact_prediction(predict_condition(inputs, key, runtime, work, history="prior"))
                        except GenerationError as exc:
                            if stopper.cancel.is_set():
                                raise
                            predictions[key] = {"status": "failed", "error": str(exc)[:300], "prediction": None, "findings": []}
                    if stopper.cancel.is_set():
                        raise GenerationError("Cancelled by user")
                    bm.write_json(folder / "cases" / f"{cid}.json", {"case_id": cid, "content_sha256": case["content_sha256"], "sessions": compact,
                                                                   "predictions": predictions, "wall_seconds": round(time.monotonic() - started, 3),
                                                                   "completed_at": datetime.now(timezone.utc).isoformat()})
                    done.add(cid)
                    log(f"{len(done)}/{len(folders)} {cid} {time.monotonic() - started:.1f}s " +
                        " ".join(f"{k}={v.get('prediction')}" for k, v in predictions.items()))
                    bm.write_json(folder / "status.json", {**status, "completed": len(done), "updated_at": datetime.now(timezone.utc).isoformat()})
        except GenerationError as exc:
            exit_code = 130 if stopper.cancel.is_set() else 1
            log(f"stopped at {len(done)}/{len(folders)}; rerun the same command to resume" if exit_code == 130 else f"failed: {exc}")
        except RuntimeError as exc:
            exit_code = 130 if stopper.cancel.is_set() else 1
            log(f"failed: {exc}")
        finally:
            if runtime is not None:
                runtime.close()
                archive.extend(runtime.metrics.get("calls", []))
                bm.write_json(folder / "runtime-metrics.json", {"backend": runtime.metrics.get("backend"), "calls": bm.call_stats(archive)})
        state = "completed" if len(done) == len(folders) else "stopped" if exit_code == 130 else "failed" if exit_code else "running"
        bm.write_json(folder / "status.json", {**status, "state": state, "completed": len(done), "updated_at": datetime.now(timezone.utc).isoformat()})
        return exit_code


# ---------------------------------------------------------------- metrics

def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def binary_metrics(pairs: list[tuple[bool, bool]]) -> dict:
    """pairs = (truth, predicted)."""
    tp = sum(t and p for t, p in pairs)
    fp = sum(p and not t for t, p in pairs)
    fn = sum(t and not p for t, p in pairs)
    tn = sum(not t and not p for t, p in pairs)
    sens, ppv = _rate(tp, tp + fn), _rate(tp, tp + fp)
    return {"n": len(pairs), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "sensitivity": sens, "specificity": _rate(tn, tn + fp),
            "ppv": ppv, "npv": _rate(tn, tn + fn), "f1": (2 * sens * ppv / (sens + ppv)) if sens and ppv else (0.0 if tp == 0 and (fp or fn) else None)}


def case_bootstrap(rows: list, fn, resamples: int = 2000, seed: int = 42) -> list[float] | None:
    """95% percentile interval resampling whole cases."""
    if not rows:
        return None
    rng = random.Random(seed)
    values = []
    for _ in range(resamples):
        v = fn([rows[rng.randrange(len(rows))] for _ in rows])
        if v is not None:
            values.append(v)
    if not values:
        return None
    values.sort()
    return [values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]]


def condition_metrics(records: list[dict], key: str, resamples: int = 2000) -> dict:
    rows = [(r["labels"]["conditions"][key], r["predictions"].get(key, {})) for r in records if key in r["labels"]["conditions"]]
    out = {"n_labelled": len(rows), "truth_counts": {s: sum(1 for lab, _ in rows if lab["status"] == s) for s in CONDITION_STATUS},
           "label_counts": {lab: sum(1 for _, p in rows if p.get("prediction") == lab) for lab in ("likely", "possible", "unlikely", "insufficient_evidence")},
           "failed": sum(1 for _, p in rows if p.get("status") == "failed")}
    for threshold, positive in POSITIVE.items():
        pairs = [(lab["status"] == "present", p.get("prediction") in positive) for lab, p in rows]
        m = binary_metrics(pairs)
        for name in ("sensitivity", "specificity"):
            m[f"{name}_ci95"] = case_bootstrap(pairs, lambda s, n=name: binary_metrics(s)[n], resamples)
        out[f"at_{threshold}"] = m
    typed = [(lab["subtype_key"], p.get("subtype_key")) for lab, p in rows if lab["subtype_key"]]
    out["subtype"] = {"n": len(typed), "exact": _rate(sum(a == b for a, b in typed), len(typed))}
    return out


def criterion_agreement(records: list[dict]) -> dict:
    """Consultant criterion labels vs model findings. Missing model findings count as insufficient_evidence."""
    pairs = []
    for r in records:
        for key, lab in r["labels"]["conditions"].items():
            found = {f["criterion_id"]: f["status"] for f in r["predictions"].get(key, {}).get("findings", [])}
            pairs += [(key, cid, truth, found.get(cid, "insufficient_evidence")) for cid, truth in lab["criteria"].items()]
    confusion = {t: {m: sum(1 for _, _, a, b in pairs if a == t and b == m) for m in (*CRITERION_STATUS, "conflicting")} for t in CRITERION_STATUS}
    decided = [(a, b) for _, _, a, b in pairs if a != "insufficient_evidence" and b in {"met", "not_met"}]
    met = [(a == "met", b == "met") for _, _, a, b in pairs if a != "insufficient_evidence"]
    return {"n": len(pairs), "exact_agreement": _rate(sum(a == b for _, _, a, b in pairs), len(pairs)), "confusion": confusion,
            "agreement_when_both_decided": _rate(sum(a == b for a, b in decided), len(decided)), "n_both_decided": len(decided),
            "model_abstained_when_labelled": _rate(sum(1 for _, _, a, b in pairs if a != "insufficient_evidence" and b == "insufficient_evidence"),
                                                   sum(1 for _, _, a, _b in pairs if a != "insufficient_evidence")),
            "met_kappa": cohen_kappa(met),
            "by_condition": {k: _rate(sum(a == b for c, _, a, b in pairs if c == k), sum(1 for c, *_ in pairs if c == k))
                             for k in sorted({c for c, *_ in pairs})}}


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def domain_agreement(records: list[dict]) -> dict:
    """Consultant session domain ratings vs Stage A domain observations (current timeframe ratings only are compared)."""
    both, rated_truth, abstained, over = [], 0, 0, 0
    n = 0
    for r in records:
        model = {str(s["session_index"]): s["domains"] for s in r["sessions"]}
        for idx, ratings in r["labels"]["session_domains"].items():
            for dk, truth in ratings.items():
                n += 1
                obs = model.get(idx, {}).get(dk, {})
                rated = obs.get("status") == "rated" and obs.get("rating") is not None and obs.get("timeframe") != "past"
                if isinstance(truth, int):
                    rated_truth += 1
                    if rated:
                        both.append((truth, obs["rating"]))
                    else:
                        abstained += 1
                elif rated and obs["rating"] > 0:
                    over += 1
    return {"n_labelled": n, "n_both_rated": len(both), "mae": _rate(sum(abs(a - b) for a, b in both), len(both)),
            "exact": _rate(sum(a == b for a, b in both), len(both)), "within_1": _rate(sum(abs(a - b) <= 1 for a, b in both), len(both)),
            "model_abstained_when_rated": _rate(abstained, rated_truth),
            "model_rated_when_label_insufficient": over}


def citation_rows(run_id: str, records: list[dict], root: Path, n: int = 100, seed: int = 42) -> list[dict]:
    pool = []
    for r in records:
        case = load_case(Path(root) / r["case_id"], with_labels=False)
        by_id = {s["id"]: s for sess in case["sessions"] for s in snapshot(r["case_id"], sess)["segments"]}
        for key, p in r["predictions"].items():
            for f in p.get("findings", []):
                for e in f.get("evidence", []):
                    seg = by_id.get(e["segment_id"])
                    if seg:
                        pool.append({"run_id": run_id, "case_id": r["case_id"], "condition": key, "criterion": f["criterion_id"], "status": f["status"],
                                     "segment_id": e["segment_id"], "speaker_role": seg["speaker_role"], "cited_text": seg["text"], "basis": e.get("basis", ""),
                                     "supports_claim (yes/partly/no)": "", "rater": "", "notes": ""})
    random.Random(seed).shuffle(pool)
    return pool[:n]


def report(run_id: str, *, root: Path = VIGNETTES, runs: Path = RUNS, resamples: int = 2000, citations: int = 100) -> dict:
    folder = Path(runs) / run_id
    if not (folder / "config.json").exists():
        raise SystemExit(f"No vignette run {run_id}")
    config = json.loads((folder / "config.json").read_text())
    total = len(case_folders(root))
    records, changed = [], []
    for path in sorted((folder / "cases").glob("*.json")):
        checkpoint = json.loads(path.read_text())
        case = load_case(Path(root) / checkpoint["case_id"], with_labels=True)
        if case["content_sha256"] != checkpoint["content_sha256"]:
            changed.append(checkpoint["case_id"])   # sessions edited after the run: excluded, not silently mixed
            continue
        records.append({**checkpoint, "labels": case["labels"], "labels_sha256": case["labels_sha256"]})
    keys = config["conditions"]
    out = {"label": LABEL, "run_id": run_id, "generated_at": datetime.now(timezone.utc).isoformat(), "config": config,
           "complete": len(records) == total and not changed, "cases_completed": len(records), "cases_expected": total, "cases_changed_since_run": changed,
           "labels_sha256": canonical(sorted((r["case_id"], r["labels_sha256"]) for r in records)),
           "conditions": {k: condition_metrics(records, k, resamples) for k in keys},
           "criteria": criterion_agreement(records), "domains": domain_agreement(records),
           "cases": [{"case_id": r["case_id"], "truth": {k: v["status"] for k, v in r["labels"]["conditions"].items()},
                      "predictions": {k: {"prediction": p.get("prediction"), "subtype_key": p.get("subtype_key"), "criteria_met_count": p.get("criteria_met_count"),
                                          "status": p.get("status")} for k, p in r["predictions"].items()}} for r in records],
           "notes": ["Positive truth = consultant status 'present'; 'subthreshold' and 'absent' count as negative.",
                     "Model positive at 'possible' = likely or possible; at 'likely' = likely only. insufficient_evidence counts as negative.",
                     "Criterion agreement treats a criterion with no model finding as insufficient_evidence.",
                     "Domain agreement compares current-timeframe Stage A ratings with the consultant's session ratings.",
                     f"Bootstrap: {resamples} case resamples, seed 42, percentile 95% intervals. Small case counts give wide intervals.",
                     "Synthetic vignettes: results describe agreement with the consultant's labels on written cases, not clinical accuracy."]}
    target = folder / "report"
    target.mkdir(parents=True, exist_ok=True)
    (target / "report.json").write_text(json.dumps(out, indent=1))
    (target / "report.md").write_text(markdown(out))
    if citations and records:
        rows = citation_rows(run_id, records, root, citations)
        if rows:
            with (target / "citation_rating.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    return out


def _pct(v) -> str:
    return "–" if v is None else f"{v:.0%}"


def markdown(r: dict) -> str:
    lines = [f"# Vignette report: {r['run_id']}", "", r["label"], ""]
    if not r["complete"]:
        lines += [f"**INCOMPLETE RUN:** {r['cases_completed']}/{r['cases_expected']} cases" +
                  (f"; changed since the run: {', '.join(r['cases_changed_since_run'])}" if r["cases_changed_since_run"] else ""), ""]
    lines += ["| Condition | Labelled | Present | Sens (possible) | Spec (possible) | Sens (likely) | Spec (likely) | Abstained |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for k, m in r["conditions"].items():
        p, lk = m["at_possible"], m["at_likely"]
        lines.append(f"| {k} | {m['n_labelled']} | {m['truth_counts']['present']} | {_pct(p['sensitivity'])} | {_pct(p['specificity'])} | "
                     f"{_pct(lk['sensitivity'])} | {_pct(lk['specificity'])} | {m['label_counts']['insufficient_evidence']} |")
    c, d = r["criteria"], r["domains"]
    lines += ["", f"Criteria: {c['n']} labelled, exact agreement {_pct(c['exact_agreement'])}, agreement when both decided {_pct(c['agreement_when_both_decided'])} "
              f"(n={c['n_both_decided']}), model abstained on {_pct(c['model_abstained_when_labelled'])} of decided labels.",
              f"Domains: {d['n_labelled']} labelled ratings, MAE {d['mae'] if d['mae'] is None else round(d['mae'], 2)}, exact {_pct(d['exact'])}, "
              f"model abstained on {_pct(d['model_abstained_when_rated'])} of rated labels.", ""]
    lines += [f"- {n}" for n in r["notes"]]
    return "\n".join(lines) + "\n"


def show_status(run_id: str, runs: Path = RUNS) -> None:
    folder = Path(runs) / run_id
    if not (folder / "status.json").exists():
        raise SystemExit(f"No vignette run {run_id}")
    print((folder / "status.json").read_text())
    if (folder / "run.log").exists():
        print("\n".join((folder / "run.log").read_text().splitlines()[-5:]))


def main(argv=None) -> int:
    from .benchmark import load_env
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--root", type=Path, default=VIGNETTES)
    r = sub.add_parser("run")
    r.add_argument("--run-id", required=True)
    r.add_argument("--root", type=Path, default=VIGNETTES)
    r.add_argument("--evidence", default="retrieval", choices=["retrieval", "full", "auto"])
    s = sub.add_parser("status")
    s.add_argument("--run-id", required=True)
    p = sub.add_parser("report")
    p.add_argument("--run-id", required=True)
    p.add_argument("--root", type=Path, default=VIGNETTES)
    args = parser.parse_args(argv)
    if args.command == "validate":
        print(json.dumps(validate(args.root), indent=2))
    elif args.command == "run":
        return run(args.run_id, root=args.root, evidence=args.evidence)
    elif args.command == "status":
        show_status(args.run_id)
    else:
        print(markdown(report(args.run_id, root=args.root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

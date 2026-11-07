"""Deterministic Stage B post-processing. Pure functions; the code, not the model, decides what the labels may be.

Rules: criteria counts are recomputed from findings with valid cited evidence; a label is only ever downgraded,
never upgraded; 'likely' needs the registry's counts, duration and impairment met by cited findings; 'unlikely'
needs cited counter-evidence; bipolar subtypes need a cited manic/hypomanic period; rationale sentences without
valid source ids are removed and listed.
"""
from __future__ import annotations

from services.progress.registry.loader import Condition

LABEL_ORDER = ["insufficient_evidence", "unlikely", "possible", "likely"]
MAX_RATIONALE = 5


def _met(finding: dict) -> bool:
    return finding["status"] == "met" and bool(finding["evidence"])


def normalise_findings(condition: Condition, findings: list[dict], notes: list[str]) -> dict[str, dict]:
    """One finding per assessed criterion; unsupported statuses become insufficient_evidence."""
    out: dict[str, dict] = {}
    known = {c.id for c in condition.assessed_criteria}
    for f in findings:
        if f["criterion_id"] not in known:
            notes.append(f"Ignored finding for unknown or unassessed criterion {f['criterion_id']}")
            continue
        if f["criterion_id"] in out:
            notes.append(f"Ignored duplicate finding for {f['criterion_id']}")
            continue
        f = dict(f)
        if f["status"] == "met" and not f["evidence"]:
            notes.append(f"{f['criterion_id']}: 'met' without cited evidence was treated as insufficient evidence")
            f["status"] = "insufficient_evidence"
        if f["status"] == "not_met" and not f["counter_evidence"]:
            notes.append(f"{f['criterion_id']}: 'not met' without cited counter-evidence was treated as insufficient evidence")
            f["status"] = "insufficient_evidence"
        if f["status"] == "conflicting" and not (f["evidence"] or f["counter_evidence"]):
            notes.append(f"{f['criterion_id']}: 'conflicting' without citations was treated as insufficient evidence")
            f["status"] = "insufficient_evidence"
        out[f["criterion_id"]] = f
    for c in condition.assessed_criteria:
        if c.id not in out:
            notes.append(f"{c.id}: not returned by the model; recorded as insufficient evidence")
            out[c.id] = {"criterion_id": c.id, "status": "insufficient_evidence", "evidence": [], "counter_evidence": [],
                         "timeframe": "unclear", "duration_note": ""}
    return out


def requirement_status(condition: Condition, findings: dict[str, dict]) -> tuple[bool, int, int, list[str]]:
    """(all satisfied, criteria met count, criteria required, per-requirement descriptions)."""
    met = {cid for cid, f in findings.items() if _met(f)}
    satisfied, count, required, lines = True, 0, 0, []
    for req in condition.requirements:
        need = req.min_met
        if req.min_met_if_only:
            only = req.min_met_if_only.criterion
            group = next((r for r in condition.requirements if only in r.criteria), None)
            others = set(group.criteria) - {only} if group else set()
            if only in met and not (others & met):
                need = req.min_met_if_only.min_met
        got = len(met & set(req.criteria))
        core_ok = not req.must_include_any_of or bool(met & set(req.must_include_any_of))
        ok = got >= need and core_ok
        satisfied &= ok
        count += got
        required += need
        lines.append(f"{req.label}: {got}/{need}{'' if core_ok else ' (required core criterion not met)'}")
    return satisfied, count, required, lines


def impairment(condition: Condition, findings: dict[str, dict]) -> str:
    rows = [findings[c.id] for c in condition.assessed_criteria if c.kind == "impairment" and c.id in findings]
    if any(_met(f) for f in rows):
        return "met"
    if rows and all(f["status"] == "not_met" and f["counter_evidence"] for f in rows):
        return "not_met"
    return "unknown"


def core_ids(condition: Condition) -> set[str]:
    return {c.id for c in condition.assessed_criteria if c.core}


def apply(condition: Condition, model: dict, *, depressive_features: bool = False) -> dict:
    """``model``: validated output with findings already mapped to cited segments."""
    notes: list[str] = []
    findings = normalise_findings(condition, model["findings"], notes)
    satisfied, met_count, required, req_lines = requirement_status(condition, findings)
    imp = impairment(condition, findings)
    if model.get("impairment_status") != imp:
        notes.append(f"Impairment status set to '{imp}' from cited findings (model said '{model.get('impairment_status')}')")
    duration = model.get("duration_status", "unknown")
    duration_cited = any(f["duration_note"].strip() and (f["evidence"] or f["counter_evidence"]) for f in findings.values())
    if duration == "met" and not duration_cited:
        notes.append("Duration 'met' had no cited duration note; set to unknown")
        duration = "unknown"
    if duration == "not_met" and not duration_cited:
        duration = "unknown"
    met = {cid for cid, f in findings.items() if _met(f)}
    core = core_ids(condition)
    allowed = {"insufficient_evidence"}
    subtype = None
    if condition.key == "bipolar":
        subtype, allowed, duration = _bipolar(condition, findings, met, satisfied, depressive_features, duration, notes)
    else:
        if met & core:
            allowed.add("possible")
        if satisfied and duration == "met" and (imp == "met" or not condition.impairment_required):
            allowed.add("likely")
        if core and all(findings[c]["status"] == "not_met" for c in core):
            allowed.add("unlikely")
    model_label = model.get("prediction", "insufficient_evidence")
    label = model_label
    if label not in allowed:
        for candidate in (["possible", "insufficient_evidence"] if label == "likely" else ["insufficient_evidence"]):
            if candidate in allowed:
                label = candidate
                break
        reason = {"likely": "the registry's criteria counts, duration and impairment are not all met by cited findings",
                  "possible": "no core criterion is met with cited evidence",
                  "unlikely": "core criteria lack cited counter-evidence"}.get(model_label, "the label is not supported")
        notes.append(f"Label '{model_label}' downgraded to '{label}': {reason}")
    if condition.key == "bipolar" and model.get("subtype") != subtype and model.get("subtype") is not None:
        notes.append(f"Subtype '{model.get('subtype')}' replaced by '{subtype}' (set deterministically from cited periods)")
    rationale, removed = [], []
    for sentence in model.get("rationale", []):
        if sentence["segment_ids"] and len(rationale) < MAX_RATIONALE:
            rationale.append(sentence)
        else:
            removed.append(sentence["sentence"])
    return {"findings": list(findings.values()), "prediction": label, "subtype_key": subtype, "criteria_met_count": met_count,
            "criteria_required": required, "duration_status": duration, "impairment_status": imp, "rationale": rationale,
            "has_evidence": any(f["evidence"] or f["counter_evidence"] for f in findings.values()) or bool(rationale),
            "postprocess": {"model_label": model_label, "model_subtype": model.get("subtype"), "notes": notes,
                            "removed_sentences": removed, "requirements": req_lines}}


def _bipolar(condition, findings, met, satisfied, depressive_features, duration, notes):
    def period(cid: str) -> bool:
        f = findings.get(cid)
        if f is None or not _met(f):
            return False
        if cid in {"bipolar.manic_period", "bipolar.hypomanic_period", "bipolar.cyclothymic_course"} and not f["duration_note"].strip():
            notes.append(f"{cid}: met without a cited duration note; not counted")
            return False
        return True
    symptoms_ok = satisfied  # mania_a and mania_b requirement groups
    manic = period("bipolar.manic_period") and symptoms_ok
    hypomanic = period("bipolar.hypomanic_period") and symptoms_ok
    depressive = period("bipolar.depressive_period")
    mood_a = bool(met & {"mania.elevated_mood", "mania.irritable_mood"})
    subtype = None
    if manic:
        subtype = "bipolar_i_pattern"
    elif hypomanic and depressive:
        subtype = "bipolar_ii_pattern"
    elif period("bipolar.cyclothymic_course"):
        subtype = "cyclothymic_pattern"
    elif (depressive or depressive_features) and not mood_a and not period("bipolar.hypomanic_period"):
        subtype = "depressive_features_only"
        notes.append("Depressive features without any cited elevated period: bipolarity is not assessable (this does not indicate unipolar depression)")
    allowed = {"insufficient_evidence"}
    if subtype in {"bipolar_i_pattern", "bipolar_ii_pattern"}:
        allowed |= {"likely", "possible"}
        duration = "met"
    elif mood_a or hypomanic or subtype == "cyclothymic_pattern" or period("bipolar.hypomanic_period"):
        allowed.add("possible")
    if all(findings[c]["status"] == "not_met" for c in ("mania.elevated_mood", "mania.irritable_mood") if c in findings):
        allowed.add("unlikely")
    if subtype == "depressive_features_only":
        allowed = {"insufficient_evidence"}
    return subtype, allowed, duration

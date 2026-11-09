"""DSM rule engine: code computes every label, specifier, checklist line and "information still needed".

Pure functions. The model only proposes feature statuses (with citations) per period; nothing here calls a model.

Inputs
------
``periods``: ``[{id, label, time_frame, duration_days, hospitalised, duration_evidence, proposals: {feature: Proposal}}]``
``Proposal``: ``{status, contexts, degree, qualifier, frequency, evidence: [cited], counter_evidence: [cited]}`` where a
cited dict has ``speaker_role`` and, for counter-evidence, ``kind`` (``denial`` or ``alternative``).
``extras``: ``{"global": {feature: Proposal}, "a9": {"present": bool, "source": str, "evidence": [...]}}``

Label policy (user decisions, 9 Nov 2026)
-----------------------------------------
* likely: every required element is met with citations and no exclusion is contradicted by cited evidence.
* possible: the core element is met (or partly met), but something else is unmet or unknown, or an exclusion has
  cited cause evidence.
* not_met: the core element is cited absent, denied or below threshold, or the pattern is ruled out (for example a
  hypomanic period with marked impairment, or MDD with a likely manic episode).
* insufficient_evidence: anything else.
* Unassessed exclusions, clinical-judgement criteria and never-episode criteria do **not** downgrade a label; they go
  into "information still needed".
"""
from __future__ import annotations

from .registry import AllOf, AnyOf, DsmRegistry, Leaf

ORDER = ["likely", "possible", "not_met", "insufficient_evidence"]
LABEL_TEXT = {"likely": "Likely", "possible": "Possible", "insufficient_evidence": "Insufficient evidence", "not_met": "Not met"}
REQUIRED = {"item", "count", "duration", "episode", "all_episodes", "subthreshold_course"}
MARK = {"met": "✓", "not_met": "✕", "unknown": "?", "partial": "~", "contradicted": "!", "info": "·"}
NOT_SATISFIED = {"absent", "explicit_denial", "subthreshold", "not_linked"}
SEVERITY_CODES = {"mild": "F32.0 / F33.0", "moderate": "F32.1 / F33.1", "severe": "F32.2 / F33.2"}


def _denied(proposal: dict) -> bool:
    return any(c.get("kind") == "denial" and c.get("speaker_role") == "CLIENT" for c in proposal.get("counter_evidence", []))


def leaf_status(leaf: Leaf, proposal: dict | None) -> tuple[str, str]:
    """Status of one feature reference, after the denial, context and degree rules."""
    if proposal is None:
        return "insufficient_evidence", ""
    status = proposal.get("status", "insufficient_evidence")
    if status == "present":
        if _denied(proposal):
            return "conflicting", "contradicted by a cited client denial"
        if leaf.context and not set(proposal.get("contexts", [])) & set(leaf.context):
            return "not_linked", f"present, but not linked to {' or '.join(c.replace('_', ' ') for c in leaf.context)}"
        if leaf.degree == "marked" and proposal.get("degree") != "marked":
            return "subthreshold", "present, but not cited as marked"
    return status, ""


def _combine(expr, period: dict, extras: dict) -> tuple[str, list[str], list[tuple[str, dict]]]:
    if isinstance(expr, Leaf):
        proposal = period["proposals"].get(expr.feature) or extras.get("global", {}).get(expr.feature)
        status, note = leaf_status(expr, proposal)
        return status, [note] if note else [], [(expr.feature, proposal)] if proposal else []
    children = [_combine(c, period, extras) for c in (expr.any_of if isinstance(expr, AnyOf) else expr.all_of)]
    statuses = [c[0] for c in children]
    notes = [n for c in children for n in c[1]]
    used = [u for c in children for u in c[2]]
    if isinstance(expr, AnyOf):
        for s in ("present", "conflicting", "subthreshold", "not_linked", "insufficient_evidence", "explicit_denial", "absent"):
            if s in statuses:
                return s, notes, used
    else:
        for s in ("explicit_denial", "absent"):
            if s in statuses:
                return s, notes, used
        if all(s == "present" for s in statuses):
            return "present", notes, used
        for s in ("conflicting", "subthreshold", "not_linked"):
            if s in statuses:
                return s, notes, used
    return "insufficient_evidence", notes, used


def item_status(reg: DsmRegistry, item_id: str, period: dict, extras: dict) -> dict:
    item = reg.item(item_id)
    if not item.model_assessed:  # MDE.A9: clinician-accepted risk statement or clinician-entered PHQ-9 item 9 only
        a9 = extras.get("a9") or {}
        if a9.get("present") and period.get("time_frame", "current") != "past":
            return {"item": item_id, "status": "present", "note": a9.get("source", "clinician-entered"), "evidence": a9.get("evidence", []), "counter_evidence": []}
        return {"item": item_id, "status": "insufficient_evidence", "note": "not assessed by the model (see risk statements and PHQ-9 item 9)",
                "evidence": [], "counter_evidence": []}
    if item.satisfied_by is None:
        return {"item": item_id, "status": "insufficient_evidence", "note": "", "evidence": [], "counter_evidence": []}
    status, notes, used = _combine(item.satisfied_by, period, extras)
    evidence = [e for _, p in used for e in p.get("evidence", [])]
    counter = [e for _, p in used for e in p.get("counter_evidence", [])]
    frequency = next((p.get("frequency") for _, p in used if p.get("frequency") and p.get("frequency") != "unclear"), "")
    return {"item": item_id, "status": status, "note": "; ".join(dict.fromkeys(notes)), "evidence": _unique(evidence),
            "counter_evidence": _unique(counter), "frequency": frequency}


def _unique(cited: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in cited:
        key = (c.get("segment_id"), c.get("kind"))
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _exclusion_status(proposal: dict | None) -> str:
    if proposal is None:
        return "not_assessed"
    if proposal.get("status") == "present":
        return "cause_evidence"
    if proposal.get("status") in {"absent", "explicit_denial"}:
        return "ruled_out_cited"
    return "not_assessed"


def short_id(element_id: str) -> str:
    return element_id.split(".", 1)[1].replace(".", " ") if "." in element_id else element_id


def evaluate_element(reg: DsmRegistry, el, period: dict, extras: dict, results: dict) -> dict:
    out = {"id": el.id, "type": el.type, "label": el.label or (reg.item(el.item).label if el.item else el.id), "status": "unknown",
           "detail": "", "items": [], "evidence": [], "counter_evidence": [], "ask": ""}
    if el.type in {"item", "judgement"}:
        res = item_status(reg, el.item, period, extras)
        out["items"] = [res]
        s = res["status"]
        out["status"] = "met" if s == "present" else "not_met" if s in NOT_SATISFIED else "unknown"
        if el.type == "judgement" and out["status"] == "unknown":
            out["detail"] = "clinical judgement"
        out["detail"] = out["detail"] or {"present": "present", "absent": "absent", "explicit_denial": "denied by client", "subthreshold": "below threshold",
                                          "not_linked": res["note"], "conflicting": "conflicting accounts", "insufficient_evidence": "not established"}.get(s, s)
        if s == "conflicting" and res["note"]:
            out["detail"] = res["note"]
    elif el.type == "count":
        rows = [item_status(reg, i, period, extras) for i in el.items]
        out["items"] = rows
        present = [r["item"] for r in rows if r["status"] == "present"]
        open_ = [r for r in rows if r["status"] in {"insufficient_evidence", "conflicting"}]
        need = el.min
        only = el.min_if_only
        if only:
            props = period["proposals"]
            if (props.get(only["present"]) or {}).get("status") == "present" and (props.get(only["absent"]) or {}).get("status") != "present":
                need = only["min"]
        core_ok = not el.must_include_any or any(i in present for i in el.must_include_any)
        core_out = bool(el.must_include_any) and all(r["status"] in NOT_SATISFIED for r in rows if r["item"] in el.must_include_any)
        if len(present) >= need and core_ok:
            out["status"] = "met"
        elif core_out or len(present) + len(open_) < need:
            out["status"] = "not_met"
        elif el.must_include_any and core_ok:
            out["status"] = "partial"  # a core item is present but the count is not reached yet
        includes = [short_id(i) for i in el.must_include_any if i in present]
        out["detail"] = f"{len(present)} of {len(el.items)} present (needs {need}" + (f", including {' or '.join(short_id(i) for i in el.must_include_any)}" if el.must_include_any else "") + ")" \
            + (f", includes {', '.join(includes)}" if includes else "")
    elif el.type == "duration":
        days, hosp = period.get("duration_days"), period.get("hospitalised")
        out["evidence"] = period.get("duration_evidence", [])
        if el.hospitalised_any and hosp:
            out["status"], out["detail"] = "met", "hospitalised (any duration counts)"
        elif days is None:
            out["detail"] = "duration not established"
        elif days >= el.min_days:
            out["status"], out["detail"] = "met", f"about {_days_text(days)} cited"
        elif out["evidence"]:
            out["status"], out["detail"] = "not_met", f"about {_days_text(days)} cited, needs {_days_text(el.min_days)}"
        else:
            out["detail"] = f"about {_days_text(days)}, not cited"
    elif el.type in {"exclusion", "note"}:
        states = []
        for key in el.features:
            proposal = period["proposals"].get(key) or extras.get("global", {}).get(key)
            states.append((key, _exclusion_status(proposal), proposal))
            if proposal:
                out["evidence"] += proposal.get("evidence", [])
        labels = {k: reg.feature(k).label.lower() for k, _, _ in states}
        if el.type == "note":
            cited = [k for k, s, _ in states if s == "cause_evidence"]
            out["status"] = "info"
            out["detail"] = f"{', '.join(labels[k] for k in cited)} cited: clinical judgement" if cited else "none cited"
        elif any(s == "cause_evidence" for _, s, _ in states):
            out["status"] = "contradicted"
            out["detail"] = "possible cause cited: " + ", ".join(labels[k] for k, s, _ in states if s == "cause_evidence")
        elif all(s == "ruled_out_cited" for _, s, _ in states):
            out["status"], out["detail"] = "met", "ruled out (cited): " + ", ".join(labels[k] for k, _, _ in states)
        else:
            ruled = [labels[k] for k, s, _ in states if s == "ruled_out_cited"]
            out["detail"] = "not assessed" + (f" (ruled out: {', '.join(ruled)})" if ruled else "")
        out["evidence"] = _unique(out["evidence"])
    elif el.type == "absence":
        rows = [item_status(reg, i, period, extras) for i in el.items]
        out["items"] = rows
        if any(r["status"] == "present" for r in rows):
            out["status"], out["detail"] = "contradicted", "marked impairment, hospitalisation or psychotic features cited"
        elif rows and all(r["status"] in {"absent", "explicit_denial", "subthreshold"} for r in rows):
            out["status"], out["detail"] = "met", "not severe enough to be manic (cited)"
        else:
            out["detail"] = "severity not established"
    elif el.type in {"episode", "all_episodes", "never_episode"}:
        keys = [el.condition] if el.type == "episode" else el.conditions
        labels = {k: (results.get(k) or {}).get("label", "insufficient_evidence") for k in keys}
        names = {k: reg.condition(k).label.lower() for k in keys}
        for k in keys:
            res = results.get(k) or {}
            out["evidence"] += [e for line in res.get("elements", []) for e in line.get("evidence", [])][:6]
        if el.type == "never_episode":
            if any(v == "likely" for v in labels.values()):
                out["status"], out["detail"] = "contradicted", "likely " + ", ".join(names[k] for k, v in labels.items() if v == "likely")
            elif all(v == "not_met" for v in labels.values()):
                out["status"], out["detail"] = "met", "none met (cited)"
            else:
                possible = [names[k] for k, v in labels.items() if v == "possible"]
                out["detail"] = ("possible " + ", ".join(possible) + ": ask") if possible else "history not established"
        else:
            values = list(labels.values())
            if all(v == "likely" for v in values):
                out["status"] = "met"
            elif any(v == "not_met" for v in values):
                out["status"] = "not_met"
            elif all(v in {"likely", "possible"} for v in values):
                out["status"] = "partial"
            out["detail"] = "; ".join(f"{names[k]}: {LABEL_TEXT[v].lower()}" for k, v in labels.items())
        out["evidence"] = _unique(out["evidence"])
    elif el.type == "subthreshold_course":
        groups_ok = {}
        for g in el.groups:
            keys = [f.key for f in reg.features.features if f.group == g]
            groups_ok[g] = any((period["proposals"].get(k) or {}).get("status") in {"subthreshold", "present"} for k in keys)
            out["evidence"] += [e for k in keys for e in (period["proposals"].get(k) or {}).get("evidence", [])]
        days = period.get("duration_days")
        if all(groups_ok.values()) and days is not None and days >= (el.min_days or 0):
            out["status"], out["detail"] = "met", f"milder high and low periods over about {_days_text(days)}"
        elif all(groups_ok.values()) and days is not None and period.get("duration_evidence"):
            out["status"], out["detail"] = "not_met", f"about {_days_text(days)} cited, needs two years"
        else:
            out["detail"] = "two-year course of milder high and low periods not established"
        out["evidence"] = _unique(out["evidence"])[:6]
    if not out["evidence"]:
        out["evidence"] = _unique([e for r in out["items"] for e in r.get("evidence", [])])
    out["counter_evidence"] = _unique(out["counter_evidence"] + [e for r in out["items"] for e in r.get("counter_evidence", [])])
    if out["status"] in {"unknown", "partial"}:
        out["ask"] = el.ask
    out["line"] = f"{short_id(el.id)}: {out['detail']} {MARK[out['status']]}".strip()
    return out


def _days_text(days: int) -> str:
    if days >= 540:
        return f"{round(days / 365)} years"
    if days >= 330:
        return "1 year"
    if days >= 60:
        return f"{round(days / 30)} months"
    if days >= 14:
        return f"{round(days / 7)} weeks"
    return f"{days} day{'s' if days != 1 else ''}"


def label_for(cond, elements: list[dict]) -> str:
    by_id = {e["id"]: e for e in elements}
    core = by_id[cond.core]
    cited_core = bool(core["evidence"] or core["counter_evidence"] or any(r.get("evidence") for r in core["items"]))
    if core["status"] == "not_met" and cited_core:
        return "not_met"
    rule_out = {"absence", "never_episode"}
    for el, res in zip(cond.elements, elements):
        if el.type in rule_out and res["status"] == "contradicted" and (el.on_present or "not_met") == "not_met":
            return "not_met"
    required = [res for el, res in zip(cond.elements, elements) if el.type in REQUIRED]
    judgement_blocked = any(res["status"] == "not_met" for el, res in zip(cond.elements, elements) if el.type == "judgement")
    exclusion_cited = any(res["status"] == "contradicted" for el, res in zip(cond.elements, elements) if el.type == "exclusion")
    if core["status"] == "met" and all(r["status"] == "met" for r in required) and not exclusion_cited and not judgement_blocked:
        return "likely"
    if core["status"] in {"met", "partial"}:
        return "possible"
    return "insufficient_evidence"


def evaluate_condition(reg: DsmRegistry, key: str, periods: list[dict], extras: dict, results: dict) -> dict:
    cond = reg.condition(key)
    relevant = set(reg.condition_features(key))
    candidates = periods if cond.per_period else [_merged(periods)]
    if cond.per_period:
        candidates = [p for p in periods if relevant & set(p["proposals"])] or [periods[0] if periods else _empty_period()]
    evaluations = []
    for period in candidates:
        elements = [evaluate_element(reg, el, period, extras, results) for el in cond.elements]
        evaluations.append((label_for(cond, elements), period, elements))
    label, period, elements = min(evaluations, key=lambda x: ORDER.index(x[0]))
    required = [res for el, res in zip(cond.elements, elements) if el.type in REQUIRED]
    out = {"condition_key": key, "label": label, "label_text": f"Criteria pattern consistent with {cond.label.lower()}: {LABEL_TEXT[label].lower()}",
           "footer": reg.conditions.label_footer, "role": cond.role,
           "period": {k: period.get(k) for k in ("id", "label", "time_frame", "duration_days", "hospitalised")},
           "elements": elements, "criteria_met_count": sum(r["status"] == "met" for r in required), "criteria_required": len(required),
           "codes": {"icd10cm": cond.icd10cm, "icd11": cond.icd11}, "differentials": [d.model_dump() for d in cond.differentials],
           "specifiers": [], "has_evidence": any(e["evidence"] or e["counter_evidence"] for e in elements),
           "periods_considered": [p.get("id") for _, p, _ in evaluations]}
    out["missing_information"] = list(dict.fromkeys(e["ask"] for e in elements if e["ask"] and e["status"] in {"unknown", "partial"}))
    if any(e["status"] == "info" and "cited" in e["detail"] and not e["detail"].startswith("none") for e in elements):
        out["missing_information"].append("A recent loss is cited: consider whether the presentation is an expected response to the loss (clinical judgement).")
    return out


def _empty_period() -> dict:
    return {"id": "current", "label": "Current", "time_frame": "current", "duration_days": None, "hospitalised": False, "duration_evidence": [], "proposals": {}}


def _merged(periods: list[dict]) -> dict:
    """Disorder-level conditions read episode results, but exclusions and impairment come from any period."""
    merged = _empty_period()
    merged.update(id="all", label="All periods")
    for p in periods:
        for key, prop in p["proposals"].items():
            current = merged["proposals"].get(key)
            if current is None or ORDER_STATUS.index(prop.get("status", "insufficient_evidence")) < ORDER_STATUS.index(current.get("status", "insufficient_evidence")):
                merged["proposals"][key] = prop
    return merged


ORDER_STATUS = ["present", "conflicting", "explicit_denial", "absent", "subthreshold", "insufficient_evidence"]


def specifiers(reg: DsmRegistry, key: str, result: dict, results: dict, periods: list[dict], extras: dict) -> list[dict]:
    if result["label"] != "likely":
        return []
    cond = reg.condition(key)
    out = []
    if "severity" in cond.specifiers or "anxious_distress" in cond.specifiers:
        mde = results.get("mde") or {}
        period = next((p for p in periods if p.get("id") == (mde.get("period") or {}).get("id")), periods[0] if periods else _empty_period())
        count = next((e for e in mde.get("elements", []) if e["id"] == "MDE.A"), None)
        n = sum(r["status"] == "present" for r in count["items"]) if count else 0
        marked = any((period["proposals"].get(k) or {}).get("degree") == "marked" and (period["proposals"].get(k) or {}).get("status") == "present"
                     for k in ("imp.social", "imp.occupational", "imp.other"))
        if "severity" in cond.specifiers:
            level = "severe" if n >= 8 or (n == 7 and marked) else "moderate" if n == 7 or marked else "mild"
            out.append({"key": "severity", "label": f"Severity (suggested): {level}", "value": level,
                        "basis": f"{n} of 9 depressive items present{', impairment cited as marked' if marked else ''}. Project rule pending clinical verification.",
                        "code": SEVERITY_CODES[level]})
        if "anxious_distress" in cond.specifiers:
            rows = [item_status(reg, f"ANXD.{i}", period, extras) for i in range(1, 6)]
            k = sum(r["status"] == "present" for r in rows)
            if k >= 2:
                psycho = period["proposals"].get("feat.psychomotor_observable") or {}
                agitated = psycho.get("status") == "present" and psycho.get("qualifier") == "agitated"
                level = "mild" if k == 2 else "moderate" if k == 3 else "severe" if agitated else "moderate-severe"
                out.append({"key": "anxious_distress", "label": f"With anxious distress (suggested): {level}", "value": level,
                            "basis": f"{k} of 5 anxious-distress items present" + (" with motor agitation" if agitated else ""), "code": ""})
    if "performance_only" in cond.specifiers:
        period = next((p for p in periods if p.get("id") == result["period"]["id"]), periods[0] if periods else _empty_period())
        if item_status(reg, "SAD.PERF", period, extras)["status"] == "present":
            out.append({"key": "performance_only", "label": "Performance only (suggested)", "value": "performance_only",
                        "basis": "fear cited only for speaking or performing in public", "code": ""})
    return out


def evaluate_all(reg: DsmRegistry, periods: list[dict], extras: dict | None = None, keys: list[str] | None = None) -> dict[str, dict]:
    """Episodes first, then disorders and differentials. Returns {condition_key: result}."""
    extras = extras or {}
    periods = periods or [_empty_period()]
    wanted = keys or (reg.conditions.episode_conditions + reg.conditions.enabled_conditions + reg.conditions.differential_conditions)
    order = [k for k in reg.conditions.episode_conditions] + [k for k in wanted if k not in reg.conditions.episode_conditions]
    results: dict[str, dict] = {}
    for key in order:
        results[key] = evaluate_condition(reg, key, periods, extras, results)
        results[key]["specifiers"] = specifiers(reg, key, results[key], results, periods, extras)
        for spec in results[key]["specifiers"]:
            if spec.get("code"):
                results[key]["codes"]["suggested"] = spec["code"]
    return {k: v for k, v in results.items() if k in wanted or k in reg.conditions.episode_conditions}

"""AI PHQ-8 and GAD-7 estimates derived from DSM findings in code (``dsm-fast-derived-v1``; plan §4.7, approved Q2).

Each questionnaire item maps to criterion items (``registry/dsm/instrument_map.json``), and those to the features that
link to them. The item value is the highest value of its features:
``explicit_denial``/``absent`` → 0 · present: ``nearly every day`` → 3, ``more days than not``/``most of the day`` → 2,
``occasional``/``unclear`` → 1 · ``subthreshold`` → 1 · ``conflicting``/``insufficient_evidence`` → gap (never 0).
PHQ-9 item 9 is never derived. The result has the same shape as the v2 item estimates, so the existing AI-estimate
series, badges and review flow store and show it unchanged.
"""
from __future__ import annotations

from ..registry import dsm_registry

FREQUENCY_SCORE = {"nearly every day": 3, "more days than not": 2, "most of the day": 2, "occasional": 1, "unclear": 1}
NEVER = {("phq9", "9")}


def feature_value(finding: dict | None) -> int | None:
    if not finding:
        return None
    status = finding.get("status")
    if status in {"explicit_denial", "absent"}:
        return 0
    if status == "subthreshold":
        return 1
    if status == "present":
        return FREQUENCY_SCORE.get(finding.get("frequency") or "unclear", 1)
    return None


def item_features() -> dict[str, dict[str, list[str]]]:
    """{instrument: {item: [feature keys]}} from the instrument map and the feature links (context-gated links included: a questionnaire item does not ask about the cause)."""
    from ..registry import leaves
    reg = dsm_registry()
    out: dict[str, dict[str, list[str]]] = {}
    for instrument in ("phq9", "gad7"):
        out[instrument] = {}
        for item, criteria in reg.instrument_map["maps"][instrument].items():
            if (instrument, item) in NEVER:
                continue
            keys = [leaf.feature for c in criteria for leaf in leaves(reg.item(c).satisfied_by)]  # questionnaire items do not depend on the cause
            out[instrument][item] = list(dict.fromkeys(keys))
    return out


def derive(findings: dict[str, dict]) -> tuple[dict, dict]:
    out = {}
    for instrument, items in item_features().items():
        values = {}
        for item, keys in items.items():
            scored = [(feature_value(findings.get(k)), k) for k in keys]
            scored = [(v, k) for v, k in scored if v is not None]
            if not scored:
                values[item] = {"score": None, "status": "insufficient_evidence", "evidence_segment_ids": [], "basis": ""}
                continue
            value, key = max(scored, key=lambda x: x[0])
            f = findings[key]
            label = dsm_registry().feature(key).label.lower()
            basis = (f"Derived from the {label} finding ({f['status'].replace('_', ' ')}"
                     + (f", {f['frequency']}" if f["status"] == "present" else "") + ").")
            values[item] = {"score": value, "status": "explicit_denial" if value == 0 else "rated",
                            "evidence_segment_ids": list(f.get("evidence_segment_ids", [])), "basis": basis, "evidence_strength": "indirect"}
        out[instrument] = values
    return out["phq9"], out["gad7"]

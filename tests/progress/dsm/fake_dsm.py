"""Scripted DSM model outputs for tests (no model). Specs cite turns by text so they follow alias numbering."""
from tests.progress.fake_runtime import scripted

EMPTY = {"status": "insufficient_evidence", "who": "client_report", "frequency": "unclear", "onset_or_duration": "", "time_frame": "unclear",
         "period_label": "", "qualifier": None, "degree": None, "contexts": [], "refs": [], "counter_refs": [],
         "counter_check": {"searched": True, "alternative_explanation": ""}, "basis": ""}


def find(data, text):
    return [r["id"] for r in data["sources"] if text.lower() in r["text"].lower()][:1]


def refs(data, texts):
    return [a for t in texts for a in find(data, t)]


def stage_a(findings=None, risks=None, items=None):
    """findings: {feature: {"status", "refs_text": [...], "counter_text": [(text, kind)], ...}}; risks: [{"category", "refs_text"}]."""
    findings, risks = findings or {}, risks or []
    legacy = scripted(items=items or {})

    def respond(system, data):
        req = data["requested"]
        if "features" not in req and not req.get("risk_statements"):
            return legacy(system, data)
        out = {"limitations": []}
        if req.get("features"):
            out["features"] = {}
            for f in req["features"]:
                spec = findings.get(f["key"])
                cited = refs(data, spec.get("refs_text", [])) if spec else []
                if not spec or not cited:
                    out["features"][f["key"]] = dict(EMPTY)
                    continue
                counter = [{"ref": a, "kind": kind} for text, kind in spec.get("counter_text", []) for a in find(data, text)]
                out["features"][f["key"]] = {**EMPTY, "status": spec.get("status", "present"), "who": spec.get("who", "client_report"),
                                             "frequency": spec.get("frequency", "nearly every day"), "onset_or_duration": spec.get("onset", ""),
                                             "time_frame": spec.get("time_frame", "current_episode"), "qualifier": spec.get("qualifier"),
                                             "degree": spec.get("degree"), "contexts": spec.get("contexts", []), "refs": cited, "counter_refs": counter,
                                             "counter_check": {"searched": spec.get("searched", True), "alternative_explanation": ""},
                                             "basis": spec.get("basis", "Paraphrased from the cited turns.")}
        if req.get("risk_statements"):
            out["risk_statements"] = [{"category": r["category"], "refs": refs(data, r["refs_text"]), "basis": r.get("basis", "Flagged for clinician review.")}
                                      for r in risks if refs(data, r["refs_text"])]
        return out
    return respond


def stage_b(proposals=None, periods=None, model_labels=None):
    """proposals: {feature: {"status", "refs_text", "counter_text": [(text, kind)], "contexts", ...}};
    periods: [{"label", "time_frame", "duration_days", "duration_text": [...], "features": [...], "hospitalised"}]."""
    proposals, periods = proposals or {}, periods or []

    def respond(system, data):
        if "requested_features" in data:
            out = {}
            for f in data["requested_features"]:
                spec = proposals.get(f["key"])
                cited = refs(data, spec.get("refs_text", [])) if spec else []
                if not spec or not cited:
                    out[f["key"]] = {"status": "insufficient_evidence", "contexts": [], "degree": None, "qualifier": None, "frequency": "unclear",
                                     "time_frame": "unclear", "period_note": "", "evidence": [], "counter_evidence": []}
                    continue
                out[f["key"]] = {"status": spec.get("status", "present"), "contexts": spec.get("contexts", []), "degree": spec.get("degree"),
                                 "qualifier": spec.get("qualifier"), "frequency": spec.get("frequency", "nearly every day"),
                                 "time_frame": spec.get("time_frame", "current"), "period_note": spec.get("period_note", ""),
                                 "evidence": [{"ref": a, "basis": "Paraphrased support."} for a in cited],
                                 "counter_evidence": [{"ref": a, "kind": kind, "basis": ""} for text, kind in spec.get("counter_text", []) for a in find(data, text)]}
            return {"features": out}
        keys = {p["feature"] for p in data["proposals"]}
        return {"periods": [{"label": p["label"], "time_frame": p.get("time_frame", "current"), "duration_days": p.get("duration_days"),
                             "hospitalised": p.get("hospitalised", False), "duration_refs": refs(data, p.get("duration_text", [])),
                             "features": [k for k in p.get("features", []) if k in keys]} for p in periods],
                "model_labels": {k: (model_labels or {}).get(k, "insufficient_evidence") for k in data["conditions"]}}
    return respond


def combined(a, b):
    return lambda system, data: b(system, data) if ("requested_features" in data or "proposals" in data) else a(system, data)

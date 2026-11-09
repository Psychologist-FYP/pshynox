"""Hand-built proposals and periods for the DSM rule engine (fictional)."""
from services.progress.dsm.registry import dsm_registry

REG = dsm_registry()


def cite(sid="seg-1", role="CLIENT", kind=None):
    out = {"segment_id": sid, "session_id": "s1", "start_ms": 0, "end_ms": 1000, "speaker_role": role, "basis": "paraphrase"}
    if kind:
        out["kind"] = kind
    return out


def prop(status="present", contexts=(), degree=None, qualifier=None, frequency="nearly every day", evidence=None, counter=None, sid=None):
    ev = evidence if evidence is not None else ([cite(sid or "seg-1")] if status != "insufficient_evidence" else [])
    return {"status": status, "contexts": list(contexts), "degree": degree, "qualifier": qualifier, "frequency": frequency,
            "evidence": ev, "counter_evidence": counter or []}


def period(proposals, *, days=None, hospitalised=False, pid="P1", time_frame="current", cited_duration=True):
    return {"id": pid, "label": pid, "time_frame": time_frame, "duration_days": days, "hospitalised": hospitalised,
            "duration_evidence": [cite("seg-dur")] if days is not None and cited_duration else [], "proposals": proposals}


MDE_FEATURES = ["feat.depressed_mood", "feat.anhedonia", "feat.appetite_weight", "feat.sleep_insomnia_hypersomnia",
                "feat.psychomotor_observable", "feat.fatigue", "feat.worthlessness_guilt", "feat.concentration"]
NO_CAUSE = {"excl.substance": prop("absent"), "excl.medical": prop("absent")}


def depressive(n, *, core=True, days=21, impair=True, extra=None):
    features = MDE_FEATURES if core else MDE_FEATURES[2:]
    p = {f: prop(contexts=["low_mood"]) for f in features[:n]}
    if impair:
        p["imp.occupational"] = prop(contexts=["low_mood"])
    p.update(extra or {})
    return period(p, days=days)

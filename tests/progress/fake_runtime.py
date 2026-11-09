"""A scripted stand-in for services.note_generation.runtime.Runtime (no model, no network)."""
import json
import threading

from services.note_generation.runtime import GenerationError, OutputLimitError


class FakeRuntime:
    """Answers each request with ``responder(system, data)``. Token use is approximated as characters / 4."""

    def __init__(self, responder, *, context=4096, limit_tasks=None, fail_calls=0, sha="f" * 64):
        self.responder = responder
        self.context, self.safety_margin = context, 64
        self.metrics = {"calls": [], "runtime_version": "fake-1", "inference_totals": {}}
        self.model = {"sha256": sha}  # a different sha simulates another model version (cache miss)
        self.cancel = threading.Event()
        self.process = None
        self.limit_tasks = limit_tasks      # raise OutputLimitError when more tasks are requested
        self.fail_calls = fail_calls
        self.requests = []
        self.closed = False

    def load(self, writer):
        self.writer = writer

    def check(self):
        if self.cancel.is_set():
            raise GenerationError("Cancelled by user")

    def prompt(self, system, data):
        return system + json.dumps(data, ensure_ascii=False)

    def count(self, text):
        return len(text) // 4

    def fits(self, system, data, output=700):
        return self.count(self.prompt(system, data)) + output + self.safety_margin <= self.context

    def generate(self, system, data, output=700):
        if not self.fits(system, data, output):
            raise GenerationError("Context budget exceeded")
        self.requests.append({"system": system, "data": data, "schema": getattr(self, "response_schema", None)})
        self.metrics["calls"].append({"tokens_predicted": 10})
        if self.fail_calls:
            self.fail_calls -= 1
            raise GenerationError("Local runtime request failed (fake)")
        requested = data.get("requested", {})
        n = len(requested.get("domains", [])) + len(requested.get("phq_items", [])) + len(requested.get("gad7_items", []))
        if self.limit_tasks is not None and n > self.limit_tasks:
            raise OutputLimitError("Incomplete output: generation reached its token limit; result withheld")
        return self.responder(system, data)

    def sample_memory(self):
        pass

    def close(self):
        self.closed = True


def alias_for(data, needle, role=None):
    for row in data["sources"]:
        if needle.lower() in row["text"].lower() and (role is None or row["speaker_role"] == role):
            return row["id"]
    raise AssertionError(f"no source containing {needle!r}")


def scripted(domains=None, items=None, risks=None, plans=None, limitations=None):
    """Build a Stage A responder. Values may reference sources by text: {'refs_text': ['slept']}."""
    domains, items, risks, plans = domains or {}, items or {}, risks or [], plans or {}

    def resolve(data, spec):
        refs = list(spec.get("refs", []))
        for text in spec.get("refs_text", []):
            try:
                refs.append(alias_for(data, text))
            except AssertionError:
                pass  # turn not in this packet/session
        return refs

    def present(data, spec):
        """A scripted value applies only when its cited turn exists in the supplied sources."""
        return spec is not None and (not spec.get("refs_text") or bool(resolve(data, spec)))

    def respond(system, data):
        req = data["requested"]
        out = {"limitations": limitations or []}
        if "domains" in req:
            out["domains"] = {}
            for d in req["domains"]:
                spec = domains.get(d["key"])
                if not present(data, spec):
                    out["domains"][d["key"]] = {"status": "insufficient_evidence", "rating": None, "who": "client_report", "timeframe": "unclear",
                                                "direction": None, "frequency": "", "intensity": "", "duration": "", "refs": [], "basis": ""}
                else:
                    out["domains"][d["key"]] = {"status": "rated", "rating": None, "who": "client_report", "timeframe": "current", "direction": None,
                                                "frequency": "", "intensity": "", "duration": "", "basis": "Client described this.",
                                                **{k: v for k, v in spec.items() if k not in {"refs_text"}}, "refs": resolve(data, spec)}
        for field, instrument in (("phq_items", "phq9"), ("gad7_items", "gad7")):
            if field in req:
                out[field] = {}
                for i in req[field]:
                    spec = items.get(f"{instrument}:{i['id']}")
                    out[field][i["id"]] = ({"status": spec.get("status", "rated"), "score": spec["score"], "refs": resolve(data, spec),
                                            "basis": spec.get("basis", "Client reported it."), "evidence_strength": spec.get("evidence_strength")}
                                           if present(data, spec) else {"status": "insufficient_evidence", "score": None, "refs": [], "basis": ""})
        if req.get("risk_statements"):
            out["risk_statements"] = [{"category": r["category"], "refs": resolve(data, r), "basis": r.get("basis", "Statement flagged for review.")} for r in risks if present(data, r)]
        if "plans" in req:
            out["plan_followups"] = [{"plan_id": p["plan_id"], **({"status": "not_discussed", "refs": [], "basis": ""} if p["plan_id"] not in plans else
                                      {"status": plans[p["plan_id"]]["status"], "refs": resolve(data, plans[p["plan_id"]]), "basis": "Client said it was attempted."})}
                                     for p in req["plans"]]
        return out
    return respond

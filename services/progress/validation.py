"""Deterministic checks applied to every model output. Invalid items are withheld with a visible limitation.

Structural problems raise ``GenerationError('Invalid output schema …')`` so the shared Recovery layer can request
one repair; content problems withhold only the affected item.
"""
from __future__ import annotations

import re

from services.note_generation.runtime import GenerationError

RELATIVES = (r"sister|brother|mother|mom|mum|father|dad|parents?|friend|partner|husband|wife|son|daughter|boyfriend|"
             r"girlfriend|roommate|flatmate|colleague|coworker|boss|cousin|aunt|uncle|grandmother|grandfather|grandma|"
             r"grandpa|kids?|child|children|baby|neighbou?r|teacher|classmate|fianc[eé]e?")
THIRD_PARTY = re.compile(rf"\b(?:my|his|her|their|our|your)\s+(?:\w+\s+)?(?:{RELATIVES})\b|^\s*(?:he|she|they)\b|\b(?:he|she|they)\s+(?:can't|cannot|doesn't|don't|isn't|is|was|has|had|keeps?|never)\b", re.I)
RELATIVE_PHRASE = re.compile(rf"\b(?:my|our)\s+(?:\w+\s+)?(?:{RELATIVES})\b", re.I)
FIRST_PERSON = re.compile(r"\b(?:i|i'm|im|i've|i'd|i'll|me|my|mine|myself)\b", re.I)
MAX_QUOTE_WORDS = 15

DOMAIN_STATUSES = {"rated", "explicit_denial", "conflicting", "insufficient_evidence"}
TIMEFRAMES = {"current", "past", "unclear"}
RISK_CATEGORIES = {"self_harm", "suicidal_ideation", "harm_to_others", "other_safety"}
PLAN_STATUSES = {"discussed_done", "discussed_partial", "discussed_not_done", "not_discussed"}


def words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.casefold())


def other_person_only(text: str) -> bool:
    """True when a turn is about someone else and carries no first-person reference to the client."""
    if not THIRD_PARTY.search(text):
        return False
    remainder = RELATIVE_PHRASE.sub(" ", text)
    return not FIRST_PERSON.search(remainder)


def copied_run(output: str, sources: list[str], limit: int = MAX_QUOTE_WORDS) -> bool:
    """True when ``output`` reproduces more than ``limit`` consecutive words of any source."""
    out = words(output)
    if len(out) <= limit:
        return False
    grams = {tuple(out[i:i + limit + 1]) for i in range(len(out) - limit)}
    for text in sources:
        src = words(text)
        if any(tuple(src[i:i + limit + 1]) in grams for i in range(len(src) - limit)):
            return True
    return False


def contains_name(text: str, names: list[str]) -> bool:
    lowered = f" {text.casefold()} "
    return any(re.search(rf"\b{re.escape(n.casefold())}\b", lowered) for n in names if len(n) >= 3)


class Checker:
    """Map aliases back to source ids and accumulate withheld items with reasons."""

    def __init__(self, aliases: dict[str, dict], names: list[str] | None = None):
        self.aliases = aliases          # alias -> source row (id, speaker_role, text, session_id, start_ms, ...)
        self.names = [n for name in (names or []) for n in name.split() if n]
        self.limitations: list[str] = []
        self.withheld: list[dict] = []

    def withhold(self, kind: str, key: str, reason: str, item=None):
        self.limitations.append(f"Withheld {kind} {key}: {reason}")
        self.withheld.append({"kind": kind, "key": key, "reason": reason, "item": item})

    def refs(self, kind: str, key: str, refs) -> list[dict] | None:
        if not isinstance(refs, list) or any(not isinstance(r, str) for r in refs):
            self.withhold(kind, key, "refs must be a list of supplied source aliases")
            return None
        unknown = [r for r in refs if r not in self.aliases]
        if unknown:
            self.withhold(kind, key, f"cites sources that were not supplied ({', '.join(unknown[:3])})")
            return None
        return [self.aliases[r] for r in dict.fromkeys(refs)]

    def text_ok(self, kind: str, key: str, texts: list[str], sources: list[dict]) -> bool:
        joined = " ".join(t for t in texts if t)
        # Any supplied turn counts, cited or not: no stored quotation over the limit.
        if copied_run(joined, [s["text"] for s in self.aliases.values()]):
            self.withhold(kind, key, f"stored text copies more than {MAX_QUOTE_WORDS} consecutive source words")
            return False
        if contains_name(joined, self.names):
            self.withhold(kind, key, "stored text contains a personal name")
            return False
        return True

    def attributable(self, kind: str, key: str, status: str, who: str, sources: list[dict]) -> bool:
        clients = [s for s in sources if s["speaker_role"] == "CLIENT"]
        clinicians = [s for s in sources if s["speaker_role"] == "CLINICIAN"]
        if status == "explicit_denial" and not clients:
            self.withhold(kind, key, "an explicit denial needs a client turn")
            return False
        if status == "rated":
            if who == "clinician_observation":
                if not clinicians:
                    self.withhold(kind, key, "a clinician observation needs a clinician turn")
                    return False
            elif not clients:
                self.withhold(kind, key, "a rated value needs client evidence or a clinician observation")
                return False
        if status in {"rated", "explicit_denial", "conflicting"} and clients and who != "clinician_observation":
            if all(other_person_only(s["text"]) for s in clients):
                self.withhold(kind, key, "cited client turns describe another person, not the client")
                return False
        return True


def _need(value: dict, key: str, kind=dict):
    if not isinstance(value.get(key), kind):
        raise GenerationError(f"Invalid output schema: {key} required")
    return value[key]


def validate_extraction(value, data: dict, checker: Checker, *, strict_structure: bool = True, version: str = "v1") -> dict:
    """Validate one Stage A group response. Returns accepted items keyed like the response."""
    if not isinstance(value, dict):
        raise GenerationError("Invalid output schema: object required")
    tasks = data["tasks"]
    domain_keys = [t["key"] for t in tasks if t["kind"] == "domain"]
    items = {"phq_items": [t["key"] for t in tasks if t["kind"] == "phq9"], "gad7_items": [t["key"] for t in tasks if t["kind"] == "gad7"]}
    out = {"domains": {}, "phq_items": {}, "gad7_items": {}, "risk_statements": [], "plan_followups": []}
    if domain_keys:
        domains = _need(value, "domains")
        missing = set(domain_keys) - set(domains)
        if missing and strict_structure:
            raise GenerationError(f"Invalid output schema: every requested domain required ({', '.join(sorted(missing))})")
        allowed = {t["key"]: t.get("directions", []) for t in tasks if t["kind"] == "domain"}
        for key in domain_keys:
            item = domains.get(key)
            if not isinstance(item, dict):
                checker.withhold("domain", key, "missing from the model output")
                continue
            status = item.get("status")
            if status not in DOMAIN_STATUSES:
                checker.withhold("domain", key, "invalid status", item)
                continue
            sources = checker.refs("domain", key, item.get("refs", []))
            if sources is None:
                continue
            rating = item.get("rating")
            if status == "rated":
                if not isinstance(rating, int) or isinstance(rating, bool) or not 0 <= rating <= 3:
                    checker.withhold("domain", key, "a rated value needs an integer rating 0-3", item)
                    continue
            else:
                rating = None
            if status != "insufficient_evidence" and not sources:
                checker.withhold("domain", key, f"{status} needs cited turns", item)
                continue
            who = item.get("who") or "client_report"
            if not checker.attributable("domain", key, status, who, sources):
                continue
            direction = item.get("direction") or None
            if direction is not None and direction not in allowed[key]:
                direction = None
                checker.limitations.append(f"Dropped unsupported direction for {key}")
            timeframe = item.get("timeframe") if item.get("timeframe") in TIMEFRAMES else "unclear"
            texts = [str(item.get(k) or "")[:300] for k in ("basis", "frequency", "intensity", "duration")]
            if not checker.text_ok("domain", key, texts, sources):
                continue
            out["domains"][key] = {"status_value": status, "rating": rating, "direction": direction, "timeframe": timeframe,
                                   "who": who, "frequency_text": texts[1], "intensity_text": texts[2], "duration_text": texts[3],
                                   "basis": texts[0], "evidence_segment_ids": [s["id"] for s in sources]}
    for field, keys in items.items():
        if not keys:
            continue
        given = _need(value, field)
        instrument = "phq9" if field == "phq_items" else "gad7"
        for extra in set(given) - set(keys):
            checker.limitations.append(f"Ignored unrequested {instrument} item {extra}")
        for key in keys:
            item = given.get(key)
            label = f"{instrument} item {key}"
            if not isinstance(item, dict):
                checker.withhold("item", label, "missing from the model output")
                continue
            status = item.get("status", "rated")
            score = item.get("score")
            sources = checker.refs("item", label, item.get("refs", []))
            if sources is None:
                continue
            if version == "v2" and (status == "explicit_denial" or (status == "rated" and score == 0)):
                # v2: absent or described as normal -> score 0, needs a client turn.
                if not sources or not checker.attributable("item", label, "explicit_denial", "client_report", sources):
                    if not sources:
                        checker.withhold("item", label, "a denial needs cited turns", item)
                    continue
                basis = str(item.get("basis") or "")[:300]
                if not checker.text_ok("item", label, [basis], sources):
                    continue
                out[field][key] = {"score": 0, "status": "explicit_denial", "evidence_segment_ids": [s["id"] for s in sources], "basis": basis,
                                   "evidence_strength": item.get("evidence_strength") or "indirect"}
                continue
            if status == "insufficient_evidence" or score is None:
                out[field][key] = {"score": None, "status": "insufficient_evidence", "evidence_segment_ids": [], "basis": ""}
                continue
            if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= 3:
                checker.withhold("item", label, "score must be 0-3", item)
                continue
            if not sources or not checker.attributable("item", label, "rated", item.get("who") or "client_report", sources):
                if not sources:
                    checker.withhold("item", label, "an estimate needs cited turns", item)
                continue
            basis = str(item.get("basis") or "")[:300]
            if not checker.text_ok("item", label, [basis], sources):
                continue
            out[field][key] = {"score": score, "status": "rated", "evidence_segment_ids": [s["id"] for s in sources], "basis": basis,
                               **({"evidence_strength": item.get("evidence_strength") or "indirect"} if version == "v2" else {})}
    if any(t["kind"] == "risk" for t in tasks):
        for i, item in enumerate(_need(value, "risk_statements", list)):
            if not isinstance(item, dict) or item.get("category") not in RISK_CATEGORIES:
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
    plan_tasks = {t["key"] for t in tasks if t["kind"] == "plan"}
    if plan_tasks:
        for item in _need(value, "plan_followups", list):
            if not isinstance(item, dict) or item.get("plan_id") not in plan_tasks or item.get("status") not in PLAN_STATUSES:
                checker.withhold("plan follow-up", str(item.get("plan_id") if isinstance(item, dict) else "?"), "unknown plan or status", item)
                continue
            sources = checker.refs("plan follow-up", item["plan_id"], item.get("refs", []))
            if sources is None:
                continue
            if item["status"] != "not_discussed" and not sources:
                checker.withhold("plan follow-up", item["plan_id"], "a discussed plan needs cited turns", item)
                continue
            basis = str(item.get("basis") or "")[:300]
            if not checker.text_ok("plan follow-up", item["plan_id"], [basis], sources):
                continue
            out["plan_followups"].append({"plan_id": item["plan_id"], "status": item["status"], "basis": basis,
                                          "evidence_segment_ids": [s["id"] for s in sources]})
    for note in value.get("limitations", []) if isinstance(value.get("limitations"), list) else []:
        if isinstance(note, str) and note.strip():
            checker.limitations.append("Model note: " + note.strip()[:200])
    return out

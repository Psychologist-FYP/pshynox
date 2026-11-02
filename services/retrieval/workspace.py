"""Research-only file storage. This module never imports clinical database models."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
from uuid import uuid4

from services.retrieval.benchmark import load_dataset
from services.retrieval.prepare import digest, research_root, write_json
from services.retrieval.query import parse_query, QuerySyntaxError


class ResearchError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def identifier(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise ResearchError("Invalid research identifier", 404)
    return value


def progress(query, conversation):
    grades = query.get("judgments", {})
    reviewed = sum(s["id"] in grades for s in conversation["segments"])
    total = len(conversation["segments"])
    all_zero = reviewed == total and all(j["grade"] == 0 for j in grades.values())
    complete = reviewed == total and not query.get("draft", True) and (not all_zero or query.get("no_answer_confirmed", False))
    return {"reviewed": reviewed, "total": total, "complete": bool(complete),
            "needs_no_answer_confirmation": all_zero and not query.get("no_answer_confirmed", False)}


class ResearchStore:
    def __init__(self, root=None):
        self.root = Path(root or research_root())

    def dataset(self, name):
        path = self.root / "datasets" / f"{identifier(name)}.json"
        if not path.is_file():
            raise ResearchError("Dataset not prepared. Run .venv/bin/python -m services.retrieval.prepare", 404)
        return json.loads(path.read_text())

    def state(self, name):
        dataset = self.dataset(name)
        path = self.root / "annotations" / f"{identifier(name)}.json"
        value = json.loads(path.read_text())
        if value["dataset_version"] != dataset["version"]:
            raise ResearchError("Dataset changed; annotations must be migrated explicitly.", 409)
        return value

    @contextmanager
    def edit(self, name, revision):
        folder = self.root / "annotations"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{identifier(name)}.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = self.state(name)
            if revision != state["revision"]:
                raise ResearchError("Progress changed in another tab. Reload before saving again.", 409)
            yield state
            state["revision"] += 1
            write_json(folder / f"{name}.json", state)

    def conversation(self, name, cid):
        dataset = self.dataset(name)
        conversation = next((c for c in dataset["conversations"] if c["conversation_id"] == cid), None)
        if conversation is None:
            raise ResearchError("Conversation not found", 404)
        return conversation

    def overview(self, name):
        dataset, state = self.dataset(name), self.state(name)
        pilots = dataset["selection"]["pilot_ids"] + state["pilot_extra"]
        conversations = []
        for c in dataset["conversations"]:
            queries = [q for q in state["queries"] if q["conversation_id"] == c["conversation_id"]]
            statuses = [progress(q, c) for q in queries]
            conversations.append({"conversation_id": c["conversation_id"], "split": c["split"],
                                  "turns": len(c["segments"]), "pilot": c["conversation_id"] in pilots,
                                  "queries": len(queries), "complete_queries": sum(p["complete"] for p in statuses)})
        return {"dataset_id": name, "version": dataset["version"], "description": dataset["description"],
                "revision": state["revision"], "selection": {**dataset["selection"], "pilot_ids": pilots},
                "conversations": conversations}

    def detail(self, name, cid):
        conversation, state = self.conversation(name, cid), self.state(name)
        return {"conversation": conversation, "revision": state["revision"],
                "queries": [{**q, "progress": progress(q, conversation)} for q in state["queries"] if q["conversation_id"] == cid],
                "explored": cid in state["explored"]}

    def save_query(self, name, cid, payload):
        conversation = self.conversation(name, cid)
        with self.edit(name, payload["revision"]) as state:
            qid = payload.get("query_id") or "query-" + uuid4().hex
            existing = next((q for q in state["queries"] if q["query_id"] == qid), None)
            if existing and existing["conversation_id"] != cid:
                raise ResearchError("Query belongs to another conversation")
            if not payload["text"].strip() or not payload["intent"].strip():
                raise ResearchError("Query and intended evidence are required")
            if not payload["draft"]:
                try:
                    parse_query(payload["text"])
                except QuerySyntaxError as exc:
                    raise ResearchError(f"Correct query syntax before approving: {exc}") from exc
            query = deepcopy(existing) if existing else {"query_id": qid, "conversation_id": cid, "judgments": {}}
            if existing and (existing["text"] != payload["text"] or existing["intent"] != payload["intent"]):
                query["judgments"] = {}
                query["no_answer_confirmed"] = False
            query.update(text=payload["text"], intent=payload["intent"], draft=payload["draft"])
            if payload.get("no_answer_confirmed"):
                grades = query["judgments"]
                if len(grades) != len(conversation["segments"]) or any(j["grade"] != 0 for j in grades.values()):
                    raise ResearchError("Review every turn as grade 0 before confirming no relevant evidence")
                if query["draft"] or not payload.get("reviewer", "").strip():
                    raise ResearchError("Approve the query and provide a reviewer before confirming no answer")
                query["no_answer_confirmed"] = True
                query["no_answer_review"] = {"reviewer": payload["reviewer"], "at": datetime.now(timezone.utc).isoformat()}
            else:
                query["no_answer_confirmed"] = False
                query.pop("no_answer_review", None)
            if existing:
                state["queries"][state["queries"].index(existing)] = query
            else:
                state["queries"].append(query)
        return self.detail(name, cid)

    def grade(self, name, cid, qid, sid, payload):
        conversation = self.conversation(name, cid)
        if sid not in {s["id"] for s in conversation["segments"]}:
            raise ResearchError("Turn not found", 404)
        with self.edit(name, payload["revision"]) as state:
            query = next((q for q in state["queries"] if q["query_id"] == qid and q["conversation_id"] == cid), None)
            if query is None:
                raise ResearchError("Query not found", 404)
            if payload["grade"] is None:
                query["judgments"].pop(sid, None)
            else:
                if type(payload["grade"]) is not int or payload["grade"] not in (0, 1, 2):
                    raise ResearchError("Grade must be 0, 1, 2 or unreviewed")
                if not payload["reviewer"].strip() or not payload["rationale"].strip():
                    raise ResearchError("Reviewer and rationale are required for each reviewed turn")
                query["judgments"][sid] = {"grade": payload["grade"], "reviewer": payload["reviewer"],
                                          "rationale": payload["rationale"], "at": datetime.now(timezone.utc).isoformat()}
            query["no_answer_confirmed"] = False
            query.pop("no_answer_review", None)
        return self.detail(name, cid)

    def expand(self, name, cid, revision):
        self.conversation(name, cid)
        dataset = self.dataset(name)
        group = next(g for g in dataset["selection"]["groups"] if cid in g)
        with self.edit(name, revision) as state:
            state["pilot_extra"] = sorted(set(state["pilot_extra"] + group) - set(dataset["selection"]["pilot_ids"]))
        return self.overview(name)

    def mark_explored(self, name, cid, revision):
        if self.conversation(name, cid)["split"] == "test":
            raise ResearchError("Exploration is limited to development conversations to preserve held-out testing.")
        with self.edit(name, revision) as state:
            if cid not in state["explored"]:
                state["explored"].append(cid)

    def export(self, name, split):
        dataset, state = self.dataset(name), self.state(name)
        ids = set(dataset["selection"]["pilot_ids"] + state["pilot_extra"])
        conversations = [c for c in dataset["conversations"] if c["conversation_id"] in ids and c["split"] == split]
        if not conversations:
            raise ResearchError("No pilot conversations in this split")
        queries, judgments, missing = [], [], []
        for c in conversations:
            selected = [q for q in state["queries"] if q["conversation_id"] == c["conversation_id"]]
            if not selected:
                missing.append(c["conversation_id"] + ": no queries")
            for q in selected:
                p = progress(q, c)
                if not p["complete"]:
                    missing.append(f"{q['query_id']}: {p['reviewed']}/{p['total']} turns reviewed; approve draft and confirm no-answer if needed")
                queries.append({k: v for k, v in q.items() if k != "judgments"})
                for sid, judgment in q["judgments"].items():
                    judgments.append({**judgment, "query_id": q["query_id"], "conversation_id": c["conversation_id"], "source_segment_id": sid})
        if missing:
            remaining = f"; and {len(missing) - 8} more" if len(missing) > 8 else ""
            raise ResearchError(f"Export blocked. {len(missing)} incomplete query/conversation checks: " + "; ".join(missing[:8]) + remaining)
        result = {"description": dataset["description"], "dataset_id": name, "kind": dataset.get("kind", "dataset"),
                  "dataset_version": dataset["version"], "annotation_revision": state["revision"],
                  "annotation_status": "complete for selected pilot split; human judgments, not clinician validation",
                  "selection": {**dataset["selection"], "pilot_ids": sorted(ids)},
                  "explored_conversations": state["explored"], "source": dataset["source"],
                  "conversations": conversations, "queries": queries, "judgments": judgments}
        # Reuse the benchmark's authoritative validation without retaining temporary files.
        folder = self.root / "exports"
        path = folder / f"validate-{uuid4().hex}.json"
        write_json(path, result)
        try:
            load_dataset(path)
        finally:
            path.unlink()
        result["export_version"] = digest(result)
        return result

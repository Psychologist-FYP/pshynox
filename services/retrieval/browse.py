"""Fixture-backed browsing and navigation service.

The service deliberately keeps browsing separate from clinical workflow state.
Documents are transcript segments, hierarchy metadata is a sidecar fixture,
and links are navigational or lexical relationships only.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from services.api.schemas import TranscriptSegment

ROOT = Path(__file__).resolve().parents[2]
METADATA_PATH = ROOT / "packages" / "contracts" / "fixtures" / "browse_metadata.json"

CATEGORY_DEFINITIONS = [
    {"id": "gad-like-worry", "label": "GAD-like worry", "description": "Fictional worry-related excerpts."},
    {"id": "depressive-symptoms", "label": "Depressive symptoms", "description": "Fictional mood-related excerpts."},
    {"id": "everyday-functioning", "label": "Everyday functioning", "description": "Fictional daily-life and functioning excerpts."},
    {"id": "risk-related-language", "label": "Risk-related excerpts (search candidates, not a risk assessment)", "description": "Language candidates for clinician review only."},
]
TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)?")


def _tokens(value: str) -> list[str]:
    return TOKEN_RE.findall(value.lower())


def _metadata() -> dict[str, dict[str, str]]:
    with METADATA_PATH.open(encoding="utf-8") as handle:
        return {item["segment_id"]: item for item in json.load(handle)}


def _section_label(section: str) -> str:
    return {"subjective": "Subjective", "functioning": "Functioning", "other": "Other"}.get(section, section.title())


def documents(segments: Sequence[TranscriptSegment]) -> list[dict[str, Any]]:
    metadata = _metadata()
    result: list[dict[str, Any]] = []
    for segment in segments:
        item = metadata.get(segment.id, {})
        category_id = item.get("category_id", "everyday-functioning")
        category_label = item.get("category_label", "Everyday functioning")
        section = item.get("section", "other")
        result.append(
            {
                "id": segment.id,
                "segment_id": segment.id,
                "session_id": segment.session_id,
                "title": f"{segment.speaker_role.title()} excerpt at {segment.start_ms // 1000 // 60}:{segment.start_ms // 1000 % 60:02d}",
                "excerpt": segment.text,
                "text": segment.text,
                "speaker_role": segment.speaker_role.value,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "revision": segment.revision,
                "category_id": category_id,
                "category_label": category_label,
                "section": section,
                "section_label": _section_label(section),
            }
        )
    return sorted(result, key=lambda item: (item["session_id"], item["start_ms"], item["id"]))


def node_kind(node_id: str) -> str:
    if node_id == "root":
        return "root"
    return node_id.split(":", 1)[0]


def _node_id(kind: str, *parts: str) -> str:
    return ":".join((kind, *parts))


def node_documents(node_id: str, docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    if node_id == "root":
        return list(docs)
    kind, _, value = node_id.partition(":")
    if kind == "category":
        return [doc for doc in docs if doc["category_id"] == value]
    if kind == "session":
        return [doc for doc in docs if doc["session_id"] == value]
    if kind == "section":
        session_id, _, section = value.partition("/")
        return [doc for doc in docs if doc["session_id"] == session_id and doc["section"] == section]
    if kind == "segment":
        return [doc for doc in docs if doc["segment_id"] == value]
    return []


def node_info(node_id: str, docs: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    selected = node_documents(node_id, docs)
    if node_id == "root":
        return {"id": "root", "type": "root", "title": "FYP Repository", "label": "FYP Repository"}
    kind, _, value = node_id.partition(":")
    if kind == "category":
        definition = next((item for item in CATEGORY_DEFINITIONS if item["id"] == value), None)
        if definition is None and not selected:
            return None
        title = selected[0]["category_label"] if selected else definition["label"]
        return {"id": node_id, "type": kind, "title": title, "label": title}
    if not selected:
        return None
    if kind == "session":
        return {"id": node_id, "type": kind, "title": f"Session {value}", "label": f"Session {value}"}
    if kind == "section":
        session_id, _, section = value.partition("/")
        return {"id": node_id, "type": kind, "title": _section_label(section), "label": _section_label(section), "session_id": session_id}
    return {"id": node_id, "type": kind, "title": selected[0]["title"], "label": selected[0]["title"]}


def children(node_id: str, docs: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = node_documents(node_id, docs)
    if node_id == "root":
        values = [("category", doc["category_id"], doc["category_label"]) for doc in docs]
        known = [("category", item["id"], item["label"]) for item in CATEGORY_DEFINITIONS]
        entries = {value: label for _, value, label in known}
        entries.update({value: label for _, value, label in values})
        return [{"id": _node_id("category", value), "type": "category", "label": label, "count": sum(doc["category_id"] == value for doc in docs)} for value, label in entries.items()]
    kind = node_kind(node_id)
    if kind == "category":
        sessions = sorted({doc["session_id"] for doc in selected})
        return [{"id": _node_id("session", session), "type": "session", "label": f"Session {session}", "count": sum(doc["session_id"] == session for doc in selected)} for session in sessions]
    if kind == "session":
        sections = sorted({doc["section"] for doc in selected})
        session_id = selected[0]["session_id"]
        return [{"id": _node_id("section", f"{session_id}/{section}"), "type": "section", "label": _section_label(section), "count": sum(doc["section"] == section for doc in selected)} for section in sections]
    if kind == "section":
        return [{"id": _node_id("segment", doc["segment_id"]), "type": "segment", "label": doc["title"], "count": 1} for doc in selected]
    return []


def breadcrumb(node_id: str, docs: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    if node_id == "root":
        return [{"id": "root", "label": "FYP Repository"}]
    info = node_info(node_id, docs)
    if info is None:
        return []
    selected = node_documents(node_id, docs)
    category = selected[0]["category_id"] if selected else None
    session_id = selected[0]["session_id"] if selected else None
    items = [{"id": "root", "label": "FYP Repository"}]
    if node_kind(node_id) == "category" and not category:
        category = node_id.partition(":")[2]
        category_label = next((item["label"] for item in CATEGORY_DEFINITIONS if item["id"] == category), category)
        items.append({"id": node_id, "label": category_label})
    elif category:
        items.append({"id": _node_id("category", category), "label": selected[0]["category_label"]})
    if session_id and node_kind(node_id) in {"session", "section", "segment"}:
        items.append({"id": _node_id("session", session_id), "label": f"Session {session_id}"})
    if node_kind(node_id) in {"section", "segment"} and selected:
        section = selected[0]["section"]
        items.append({"id": _node_id("section", f"{session_id}/{section}"), "label": _section_label(section)})
    if node_kind(node_id) == "segment" and selected:
        items.append({"id": node_id, "label": selected[0]["title"]})
    return items


def search(query: str, docs: Sequence[dict[str, Any]], scope: str | None = None, k: int = 10) -> list[dict[str, Any]]:
    pool = node_documents(scope, docs) if scope else list(docs)
    terms = _tokens(query)
    if not terms or not pool or k <= 0:
        return []
    document_tokens = [_tokens(f"{doc['text']} {doc['category_label']} {doc['section']}") for doc in pool]
    document_frequency = Counter(term for tokens in document_tokens for term in set(tokens))
    average_length = sum(len(tokens) for tokens in document_tokens) / max(len(document_tokens), 1)
    ranked: list[dict[str, Any]] = []
    for doc, tokens in zip(pool, document_tokens):
        counts = Counter(tokens)
        score = 0.0
        for term in terms:
            frequency = counts[term]
            if not frequency:
                continue
            idf = math.log(1 + (len(pool) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            score += idf * ((frequency * 2.2) / (frequency + 1.2 * (0.75 + 0.25 * len(tokens) / max(average_length, 1))))
        if score:
            ranked.append({**doc, "score": round(score, 6), "query": query})
    return sorted(ranked, key=lambda item: (-item["score"], item["start_ms"], item["id"]))[:k]


def related(segment_id: str, docs: Sequence[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    source = next((doc for doc in docs if doc["segment_id"] == segment_id), None)
    if source is None:
        return []
    links: list[dict[str, Any]] = []
    same_session = [doc for doc in docs if doc["session_id"] == source["session_id"]]
    position = next(index for index, doc in enumerate(same_session) if doc["segment_id"] == segment_id)
    for offset, link_type in ((-1, "previous_segment"), (1, "next_segment")):
        target_index = position + offset
        if 0 <= target_index < len(same_session):
            target = same_session[target_index]
            links.append({"source_id": segment_id, "target_id": target["segment_id"], "link_type": link_type, "label": target["title"], "document": target})
    for target in docs:
        if target["segment_id"] != segment_id and target["section"] == source["section"] and target["session_id"] != source["session_id"]:
            links.append({"source_id": segment_id, "target_id": target["segment_id"], "link_type": "same_section_other_session", "label": target["title"], "document": target})
    for target in search(source["text"], docs, k=limit + 1):
        if target["segment_id"] != segment_id and all(item["target_id"] != target["segment_id"] for item in links):
            links.append({"source_id": segment_id, "target_id": target["segment_id"], "link_type": "lexically_similar", "label": target["title"], "document": target})
    return links


def graph(docs: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    nodes: dict[str, dict[str, Any]] = {"root": {"id": "root", "type": "root", "label": "FYP Repository"}}
    links: list[dict[str, Any]] = []
    for category in children("root", docs):
        nodes[category["id"]] = category
        links.append({"source_id": "root", "target_id": category["id"], "link_type": "hierarchy"})
        for session in children(category["id"], docs):
            nodes[session["id"]] = session
            links.append({"source_id": category["id"], "target_id": session["id"], "link_type": "hierarchy"})
            for section in children(session["id"], docs):
                nodes[section["id"]] = section
                links.append({"source_id": session["id"], "target_id": section["id"], "link_type": "hierarchy"})
                for segment in children(section["id"], docs):
                    nodes[segment["id"]] = segment
                    links.append({"source_id": section["id"], "target_id": segment["id"], "link_type": "hierarchy"})
    for doc in docs:
        for link in related(doc["segment_id"], docs):
            links.append({"source_id": _node_id("segment", link["source_id"]), "target_id": _node_id("segment", link["target_id"]), "link_type": link["link_type"]})
    return {"nodes": list(nodes.values()), "links": links}

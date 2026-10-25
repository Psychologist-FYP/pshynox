from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from services.api.db import SessionRecord, TranscriptSegmentRecord
from services.api.db import get_db, make_engine, make_session_factory
from services.api.schemas import DraftComposition, FuzzyEvidenceHit, TranscriptSegment
from services.retrieval.browse import CATEGORY_DEFINITIONS, breadcrumb, children, documents, graph, node_documents, node_info, related, search
from services.retrieval.compose import compose_draft
from services.retrieval.fuzzy import fuzzy_search

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "packages" / "contracts" / "fixtures"
FIXTURE_MODE = os.getenv("FIXTURE_MODE", "true").lower() == "true"
_engine = make_engine()
_session_factory = make_session_factory(_engine)
router = APIRouter(prefix="/api/browse", tags=["browse"])


def _load_fixture(name: str) -> list[dict[str, Any]]:
    with (FIXTURE_DIR / name).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, list) else [payload]


def _segments(db: DbSession) -> list[TranscriptSegment]:
    records = db.scalars(select(TranscriptSegmentRecord).order_by(TranscriptSegmentRecord.session_id, TranscriptSegmentRecord.start_ms, TranscriptSegmentRecord.id)).all()
    result = [TranscriptSegment(id=row.id, session_id=row.session_id, start_ms=row.start_ms, end_ms=row.end_ms, speaker_role=row.speaker_role, text=row.text, revision=row.revision) for row in records]
    if FIXTURE_MODE:
        existing = {segment.id for segment in result}
        result.extend(TranscriptSegment.model_validate(item) for item in _load_fixture("transcript.json") if item["id"] not in existing)
    return result


def _db() -> DbSession:
    yield from get_db(_session_factory)


def _docs(db: DbSession) -> list[dict[str, Any]]:
    return documents(_segments(db))


def _require_node(node_id: str, docs: list[dict[str, Any]]) -> dict[str, Any]:
    info = node_info(node_id, docs)
    if info is None:
        raise HTTPException(status_code=404, detail={"code": "BROWSE_NODE_NOT_FOUND", "message": "Browse node was not found."})
    return info


@router.get("/home")
def browse_home(db: DbSession = Depends(_db)) -> dict[str, Any]:
    docs = _docs(db)
    return {
        "node_id": "root",
        "title": "FYP Repository",
        "hint": "Start with a category, follow the hierarchy, or search for a fictional transcript excerpt.",
        "categories": [{**item, "count": sum(doc["category_id"] == item["id"] for doc in docs)} for item in CATEGORY_DEFINITIONS],
        "session_count": len({doc["session_id"] for doc in docs}),
        "segment_count": len(docs),
    }


@router.get("/tree")
def browse_tree(node_id: str = "root", db: DbSession = Depends(_db)) -> dict[str, Any]:
    docs = _docs(db)
    _require_node(node_id, docs)
    return {"node_id": node_id, "children": children(node_id, docs)}


@router.get("/node/{node_id:path}")
def browse_node(node_id: str, db: DbSession = Depends(_db)) -> dict[str, Any]:
    docs = _docs(db)
    info = _require_node(node_id, docs)
    selected = node_documents(node_id, docs)
    return {**info, "node_id": node_id, "breadcrumb": breadcrumb(node_id, docs), "children": children(node_id, docs), "documents": selected}


@router.get("/documents")
def browse_documents(node_id: str | None = None, page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100), db: DbSession = Depends(_db)) -> dict[str, Any]:
    docs = _docs(db)
    if node_id:
        _require_node(node_id, docs)
        docs = node_documents(node_id, docs)
    start = (page - 1) * per_page
    return {"page": page, "per_page": per_page, "total": len(docs), "documents": docs[start : start + per_page]}


@router.get("/search")
def browse_search(q: str = "", scope: str | None = None, k: int = Query(10, ge=1, le=100), db: DbSession = Depends(_db)) -> dict[str, Any]:
    docs = _docs(db)
    if scope:
        _require_node(scope, docs)
    return {"query": q, "scope": scope, "results": search(q, docs, scope, k)}


@router.get("/fuzzy", response_model=list[FuzzyEvidenceHit])
def browse_fuzzy(
    q: str = "",
    scope: str | None = None,
    k: int = Query(10, ge=1, le=100),
    not_mode: Literal["fuzzy", "strict-exclude"] = "fuzzy",
    window: int = Query(0, ge=0, le=10),
    db: DbSession = Depends(_db),
) -> list[FuzzyEvidenceHit]:
    docs = _docs(db)
    if scope:
        _require_node(scope, docs)
    selected = node_documents(scope, docs) if scope else docs
    if not q.strip():
        return []
    if not_mode == "strict-exclude" and any(doc.get("category_id") == "risk-related-language" for doc in selected):
        raise HTTPException(status_code=422, detail={"code": "RISK_NOT_MODE_FORBIDDEN", "message": "NOT/exclude mode is unavailable for risk-related search candidates."})
    try:
        results = fuzzy_search(q, selected, k=k, not_mode=not_mode, window=window)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_FUZZY_QUERY", "message": str(exc)}) from exc
    return [FuzzyEvidenceHit.model_validate(result.__dict__) for result in results]


@router.get("/soap-preview/{session_id}", response_model=DraftComposition)
def soap_preview(session_id: str) -> DraftComposition:
    try:
        return compose_draft(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"code": "SOAP_PREVIEW_NOT_FOUND", "message": str(exc)}) from exc


@router.get("/segment/{segment_id}")
def segment_preview(segment_id: str, db: DbSession = Depends(_db)) -> dict[str, Any]:
    docs = _docs(db)
    document = next((doc for doc in docs if doc["segment_id"] == segment_id), None)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "BROWSE_SEGMENT_NOT_FOUND", "message": "Browse segment was not found."})
    node_id = f"segment:{segment_id}"
    return {"document": document, "breadcrumb": breadcrumb(node_id, docs), "related": related(segment_id, docs)}


@router.get("/related/{segment_id}")
def related_segments(segment_id: str, db: DbSession = Depends(_db)) -> dict[str, Any]:
    docs = _docs(db)
    if not any(doc["segment_id"] == segment_id for doc in docs):
        raise HTTPException(status_code=404, detail={"code": "BROWSE_SEGMENT_NOT_FOUND", "message": "Browse segment was not found."})
    return {"source_id": segment_id, "links": related(segment_id, docs)}


@router.get("/graph")
def browse_graph(db: DbSession = Depends(_db)) -> dict[str, Any]:
    return graph(_docs(db))

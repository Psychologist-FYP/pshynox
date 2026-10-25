"""Deterministic SOAP-style note preview over the fictional SOAP fixture."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from services.api.schemas import (
    DraftComposition,
    NoteDraft,
    NoteField,
    NoteWorkflowStatus,
    Provenance,
    ReviewStatus,
    RiskReviewPreview,
    RiskReviewStatus,
    RiskCandidate,
    SectionTrace,
    Session,
    TranscriptSegment,
)
from services.retrieval.browse import documents, search

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "packages" / "contracts" / "fixtures"
SOAP_SESSION_PATH = FIXTURE_DIR / "soap_session.json"
SOAP_TRANSCRIPT_PATH = FIXTURE_DIR / "soap_transcript.json"
SOAP_METADATA_PATH = FIXTURE_DIR / "soap_metadata.json"

SECTION_QUERIES = {
    "subjective": ("worry", "sleep"),
    "functioning": ("sleep", "meals"),
}


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _fixture() -> tuple[Session, list[TranscriptSegment], list[dict[str, Any]]]:
    session = Session.model_validate(_load_json(SOAP_SESSION_PATH))
    segments = [TranscriptSegment.model_validate(item) for item in _load_json(SOAP_TRANSCRIPT_PATH)]
    return session, segments, documents(segments, metadata_path=SOAP_METADATA_PATH)


def _candidate_fields(docs: Sequence[dict[str, Any]], section: str) -> tuple[NoteField, SectionTrace]:
    queries = SECTION_QUERIES[section]
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for query in queries:
        for hit in search(query, docs, scope=None, k=10):
            if hit["segment_id"] in seen:
                continue
            source = next(item for item in docs if item["segment_id"] == hit["segment_id"])
            if source["speaker_role"] != "CLIENT":
                continue
            seen.add(hit["segment_id"])
            hits.append(source)
    hits.sort(key=lambda item: (item["start_ms"], item["segment_id"]))
    evidence_ids = [item["segment_id"] for item in hits]
    text = " ".join(item["text"] for item in hits)
    field = NoteField(section=section, text=text, provenance=Provenance.RETRIEVAL_CANDIDATE, evidence_ids=evidence_ids, review_status=ReviewStatus.PENDING)
    trace = SectionTrace(section=section, seed_queries=list(queries), matched_evidence_ids=evidence_ids, generated_text=text)
    return field, trace


def compose_draft(session_id: str, evidence: Sequence[dict[str, Any]] | None = None) -> DraftComposition:
    """Compose an ephemeral preview; no database access or persistence occurs."""

    session, segments, fixture_docs = _fixture()
    if session_id != session.id:
        raise ValueError(f"Unknown SOAP preview session: {session_id}")
    docs = list(evidence) if evidence is not None else fixture_docs
    fields: list[NoteField] = []
    traces: list[SectionTrace] = []
    for section in ("subjective", "functioning"):
        field, trace = _candidate_fields(docs, section)
        fields.append(field)
        traces.append(trace)
    for section in ("objective", "assessment", "plan"):
        fields.append(NoteField(section=section, text="", provenance=Provenance.TEMPLATE, evidence_ids=[], review_status=ReviewStatus.PENDING))
        traces.append(SectionTrace(section=section, seed_queries=[], matched_evidence_ids=[], generated_text=""))
    risk_candidates = [
        RiskCandidate(segment_id=doc["segment_id"], excerpt=doc["text"])
        for doc in docs
        if doc.get("category_id") == "risk-related-language"
    ]
    now = datetime.fromisoformat(session.created_at.isoformat()) if session.created_at else datetime.now(timezone.utc)
    draft = NoteDraft(id=f"preview-{session.id}", session_id=session.id, fields=fields, created_at=now, updated_at=now, workflow_status=NoteWorkflowStatus.DRAFT, risk_review_status=RiskReviewStatus.PENDING)
    return DraftComposition(preview_only=True, draft=draft, source_segments=segments, traces=traces, risk_review=RiskReviewPreview(status=RiskReviewStatus.PENDING, candidates=risk_candidates))

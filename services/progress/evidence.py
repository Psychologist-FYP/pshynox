"""Evidence packets: stable source aliases, full-transcript or retrieval selection, within the token budget."""
from __future__ import annotations

from services.api.schemas import TranscriptSegment
from services.retrieval.config import RetrievalSettings
from services.retrieval.engine import EvidenceRetrievalProvider, SearchScope

RISK_QUERY = "die dead death suicide suicidal kill hurt harm myself cutting overdose end it all better off gone mar jaun khudkushi"
PLAN_QUERY = "homework plan practice tried agreed last week since last session exercise goal"
SOURCE_FIELDS = ("speaker_role", "start_ms", "end_ms", "text")


def aliases_for(segments: list[dict], prefix: str = "T") -> dict[str, dict]:
    """Stable aliases by transcript position; identical text in different turns keeps distinct aliases."""
    return {f"{prefix}{i}": row for i, row in enumerate(segments)}


def model_row(alias: str, row: dict, session_index: int | None = None) -> dict:
    out = {"id": alias, **{k: row[k] for k in SOURCE_FIELDS if k in row}}
    if session_index is not None:
        out["session"] = session_index
    return out


class Retriever:
    """BM25 with ±2 context turns over one session's current segments (same provider as notes)."""

    def __init__(self, method: str = "bm25"):
        self.provider = EvidenceRetrievalProvider(RetrievalSettings(method=method, context_turns=2))

    def select(self, session_id: str, segments: list[dict], queries: list[str], k: int, scope: SearchScope | None = None) -> list[str]:
        models = [TranscriptSegment.model_validate({k: s[k] for k in ("id", "session_id", "start_ms", "end_ms", "speaker_role", "text", "revision")}) for s in segments]
        chosen: list[str] = []
        for query in queries:
            if not query.strip():
                continue
            for hit in self.provider.search(query, session_id, models, k, scope=scope or SearchScope()):
                for src in hit.sources:
                    if src.session_id == session_id and src.id not in chosen:
                        chosen.append(src.id)
        order = {s["id"]: i for i, s in enumerate(segments)}
        return sorted(chosen, key=lambda sid: order.get(sid, 0))

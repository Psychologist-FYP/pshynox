from collections.abc import Sequence

from services.api.schemas import EvidenceHit, TranscriptSegment


class RetrievalProvider:
    def search(self, query: str, session_id: str, segments: Sequence[TranscriptSegment], k: int = 10) -> list[EvidenceHit]:
        raise NotImplementedError


class FixtureRetrievalProvider(RetrievalProvider):
    """Offline provider boundary; Module B can replace this without UI changes."""

    def search(self, query: str, session_id: str, segments: Sequence[TranscriptSegment], k: int = 10) -> list[EvidenceHit]:
        terms = {term for term in query.lower().split() if term}
        if not terms:
            return []
        results = []
        for segment in segments:
            haystack = segment.text.lower()
            matched = sum(term in haystack for term in terms)
            if matched:
                results.append(
                    EvidenceHit(
                        segment_id=segment.id,
                        session_id=session_id,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        excerpt=segment.text,
                        score=float(matched),
                        section="subjective",
                        query=query,
                    )
                )
        return sorted(results, key=lambda hit: (-hit.score, hit.start_ms))[:k]

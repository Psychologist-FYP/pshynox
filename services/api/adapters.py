from collections.abc import Sequence

from services.api.schemas import EvidenceHit, TranscriptSegment


class SpeechAdapter:
    """Boundary for Module A; real transcription is intentionally external."""

    def transcribe(self, audio_path: str) -> list[TranscriptSegment]:
        raise NotImplementedError("Module A speech adapter is not configured")


class RetrievalAdapter:
    """Boundary for Module B; fixture mode keeps the app usable offline."""

    def index(self, session_id: str, segments: Sequence[TranscriptSegment]) -> None:
        raise NotImplementedError("Module B retrieval adapter is not configured")

    def search(self, query: str, scope: dict[str, str], k: int) -> list[EvidenceHit]:
        raise NotImplementedError("Module B retrieval adapter is not configured")

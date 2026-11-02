import os
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class RetrievalSettings:
    method: str = "bm25"
    k1: float = 1.5
    b: float = 0.75
    context_turns: int = 1
    cache_sessions: int = 8
    rrf_constant: int = 60
    candidate_limit: int = 20
    min_score: float | None = None
    e5_model: str = "intfloat/multilingual-e5-small"
    e5_revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    e5_cache_dir: str = "models/retrieval"
    e5_local_only: bool = True

    def __post_init__(self):
        if self.method not in {"fixture", "bm25", "tfidf", "e5", "hybrid"}:
            raise ValueError("Unknown retrieval method")
        if not math.isfinite(self.k1) or self.k1 <= 0 or not 0 <= self.b <= 1:
            raise ValueError("BM25 requires finite k1 > 0 and 0 <= b <= 1")
        if not 0 <= self.context_turns <= 5 or self.cache_sessions < 1:
            raise ValueError("Invalid context/cache size")
        if self.rrf_constant < 1 or self.candidate_limit < 1:
            raise ValueError("RRF settings must be positive")
        if self.min_score is not None and not math.isfinite(self.min_score):
            raise ValueError("Minimum score must be finite")

    def metadata(self):
        return {**asdict(self), "lexical_normalization": "NFKC+casefold; Unicode L/N/M; no stopwords",
                "dense_normalization": "original passage text; L2-normalized vectors",
                "ranking_unit": "central turn; neighbours returned as context only",
                "bm25_idf": "log(1 + (N - df + 0.5)/(df + 0.5))"}

    @classmethod
    def from_env(cls):
        return cls(
            method=os.getenv("RETRIEVAL_METHOD", "bm25"),
            k1=float(os.getenv("RETRIEVAL_BM25_K1", "1.5")),
            b=float(os.getenv("RETRIEVAL_BM25_B", "0.75")),
            context_turns=int(os.getenv("RETRIEVAL_CONTEXT_TURNS", "1")),
            cache_sessions=int(os.getenv("RETRIEVAL_CACHE_SESSIONS", "8")),
            min_score=float(os.environ["RETRIEVAL_MIN_SCORE"]) if os.getenv("RETRIEVAL_MIN_SCORE") else None,
            e5_model=os.getenv("RETRIEVAL_E5_MODEL", "intfloat/multilingual-e5-small"),
            e5_revision=os.getenv("RETRIEVAL_E5_REVISION", cls.e5_revision),
            e5_cache_dir=os.getenv("RETRIEVAL_E5_CACHE_DIR", "models/retrieval"),
            e5_local_only=os.getenv("RETRIEVAL_E5_LOCAL_ONLY", "true").lower() == "true",
        )

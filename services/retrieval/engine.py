"""Session-isolated ranking with revision-keyed, bounded in-memory indexes."""
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
import hashlib
import json
import math
from threading import RLock

from services.api.schemas import EvidenceHit, EvidenceSource, NOTE_SECTIONS, TranscriptSegment
from services.retrieval.config import RetrievalSettings
from services.retrieval.query import parse_query, tokenize


class RetrievalUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchScope:
    patient_id: str | None = None
    recording_ids: dict[str, str] = field(default_factory=dict)
    timeline_ids: dict[str, str] = field(default_factory=dict)
    speaker_role: str | None = None
    recording_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    section: str = "subjective"

    def __post_init__(self):
        if self.speaker_role is not None and self.speaker_role not in {"CLIENT", "CLINICIAN", "UNKNOWN"}:
            raise ValueError("Invalid speaker role")
        if self.section not in NOTE_SECTIONS:
            raise ValueError("Invalid note section")
        if any(value is not None and value < 0 for value in (self.start_ms, self.end_ms)):
            raise ValueError("Time filters must be nonnegative")
        if self.start_ms is not None and self.end_ms is not None and self.start_ms > self.end_ms:
            raise ValueError("end_ms must be at least start_ms")


@dataclass
class Window:
    center: TranscriptSegment
    sources: list[TranscriptSegment]
    window_id: str


@dataclass
class SessionIndex:
    fingerprint: str
    transcript_revision: str
    windows: list[Window]
    tokens: list[list[str]]
    state: dict = field(default_factory=dict)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def reciprocal_rank_fusion(rankings: list[list[int]], constant: int = 60) -> dict[int, float]:
    scores = {}
    for ranking in rankings:
        for rank, index in enumerate(dict.fromkeys(ranking), 1):
            scores[index] = scores.get(index, 0.0) + 1 / (constant + rank)
    return scores


class EvidenceRetrievalProvider:
    """The public search signature remains compatible with Module C.

    Inputs are read from the current session each request. Full text, roles,
    timestamps, source bindings and revisions participate in the fingerprint,
    so edits, deletions and new ASR turns cannot reuse superseded indexes.
    No notes or labels are accepted by the index boundary.
    """

    def __init__(self, settings: RetrievalSettings | None = None, *, encoder=None):
        self.settings = settings or RetrievalSettings.from_env()
        self._indexes: OrderedDict[tuple, SessionIndex] = OrderedDict()
        self._lock = RLock()
        self._encoder = encoder
        self.diagnostics: dict = {}

    def invalidate(self, session_id: str) -> None:
        with self._lock:
            for key in list(self._indexes):
                if key[0] == session_id:
                    del self._indexes[key]

    def _index(self, session_id, segments, scope):
        if any(s.session_id != session_id for s in segments):
            raise ValueError("All transcript segments must belong to the requested session")
        if len({s.id for s in segments}) != len(segments):
            raise ValueError("Transcript segment IDs must be unique")
        snapshot = digest([s.model_dump(mode="json") for s in segments] +
                          [scope.patient_id, scope.recording_ids, scope.timeline_ids])
        fingerprint = digest([snapshot, scope.__dict__])
        key = (session_id, scope.patient_id)
        previous = self._indexes.get(key)
        if previous and previous.fingerprint == fingerprint:
            self._indexes.move_to_end(key)
            return previous
        # Keep independent recordings apart; meeting tracks share a timeline.
        groups = {}
        for s in segments:
            group = scope.timeline_ids.get(s.id, scope.recording_ids.get(s.id, "unrecorded"))
            groups.setdefault(group, []).append(s)
        windows = []
        for group in groups.values():
            group.sort(key=lambda s: (s.start_ms, s.end_ms, s.id))
            for i, center in enumerate(group):
                if not tokenize(center.text):
                    continue
                if scope.speaker_role and center.speaker_role != scope.speaker_role:
                    continue
                if scope.recording_id and scope.recording_ids.get(center.id) != scope.recording_id:
                    continue
                if scope.start_ms is not None and center.end_ms < scope.start_ms:
                    continue
                if scope.end_ms is not None and center.start_ms > scope.end_ms:
                    continue
                radius = self.settings.context_turns
                sources = group[max(0, i - radius):i + radius + 1]
                window_id = "window-" + digest([session_id, center.id, [s.id for s in sources]])[:24]
                windows.append(Window(center, sources, window_id))
        index = SessionIndex(fingerprint, snapshot, windows, [tokenize(w.center.text) for w in windows])
        self._indexes[key] = index
        self._indexes.move_to_end(key)
        while len(self._indexes) > self.settings.cache_sessions:
            self._indexes.popitem(last=False)
        return index

    def _bm25(self, index, terms):
        if "bm25" not in index.state:
            try:
                from rank_bm25 import BM25Okapi
            except ImportError as exc:
                raise RetrievalUnavailable("Install project retrieval dependencies (pip install -e '.[dev]').") from exc
            model = BM25Okapi(index.tokens, k1=self.settings.k1, b=self.settings.b)
            # Positive Robertson IDF avoids negative/zero scores for common
            # evidence terms in tiny per-session corpora. Record this variant.
            frequencies = {}
            for tokens in index.tokens:
                for term in set(tokens):
                    frequencies[term] = frequencies.get(term, 0) + 1
            model.idf = {term: math.log1p((len(index.tokens) - df + 0.5) / (df + 0.5))
                         for term, df in frequencies.items()}
            index.state["bm25"] = model
        return index.state["bm25"].get_scores(list(terms)).tolist()

    def _tfidf(self, index, terms):
        if "tfidf" not in index.state:
            try:
                from sklearn.feature_extraction.text import TfidfVectorizer
            except ImportError as exc:
                raise RetrievalUnavailable("Install scikit-learn for TF-IDF retrieval.") from exc
            vectorizer = TfidfVectorizer(tokenizer=tokenize, token_pattern=None, lowercase=False,
                                         ngram_range=(1, 2), norm="l2")
            matrix = vectorizer.fit_transform(w.center.text for w in index.windows)
            index.state["tfidf"] = (vectorizer, matrix)
        vectorizer, matrix = index.state["tfidf"]
        return (matrix @ vectorizer.transform([" ".join(terms)]).T).toarray().ravel().tolist()

    def _load_encoder(self):
        if self._encoder is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._encoder = SentenceTransformer(
                    self.settings.e5_model, revision=self.settings.e5_revision,
                    cache_folder=self.settings.e5_cache_dir, device="cpu",
                    local_files_only=self.settings.e5_local_only,
                )
                self._encoder.max_seq_length = min(512, self._encoder.max_seq_length)
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                raise RetrievalUnavailable(
                    "E5 is unavailable. Install .[dense] and cache the configured model/revision first; "
                    "runtime uses local weights by default."
                ) from exc
        return self._encoder

    def _dense(self, index, text):
        import numpy as np

        model = self._load_encoder()
        tokenizer = model.tokenizer
        limit = min(512, model.max_seq_length)
        budget = limit - tokenizer.num_special_tokens_to_add(pair=False) - len(tokenizer.encode("passage: ", add_special_tokens=False))
        if budget < 1:
            raise RetrievalUnavailable("The configured tokenizer has no passage token budget")
        if "dense" not in index.state:
            passages, owners, split_ids = [], [], []
            for i, window in enumerate(index.windows):
                ids = tokenizer.encode(window.center.text, add_special_tokens=False)
                if len(ids) > budget:
                    split_ids.append(window.center.id)
                for offset in range(0, len(ids), budget):
                    chunk = tokenizer.decode(ids[offset:offset + budget], skip_special_tokens=True)
                    # Re-tokenization can grow after decode; subdivide until the
                    # actual prefixed input fits, instead of silently truncating.
                    pending = [chunk]
                    while pending:
                        part = pending.pop(0)
                        if len(tokenizer.encode("passage: " + part)) > limit:
                            if len(part) < 2:
                                raise RetrievalUnavailable("Unable to fit passage in tokenizer limit")
                            midpoint = len(part) // 2
                            pending[0:0] = [part[:midpoint], part[midpoint:]]
                        else:
                            passages.append("passage: " + part)
                            owners.append(i)
            matrix = model.encode(passages, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
            index.state["dense"] = (matrix, owners, split_ids)
        matrix, owners, split_ids = index.state["dense"]
        query_text = "query: " + text
        query_tokens = len(tokenizer.encode(query_text))
        transformer = model[0] if hasattr(model, "__getitem__") else None
        model_config = getattr(getattr(transformer, "auto_model", None), "config", None)
        self.diagnostics.update({"split_source_ids": split_ids, "passage_truncations": 0,
                                 "query_token_count": query_tokens, "query_truncated": query_tokens > limit,
                                 "max_tokens": limit,
                                 "resolved_model_revision": getattr(model_config, "_commit_hash", None),
                                 "tokenizer_revision": getattr(tokenizer, "init_kwargs", {}).get("_commit_hash")})
        query_vector = model.encode([query_text], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)[0]
        values = matrix @ query_vector
        scores = np.full(len(index.windows), -np.inf)
        for owner, score in zip(owners, values):
            scores[owner] = max(scores[owner], score)
        return scores.tolist()

    def search(self, query: str, session_id: str, segments: Sequence[TranscriptSegment], k: int = 10,
               *, scope: SearchScope | None = None) -> list[EvidenceHit]:
        scope = scope or SearchScope()
        parsed = parse_query(query)
        if parsed.expression is None or k <= 0:
            return []
        with self._lock:
            self.diagnostics = {}
            index = self._index(session_id, segments, scope)
            if not index.windows:
                return []
            method = self.settings.method
            # An explicit Boolean/phrase expression is a hard filter for all
            # methods. Soft queries can retrieve paraphrases with E5/hybrid.
            eligible = [i for i, tokens in enumerate(index.tokens)
                        if not parsed.explicit or parsed.matches(tokens)]
            if not eligible:
                return []
            terms = parsed.positive_terms
            semantic_text = " ".join(terms) if parsed.explicit else query

            def lexical_scores(name):
                if name == "fixture":
                    needles = set(semantic_text.lower().split())
                    return [float(sum(t in w.center.text.lower() for t in needles)) for w in index.windows]
                return self._bm25(index, terms) if name == "bm25" else self._tfidf(index, terms)

            def ranked(scores, lexical=False):
                candidates = [i for i in eligible if math.isfinite(scores[i]) and
                              (not lexical or parsed.matches(index.tokens[i]))]
                return sorted(candidates, key=lambda i: (-scores[i], index.windows[i].center.start_ms,
                                                         index.windows[i].center.id))

            if not terms:
                # Pure exclusions define eligibility, without inventing a
                # positive relevance score or invoking a semantic model.
                scores = [0.0] * len(index.windows)
                order = ranked(scores)
            elif method == "hybrid":
                lexical = self._bm25(index, terms)
                dense = self._dense(index, semantic_text)
                limit = self.settings.candidate_limit
                fused = reciprocal_rank_fusion([ranked(lexical, True)[:limit], ranked(dense)[:limit]], self.settings.rrf_constant)
                scores = [fused.get(i, float("-inf")) for i in range(len(index.windows))]
                order = ranked(scores)
            elif method == "e5":
                scores = self._dense(index, semantic_text)
                order = ranked(scores)
            else:
                scores = lexical_scores(method)
                if method == "fixture" and not parsed.explicit:
                    # Preserve the original substring baseline, including its
                    # false positives, for a meaningful comparison.
                    order = ranked(scores)
                    order = [i for i in order if scores[i] > 0]
                else:
                    order = ranked(scores, True)
            if self.settings.min_score is not None:
                order = [i for i in order if scores[i] >= self.settings.min_score]
            hits = []
            for rank, i in enumerate(order[:k], 1):
                window = index.windows[i]
                center = window.center
                hits.append(EvidenceHit(
                    segment_id=center.id, session_id=session_id, patient_id=scope.patient_id,
                    start_ms=center.start_ms, end_ms=center.end_ms, excerpt=center.text,
                    score=float(scores[i]), section=scope.section, query=query, rank=rank, method=method,
                    window_id=window.window_id, transcript_revision=index.transcript_revision,
                    recording_id=scope.recording_ids.get(center.id), speaker_role=center.speaker_role,
                    source_segment_ids=[s.id for s in window.sources], matched_source_segment_ids=[center.id],
                    sources=[EvidenceSource(**s.model_dump(), recording_id=scope.recording_ids.get(s.id),
                                            matched=s.id == center.id) for s in window.sources],
                    retrieval_diagnostics=self.diagnostics.copy(),
                ))
            return hits

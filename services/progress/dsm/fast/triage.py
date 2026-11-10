"""Retrieval-first triage: a cheap score of every turn against every feature, used to skip whole feature groups.

* BM25 (the existing ``EvidenceRetrievalProvider``) on each feature's registry ``queries``, with generic denial words
  removed (they match almost every turn, so they cannot discriminate).
* E5 (``intfloat/multilingual-e5-small``, local weights, CPU) cosine similarity between each turn and the feature's
  short triage descriptions in ``config.json``. Description embeddings are cached on disk by config and model revision.
  The encoder is released before the LLM loads.

A group is skipped only when no turn reaches either threshold for any of its features. The risk + plans call is never
triaged. Gating is per group, not per feature: in ``dsm-fast`` the model lists only features with evidence, so an
unlisted feature costs no output tokens and per-feature gating would save nothing while risking recall.
"""
from __future__ import annotations

import gc
import hashlib
import json
import re
import time
from pathlib import Path

from services.api.schemas import TranscriptSegment
from services.retrieval.config import RetrievalSettings
from services.retrieval.engine import EvidenceRetrievalProvider, RetrievalUnavailable

from ..registry import dsm_registry
from .config import fast_config

ROOT = Path(__file__).resolve().parents[4]


def lexicon_hits(segments: list[dict]) -> list[str]:
    """Segment ids whose text contains a risk-lexicon term (English, Roman Urdu, Urdu). Logged and tested; never gates."""
    terms = [t for lang in fast_config()["risk_lexicon"].values() for t in lang]
    pattern = re.compile("|".join(rf"(?<!\w){re.escape(t)}(?!\w)" for t in sorted(terms, key=len, reverse=True)), re.IGNORECASE)
    return [s["id"] for s in segments if pattern.search(s.get("text") or "")]


def _models(segments: list[dict], session_id: str) -> list[TranscriptSegment]:
    return [TranscriptSegment.model_validate({"id": s["id"], "session_id": session_id, "start_ms": s.get("start_ms", 0), "end_ms": s.get("end_ms", 0),
                                              "speaker_role": s["speaker_role"], "text": s["text"] or ".", "revision": s.get("revision", 1)})
            for s in segments]


def bm25_scores(segments: list[dict], session_id: str, hits_out: dict | None = None) -> dict[str, float]:
    """Best BM25 score of any turn, per feature. ``hits_out`` (if given) receives each feature's turn indices by score."""
    reg, cfg = dsm_registry(), fast_config()
    generic = set(cfg["triage"]["generic_terms"])
    provider = EvidenceRetrievalProvider(RetrievalSettings(method="bm25", context_turns=0))
    models = _models(segments, session_id)
    out = {}
    for f in cfg["features"]:
        terms = [w for q in reg.feature(f["key"]).queries for w in q.split() if w.casefold() not in generic]
        best, ranked = 0.0, []
        if terms:
            for hit in provider.search(" ".join(dict.fromkeys(terms)), session_id, models, k=len(models)):
                best = max(best, hit.score)
                ranked.append((hit.score, hit.segment_id))
        out[f["key"]] = round(best, 3)
        if hits_out is not None:
            hits_out[f["key"]] = [sid for _, sid in sorted(ranked, key=lambda x: -x[0])]
    return out


class E5:
    """Loaded for triage only and released afterwards (CPU; never competes for VRAM)."""

    def __init__(self, cache_dir: Path | None = None):
        self.settings = RetrievalSettings(method="e5")
        self.cache_dir = Path(cache_dir or ROOT / "data/research/progress-runs/dsm-fast-cache")
        self.model = None

    def __enter__(self):
        self.model = EvidenceRetrievalProvider(self.settings)._load_encoder()
        return self

    def __exit__(self, *exc):
        self.model = None
        gc.collect()

    def descriptions(self):
        import numpy as np
        cfg = fast_config()
        texts = [(f["key"], d) for f in cfg["features"] for d in f["triage"]]
        key = hashlib.sha256(json.dumps([texts, self.settings.e5_model, self.settings.e5_revision]).encode()).hexdigest()[:16]
        path = self.cache_dir / f"e5-descriptions-{key}.npz"
        if path.exists():
            data = np.load(path, allow_pickle=False)
            return list(data["keys"]), data["vectors"]
        vectors = self.model.encode(["query: " + d for _, d in texts], normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(path, keys=np.array([k for k, _ in texts]), vectors=vectors)
        return [k for k, _ in texts], vectors

    def scores(self, segments: list[dict]) -> dict[str, float]:
        """Best cosine similarity of any turn, per feature."""
        keys, vectors = self.descriptions()
        turns = self.model.encode(["passage: " + (s["text"] or "") for s in segments], normalize_embeddings=True, convert_to_numpy=True,
                                  show_progress_bar=False, batch_size=32)
        sims = turns @ vectors.T  # turns x descriptions
        best = sims.max(axis=0)
        out: dict[str, float] = {}
        for k, v in zip(keys, best):
            out[str(k)] = round(max(out.get(str(k), -1.0), float(v)), 4)
        return out


def triage(segments: list[dict], session_id: str, *, use_e5: bool | None = None, calllog=None) -> dict:
    """{"groups": {group: {"call": bool, "reason"}}, "bm25": {...}, "e5": {...}, "lexicon_turns": [...], "seconds"}."""
    cfg = fast_config()
    t = cfg["triage"]
    started = time.monotonic()
    hits: dict[str, list[str]] = {}
    bm25 = bm25_scores(segments, session_id, hits)
    e5: dict[str, float] = {}
    use_e5 = t["e5"] if use_e5 is None else use_e5
    note = ""
    if use_e5:
        try:
            ctx = calllog.phase("e5") if calllog else None
            if ctx:
                ctx.__enter__()
            try:
                with E5() as enc:
                    e5 = enc.scores(segments)
            finally:
                if ctx:
                    ctx.__exit__(None, None, None)
        except RetrievalUnavailable as exc:
            note = f"E5 unavailable ({str(exc)[:80]}); BM25 only"
    feature_group = {f["key"]: g["key"] for g in cfg["groups"] for f in cfg["features"]
                     if dsm_registry().feature(f["key"]).group in g["registry_groups"]}
    groups = {}
    for g in cfg["groups"]:
        keys = [k for k, gk in feature_group.items() if gk == g["key"]]
        lex = [k for k in keys if bm25.get(k, 0) >= t["bm25_min_score"]]
        sem = [k for k in keys if e5.get(k, -1) >= t["e5_threshold"]]
        call = bool(lex or sem)
        groups[g["key"]] = {"call": call, "lexical": lex, "semantic": sem,
                            "reason": "candidate turns found" if call else "not discussed (no candidate turns found)"}
    return {"groups": groups, "bm25": bm25, "e5": e5, "pointers": pointers(segments, hits, bm25), "lexicon_turns": lexicon_hits(segments), "note": note,
            "seconds": round(time.monotonic() - started, 3), "feature_group": feature_group}


def pointers(segments: list[dict], hits: dict[str, list[str]], bm25: dict[str, float]) -> dict[str, list[int]]:
    """Per feature, candidate turn indices for the prompt: the best keyword hits and their neighbours (a question and its
    answer are often split over adjacent turns). Pointers only guide reading; the model still sees every turn."""
    t = fast_config()["triage"]
    index = {s["id"]: i for i, s in enumerate(segments)}
    out = {}
    for key, ranked in hits.items():
        chosen: list[int] = []
        for sid in ranked[: t.get("pointer_hits", 3)]:
            i = index.get(sid)
            if i is None:
                continue
            chosen += [j for j in range(i - 1, i + 2) if 0 <= j < len(segments)]
        out[key] = sorted(set(chosen))[: t.get("pointer_max", 8)] if bm25.get(key, 0) >= t["bm25_min_score"] else []
    return out

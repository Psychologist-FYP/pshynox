"""The ASR model manifest (``models.json``): path, SHA-256, size, licence and source of every model file.

Model files are not in Git. The manifest is: every run records the hash of the file it used, and a file whose hash
differs from its manifest entry is refused, so a swapped or corrupted model never transcribes silently.
"""
from __future__ import annotations

import hashlib
import json
import threading
from functools import lru_cache
from pathlib import Path

from services.speech.audio import SpeechError

ROOT = Path(__file__).resolve().parents[3]
PATH = Path(__file__).with_name("models.json")
_LOCK = threading.Lock()
_DIGESTS: dict[tuple[str, int, int], str] = {}


@lru_cache(maxsize=1)
def manifest() -> dict:
    return json.loads(PATH.read_text())


def entries() -> list[dict]:
    return manifest()["models"]


def by_id(model_id: str) -> dict:
    return next(m for m in entries() if m["id"] == model_id)


def _resolve(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def entry_for(path: str | Path) -> tuple[dict | None, dict | None]:
    """(model entry, file entry) whose file is ``path`` (or, for a folder model, whose folder is ``path``)."""
    target = Path(path).resolve()
    for m in entries():
        for f in m["files"]:
            fp = _resolve(f["path"]).resolve()
            if fp == target or (target.is_dir() and fp.parent == target):
                return m, f
    return None, None


def complete(path: str | Path) -> bool:
    """The file exists and, when the manifest knows its size, has that size (a partial download is not installed)."""
    p = Path(path)
    if not p.is_file():
        return False
    _, file = entry_for(p)
    return not file or not file.get("size") or p.stat().st_size == file["size"]


def sha256(path: str | Path) -> str:
    """SHA-256 of a file, cached by (path, size, mtime) so a 650 MB model is hashed once per process."""
    p = Path(path)
    st = p.stat()
    key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
    with _LOCK:
        if key in _DIGESTS:
            return _DIGESTS[key]
    with p.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    with _LOCK:
        _DIGESTS[key] = digest
    return digest


def verify(path: str | Path) -> dict:
    """Hash ``path`` and check it against the manifest.

    Returns {"model_id", "label", "licence", "non_commercial", "sha256", "file", "manifest"}.

    A file listed with a SHA-256 must match it (MODEL_CHANGED otherwise). A file not in the manifest, or listed
    without a hash yet (pending download), is used and reported with ``manifest: "unlisted"`` / ``"unpinned"``."""
    p = Path(path)
    if not complete(p):
        raise SpeechError("MODEL_INCOMPLETE", f"{p.name} is missing or incomplete (size differs from the manifest); finish the download.")
    digest = sha256(p)
    model, file = entry_for(p)
    if model is None:
        return {"model_id": p.name, "label": p.name, "licence": "unknown", "non_commercial": False, "sha256": digest,
                "file": p.name, "manifest": "unlisted"}
    if file.get("sha256") and file["sha256"] != digest:
        raise SpeechError("MODEL_CHANGED", f"{p.name} does not match its SHA-256 in services/speech/asr/models.json. "
                                           "Re-download it or update the manifest deliberately.")
    return {"model_id": model["id"], "label": model.get("label", model["id"]), "licence": model["licence"],
            "non_commercial": bool(model.get("non_commercial")), "sha256": digest, "file": p.name,
            "manifest": "verified" if file.get("sha256") else "unpinned"}


def label_for(path: str | Path) -> str:
    """The display name of the model at ``path`` ("Urdu Sukhan ASR v1 (q8_0)"), else its file name. No hashing."""
    model, _ = entry_for(path)
    return model.get("label", model["id"]) if model else Path(path).name

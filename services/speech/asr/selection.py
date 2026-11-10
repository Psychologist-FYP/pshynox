"""Pick the engine and model for a mode from the environment, falling back visibly when something is missing.

Settings (all optional; defaults are the paths in docs/ASR_MODELS.md):

* ``ASR_ENGLISH_MODEL``: Parakeet TDT 0.6B v2 ONNX folder (onnx-asr layout).
* ``ASR_URDU_MODEL``: Urdu Sukhan ASR v1 whisper.cpp ggml file.
* ``ASR_FALLBACK_MODEL``: multilingual Whisper base ggml file (``WHISPER_MODEL`` is still read for compatibility).
* ``ASR_VAD_MODEL``: Silero VAD ggml file, used by whisper.cpp ``--vad`` and to cut long audio for Parakeet.
* ``WHISPER_BINARY`` (CPU build) and ``ASR_WHISPER_GPU_BINARY`` (GPU build, used first when present).
* ``ASR_DEVICE``: ``auto`` (GPU first, CPU fallback; default) or ``cpu``.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path

from services.speech.asr import manifest
from services.speech.asr.modes import DEFAULT_MODE, LABELS, MODES, valid

ROOT = Path(__file__).resolve().parents[3]
DEFAULTS = {
    "ASR_ENGLISH_MODEL": "models/asr/parakeet-v2/onnx-int8",
    "ASR_URDU_MODEL": "models/asr/urdu-sukhan/ggml/ggml-urdu-sukhan-asr-v1-q8_0.bin",
    "ASR_FALLBACK_MODEL": "models/ggml-base.bin",
    "ASR_VAD_MODEL": "models/asr/vad/ggml-silero-v5.1.2.bin",
    "WHISPER_BINARY": "data/tools/whisper.cpp/build/bin/whisper-cli",
    "ASR_WHISPER_GPU_BINARY": "data/tools/whisper.cpp/build-vulkan/bin/whisper-cli",
}
PARAKEET_FILES = ("config.json", "vocab.txt", "nemo128.onnx")
ENGLISH_MISSING = "English model not installed — using fallback"
ENGLISH_RUNTIME_MISSING = "English runtime (onnx-asr, onnxruntime) not installed — using fallback"
URDU_MISSING = "Urdu model not installed — using fallback"


def setting(name: str) -> Path:
    if name == "ASR_FALLBACK_MODEL":
        value = os.getenv("ASR_FALLBACK_MODEL") or os.getenv("WHISPER_MODEL") or DEFAULTS[name]
    else:
        value = os.getenv(name) or DEFAULTS[name]
    p = Path(value)
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Selection:
    mode: str
    engine: str                    # "parakeet-onnx" | "whisper.cpp"
    model: Path
    language: str                  # never "auto" in urdu_mixed
    fallback: bool = False
    notices: tuple[str, ...] = field(default_factory=tuple)
    vad_model: Path | None = None

    def describe(self) -> dict:
        return {"mode": self.mode, "mode_label": LABELS[self.mode], "engine": self.engine, "model": self.model.name,
                "language": self.language, "fallback": self.fallback, "notices": list(self.notices),
                "vad": self.vad_model.name if self.vad_model else None}


def parakeet_ready(folder: Path) -> bool:
    """All files of one variant (int8 or fp32) are present and complete."""
    if not folder.is_dir() or any(not manifest.complete(folder / f) for f in PARAKEET_FILES):
        return False
    return any(manifest.complete(folder / f"encoder-model{v}") and manifest.complete(folder / f"decoder_joint-model{v}")
               for v in (".int8.onnx", ".onnx"))


def parakeet_runtime() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in ("onnx_asr", "onnxruntime"))


def select(mode: str | None = None) -> Selection:
    """The engine and model for ``mode``. A missing model, runtime or VAD model gives a notice, never a silent change."""
    mode = valid(mode) or DEFAULT_MODE
    vad = setting("ASR_VAD_MODEL")
    vad = vad if manifest.complete(vad) else None
    notices: list[str] = [] if vad else ["VAD model not installed — transcribing without voice activity detection"]
    fallback = setting("ASR_FALLBACK_MODEL")
    if mode == "english":
        folder = setting("ASR_ENGLISH_MODEL")
        if parakeet_ready(folder) and parakeet_runtime():
            return Selection(mode, "parakeet-onnx", folder, "en", False, tuple(notices), vad)
        notices.insert(0, ENGLISH_MISSING if not parakeet_ready(folder) else ENGLISH_RUNTIME_MISSING)
        return Selection(mode, "whisper.cpp", fallback, "en", True, tuple(notices), vad)
    urdu = setting("ASR_URDU_MODEL")
    if manifest.complete(urdu):
        return Selection(mode, "whisper.cpp", urdu, "ur", False, tuple(notices), vad)
    notices.insert(0, URDU_MISSING)
    return Selection(mode, "whisper.cpp", fallback, "ur", True, tuple(notices), vad)


def status() -> dict:
    """For the UI: what each mode will use on this machine right now."""
    out = {}
    for mode in MODES:
        s = select(mode)
        out[mode] = {**s.describe(), "installed": s.model.exists()}
    return {"default_mode": DEFAULT_MODE, "modes": out}

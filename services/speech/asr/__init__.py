"""Language-specific speech recognition behind one adapter interface (see docs/ASR_MODELS.md).

``english`` uses NVIDIA Parakeet TDT 0.6B v2 (ONNX, onnx-asr); ``urdu_mixed`` uses whisper.cpp with the Urdu Sukhan
model and language ``ur``. Either falls back to the multilingual Whisper base model, with a visible notice, when its
model or runtime is missing. Every adapter returns the existing ``TranscriptSegment`` contract.
"""
from services.speech.asr.modes import DEFAULT_MODE, MODES, AsrMode, resolve_mode
from services.speech.asr.selection import Selection, select, status

__all__ = ["AsrMode", "DEFAULT_MODE", "MODES", "Selection", "resolve_mode", "select", "status"]

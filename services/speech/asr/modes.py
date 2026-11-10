"""ASR language modes: chosen per session, optionally overridden per meeting track."""
from __future__ import annotations

from typing import Literal

AsrMode = Literal["english", "urdu_mixed"]
MODES: tuple[str, ...] = ("english", "urdu_mixed")
DEFAULT_MODE = "urdu_mixed"
LABELS = {"english": "English", "urdu_mixed": "Urdu or mixed Urdu–English"}


def valid(mode: str | None) -> str | None:
    if mode is None:
        return None
    if mode not in MODES:
        raise ValueError(f"asr_mode must be one of {', '.join(MODES)}")
    return mode


def resolve_mode(session_mode: str | None = None, track_override: str | None = None) -> str:
    """The mode for one recording: the track override (meetings), else the session's mode, else the default."""
    return valid(track_override) or valid(session_mode) or DEFAULT_MODE

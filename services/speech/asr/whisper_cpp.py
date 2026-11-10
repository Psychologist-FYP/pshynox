"""whisper.cpp adapter: Urdu Sukhan or the multilingual base model, explicit language, VAD on by default.

The GPU build (``ASR_WHISPER_GPU_BINARY``, e.g. a Vulkan build) is tried first unless ``ASR_DEVICE=cpu``; if it is
missing or fails, the CPU build runs with ``-ng`` and the run records a notice. ``auto`` language detection is never
used in ``urdu_mixed`` mode (the selection always sets ``ur`` there).
"""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from services.api.schemas import TranscriptSegment
from services.speech.asr import manifest
from services.speech.audio import SpeechError


@dataclass
class WhisperCpp:
    model: Path
    language: str
    cpu_binary: Path
    gpu_binary: Path | None = None
    vad_model: Path | None = None
    threads: int = 4
    timeout: int = 1800
    device: str = "auto"                     # auto | cpu
    notices: list[str] = field(default_factory=list)
    name: str = "whisper.cpp"

    def __post_init__(self):
        if self.language == "auto":
            raise SpeechError("ASR_CONFIG", "whisper.cpp needs an explicit language here; 'auto' is not allowed.")

    def identity(self) -> dict:
        if not self.cpu_binary.is_file():
            raise SpeechError("RUNTIME_MISSING", "whisper.cpp is not installed; follow services/speech/README.md.")
        if not self.model.is_file():
            raise SpeechError("MODEL_MISSING", f"ASR model {self.model.name} is missing; see docs/ASR_MODELS.md.")
        model = manifest.verify(self.model)
        out = {"runtime": "whisper.cpp", "binary_sha256": manifest.sha256(self.cpu_binary), "model": self.model.name,
               "model_id": model["model_id"], "model_sha256": model["sha256"], "model_licence": model["licence"],
               "model_manifest": model["manifest"], "language": self.language, "threads": self.threads,
               "vad": bool(self.vad_model)}
        if self.vad_model:
            out["vad_model_sha256"] = manifest.verify(self.vad_model)["sha256"]
        return out

    def command(self, binary: Path, wav: Path, output: Path, gpu: bool) -> list[str]:
        args = [str(binary), "-m", str(self.model), "-f", str(wav), "-l", self.language, "-t", str(self.threads), "-oj", "-of", str(output)]
        if not gpu:
            args.append("-ng")
        if self.vad_model:
            args += ["--vad", "-vm", str(self.vad_model)]
        return args

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(args, capture_output=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SpeechError("ASR_UNAVAILABLE", "Local transcription could not finish. Check the runtime and timeout settings.") from exc

    def transcribe(self, wav: Path, info: dict, session_id: str, workdir: Path) -> tuple[list[TranscriptSegment], dict]:
        output = workdir / "result"
        attempts = []
        if self.device != "cpu" and self.gpu_binary and self.gpu_binary.is_file():
            attempts.append((self.gpu_binary, True))
        attempts.append((self.cpu_binary, False))
        for binary, gpu in attempts:
            result = self._run(self.command(binary, wav, output, gpu))
            if result.returncode == 0:
                break
            if gpu:
                self.notices.append("GPU transcription failed — used CPU")
        else:
            raise SpeechError("ASR_FAILED", "Local transcription failed. Check model/runtime compatibility and available memory.")
        try:
            payload = json.loads(output.with_suffix(".json").read_text())
            segments = parse_segments(payload, session_id=session_id, duration_ms=info["duration_ms"])
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise SpeechError("INVALID_ASR_OUTPUT", "The runtime returned invalid transcript data.") from exc
        return segments, {"device": "gpu" if gpu else "cpu"}


def parse_segments(payload: dict, *, session_id: str, duration_ms: int) -> list[TranscriptSegment]:
    """Bound estimated ASR segment ends to the decoded source audio.

    Whisper may estimate the final end beyond EOF. A segment must still start
    inside the recording; clipping must not turn invalid ranges into valid ones.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("transcription"), list):
        raise ValueError("Invalid transcription structure")
    segments = []
    for item in payload["transcription"]:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            raise ValueError("Invalid segment text")
        text = item["text"].strip()
        if not text or text.startswith("[_"):
            continue
        offsets = item.get("offsets")
        if not isinstance(offsets, dict):
            raise ValueError("Invalid segment offsets")
        start, end = offsets.get("from"), offsets.get("to")
        if type(start) is not int or type(end) is not int:
            raise ValueError("Segment offsets must be integer milliseconds")
        if not 0 <= start < duration_ms or end < start:
            raise ValueError("Invalid source timestamp")
        segments.append(TranscriptSegment(
            id=f"seg-{uuid.uuid4().hex}", session_id=session_id,
            start_ms=start, end_ms=min(end, duration_ms),
            speaker_role="UNKNOWN", text=text, revision=1,
        ))
    return segments


def threads() -> int:
    return int(os.getenv("WHISPER_THREADS", "4"))


def timeout() -> int:
    return int(os.getenv("WHISPER_TIMEOUT_SECONDS", "1800"))

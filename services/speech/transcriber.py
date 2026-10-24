import hashlib
import json
import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from services.api.schemas import TranscriptSegment
from services.speech.audio import SpeechError, decode_audio

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Config:
    binary: str
    model: str
    language: str = "auto"
    threads: int = 4
    timeout: int = 1800

    @classmethod
    def from_env(cls):
        return cls(
            os.getenv("WHISPER_BINARY", str(ROOT / "data/tools/whisper.cpp/build/bin/whisper-cli")),
            os.getenv("WHISPER_MODEL", str(ROOT / "models/ggml-base.bin")),
            os.getenv("WHISPER_LANGUAGE", "auto"),
            int(os.getenv("WHISPER_THREADS", "4")),
            int(os.getenv("WHISPER_TIMEOUT_SECONDS", "1800")),
        )

    def identity(self) -> dict:
        def digest(path):
            with open(path, "rb") as source:
                return hashlib.file_digest(source, "sha256").hexdigest()
        if not Path(self.binary).is_file():
            raise SpeechError("RUNTIME_MISSING", "whisper.cpp is not installed; follow services/speech/README.md.")
        if not Path(self.model).is_file():
            raise SpeechError("MODEL_MISSING", "Download the local multilingual Whisper model before transcribing.")
        return {"runtime": "whisper.cpp", "binary_sha256": digest(self.binary),
                "model": Path(self.model).name, "model_sha256": digest(self.model),
                "language": self.language, "threads": self.threads, "device": "cpu"}


def transcribe(audio_path, *, session_id: str, config: Config | None = None) -> list[TranscriptSegment]:
    return transcribe_with_metrics(audio_path, session_id=session_id, config=config)[0]


def transcribe_with_metrics(audio_path, *, session_id: str, config: Config | None = None):
    config = config or Config.from_env()
    identity = config.identity()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pshynox-asr-") as temp:
        wav = Path(temp) / "input.wav"
        info = decode_audio(Path(audio_path), wav)
        # Ordinary stereo uploads are mixed for ASR; the original channels remain
        # in the saved recording. This is not speaker-separated transcription.
        info["channel_mode"] = "stereo_mix" if info["channels"] == 2 else "mono"
        if info["silent"]:
            return [], {**identity, **info, "elapsed_seconds": time.monotonic() - started}
        output = Path(temp) / "result"
        try:
            result = subprocess.run([
                config.binary, "-m", config.model, "-f", str(wav), "-l", config.language,
                "-t", str(config.threads), "-ng", "-oj", "-of", str(output),
            ], capture_output=True, timeout=config.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SpeechError("ASR_UNAVAILABLE", "Local transcription could not finish. Check the runtime and timeout settings.") from exc
        if result.returncode:
            raise SpeechError("ASR_FAILED", "Local transcription failed. Check model/runtime compatibility and available memory.")
        try:
            payload = json.loads(output.with_suffix(".json").read_text())
            segments = parse_segments(payload, session_id=session_id, duration_ms=info["duration_ms"])
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise SpeechError("INVALID_ASR_OUTPUT", "The runtime returned invalid transcript data.") from exc
        elapsed = time.monotonic() - started
        return segments, {**identity, **info, "elapsed_seconds": elapsed,
                          "real_time_factor": elapsed / (info["duration_ms"] / 1000)}


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

"""whisper.cpp adapter: Urdu Sukhan or the multilingual base model, explicit language, VAD on by default.

The GPU build (``ASR_WHISPER_GPU_BINARY``, e.g. a Vulkan build) is tried first unless ``ASR_DEVICE=cpu``; if it is
missing or fails, the CPU build runs with ``-ng`` and the run records a notice. ``auto`` language detection is never
used in ``urdu_mixed`` mode (the selection always sets ``ur`` there).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
import wave
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from services.api.schemas import TranscriptSegment
from services.speech.asr import manifest
from services.speech.audio import SpeechError


VK_DEVICE = re.compile(r"ggml_vulkan:\s*(\d+)\s*=\s*(.+?)\s*\|\s*uma:\s*(\d)")


def pick_vulkan_device(probe_output: str) -> tuple[str, str] | None:
    """(index, name) of the first discrete GPU (``uma: 0``) in ggml's Vulkan device list, else None.

    On a laptop the integrated GPU is usually device 0, so ggml's default would skip the NVIDIA card."""
    for index, name, uma in VK_DEVICE.findall(probe_output):
        if uma == "0":
            return index, name.strip()
    return None


@lru_cache(maxsize=4)
def vulkan_device(gpu_binary: str, vad_model: str | None) -> tuple[str, str] | None:
    """``ASR_VULKAN_DEVICE`` if set, else a one-off probe (cached per process): the GPU build's small VAD tool on
    0.1 s of silence lists the Vulkan devices in about 3 s."""
    forced = os.getenv("ASR_VULKAN_DEVICE", "").strip()
    if forced:
        return forced, f"Vulkan device {forced} (ASR_VULKAN_DEVICE)"
    tool = Path(gpu_binary).with_name("whisper-vad-speech-segments")
    if not tool.is_file() or not vad_model:
        return None
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pshynox-vk-") as temp:
        wav = Path(temp) / "probe.wav"
        with wave.open(str(wav), "wb") as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b"\0\0" * 1600)
        try:
            out = subprocess.run([str(tool), "-ug", "-vm", vad_model, "-f", str(wav)], capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
    return pick_vulkan_device(out.stdout + out.stderr)


def _incomplete_tail(data: bytes) -> int:
    """Bytes at the end of ``data`` that start a UTF-8 character but do not finish it (0 if none)."""
    for k in range(1, min(4, len(data)) + 1):
        byte = data[-k]
        if byte & 0xC0 == 0x80:          # continuation byte: keep looking for the lead byte
            continue
        if byte >= 0xC0:                 # lead byte of a 2-, 3- or 4-byte character
            need = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4
            return k if need > k else 0
        return 0
    return 0


def repair_texts(texts: list[str]) -> tuple[list[str], int]:
    """Re-join UTF-8 characters that whisper.cpp split across segments; drop fragments it never completed.

    Whisper's tokens are byte-level, so a segment boundary can fall inside a multi-byte character (Urdu script, for
    example). The texts come in with undecodable bytes kept as surrogates (``surrogateescape``). A character cut at a
    boundary is moved whole into the next segment; a fragment that still is not valid UTF-8 is dropped (never shown as
    "\ufffd"). Returns the texts and the number of bytes dropped."""
    out, carry, dropped = [], b"", 0
    for text in texts:
        data = carry + text.encode("utf-8", "surrogateescape")
        cut = len(data) - _incomplete_tail(data)
        carry = data[cut:]
        clean = data[:cut].decode("utf-8", "ignore")
        dropped += cut - len(clean.encode("utf-8"))
        out.append(clean)
    return out, dropped + len(carry)


def load_output(path: Path) -> tuple[dict, int]:
    """Read whisper.cpp ``-oj`` output, which can hold invalid UTF-8 for non-Latin scripts, and repair its texts."""
    payload = json.loads(path.read_bytes().decode("utf-8", "surrogateescape"))
    items = payload.get("transcription") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not all(isinstance(i, dict) and isinstance(i.get("text"), str) for i in items):
        return payload, 0
    texts, dropped = repair_texts([i["text"] for i in items])
    for item, text in zip(items, texts):
        item["text"] = text
    return payload, dropped


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

    def _run(self, args: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(args, capture_output=True, timeout=self.timeout, **({"env": env} if env else {}))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SpeechError("ASR_UNAVAILABLE", "Local transcription could not finish. Check the runtime and timeout settings.") from exc

    def transcribe(self, wav: Path, info: dict, session_id: str, workdir: Path) -> tuple[list[TranscriptSegment], dict]:
        output = workdir / "result"
        attempts = []
        if self.device != "cpu" and self.gpu_binary and self.gpu_binary.is_file():
            attempts.append((self.gpu_binary, True))
        attempts.append((self.cpu_binary, False))
        device_name = None
        for binary, gpu in attempts:
            env = None
            if gpu and "GGML_VK_VISIBLE_DEVICES" not in os.environ:
                chosen = vulkan_device(str(binary), str(self.vad_model) if self.vad_model else None)
                if chosen:
                    env, device_name = {**os.environ, "GGML_VK_VISIBLE_DEVICES": chosen[0]}, chosen[1]
            result = self._run(self.command(binary, wav, output, gpu), env)
            if result.returncode == 0:
                break
            if gpu:
                self.notices.append("GPU transcription failed — used CPU")
        else:
            raise SpeechError("ASR_FAILED", "Local transcription failed. Check model/runtime compatibility and available memory.")
        try:
            payload, dropped = load_output(output.with_suffix(".json"))
            segments = parse_segments(payload, session_id=session_id, duration_ms=info["duration_ms"])
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise SpeechError("INVALID_ASR_OUTPUT", "The runtime returned invalid transcript data.") from exc
        return segments, {"device": "gpu" if gpu else "cpu", **({"gpu_device": device_name} if gpu and device_name else {}),
                          **({"text_bytes_dropped": dropped} if dropped else {})}


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

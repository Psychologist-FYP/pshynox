"""Parakeet TDT 0.6B v2 adapter through onnx-asr (ONNX Runtime; no NeMo or PyTorch).

The decoded 16 kHz mono WAV is cut into windows of at most 25 s (``chunking``). Each window is recognised with token
timestamps, and the timestamps are mapped back to original recording time. ONNX Runtime uses the CUDA provider when
it is installed and ``ASR_DEVICE`` is not ``cpu``, otherwise the CPU provider; the run records which one it used.
"""
from __future__ import annotations

import subprocess
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from services.api.schemas import TranscriptSegment
from services.speech.asr import chunking, manifest
from services.speech.audio import SpeechError

MODEL_NAME = "nemo-parakeet-tdt-0.6b-v2"
# A CPU model lives in system RAM, not in the GPU memory the LLM needs, so the worker keeps it between jobs (meetings
# send one-minute chunks; loading takes about 8 s). A GPU model is loaded per job and released with the memory slot.
_CPU_CACHE: dict[tuple[str, str | None], object] = {}


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        if wav.getframerate() != chunking.RATE or wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise SpeechError("INVALID_AUDIO", "Parakeet needs 16 kHz mono 16-bit audio.")
        data = wav.readframes(wav.getnframes())
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def silero_regions(vad_binary: Path, vad_model: Path, wav: Path, timeout: int = 600) -> list[tuple[int, int]] | None:
    """Speech regions (ms) from whisper.cpp's Silero VAD tool, or None when it is unavailable or fails."""
    if not vad_binary.is_file() or not vad_model.is_file():
        return None
    try:
        result = subprocess.run([str(vad_binary), "-vm", str(vad_model), "-f", str(wav), "-np"], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    return parse_vad_output(result.stdout + "\n" + result.stderr)


def parse_vad_output(text: str) -> list[tuple[int, int]] | None:
    """whisper.cpp v1.8.3 ``whisper-vad-speech-segments`` output → regions in ms, or None if it is not that output.

    The tool prints ``Detected 3 speech segments:`` and lines like ``Speech segment 2: start = 426.00, end = 854.00``
    in centiseconds (whisper's 10 ms time unit: 854.00 is 8.54 s)."""
    import re
    if not re.search(r"Detected\s+\d+\s+speech segments", text):
        return None
    regions = [(round(float(a) * 10), round(float(b) * 10))
               for a, b in re.findall(r"start\s*=\s*([0-9.]+)\s*,\s*end\s*=\s*([0-9.]+)", text)]
    return [(a, b) for a, b in regions if b > a]


@dataclass
class Parakeet:
    folder: Path
    vad_model: Path | None = None
    vad_binary: Path | None = None
    device: str = "auto"
    notices: list[str] = field(default_factory=list)
    name: str = "parakeet-onnx"
    _model: object = None
    _provider: str = ""

    def _files(self) -> tuple[str, list[Path]]:
        int8 = (self.folder / "encoder-model.int8.onnx").is_file()
        quant = "int8" if int8 else None
        suffix = ".int8.onnx" if int8 else ".onnx"
        files = [self.folder / f"encoder-model{suffix}", self.folder / f"decoder_joint-model{suffix}", self.folder / "nemo128.onnx",
                 self.folder / "vocab.txt"]
        return quant, files

    def identity(self) -> dict:
        quant, files = self._files()
        missing = [f.name for f in files if not f.is_file()]
        if missing:
            raise SpeechError("MODEL_MISSING", f"Parakeet files missing: {', '.join(missing)}; see docs/ASR_MODELS.md.")
        checked = {f.name: manifest.verify(f) for f in files}
        enc = checked[files[0].name]
        out = {"runtime": "onnx-asr", "model": f"parakeet-tdt-0.6b-v2 ({quant or 'fp32'})", "model_id": enc["model_id"],
               "model_label": f"Parakeet TDT 0.6B v2 (ONNX {quant or 'fp32'})", "model_sha256": enc["sha256"],
               "model_licence": enc["licence"], "model_non_commercial": enc["non_commercial"], "model_manifest": enc["manifest"],
               "model_files_sha256": {k: v["sha256"] for k, v in checked.items()}, "language": "en", "vad": bool(self.vad_model)}
        return out

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            import onnx_asr
            import onnxruntime
        except ImportError as exc:
            raise SpeechError("RUNTIME_MISSING", "onnx-asr / onnxruntime are not installed; see docs/ASR_MODELS.md.") from exc
        available = onnxruntime.get_available_providers()
        providers = (["CUDAExecutionProvider"] if self.device != "cpu" and "CUDAExecutionProvider" in available else []) + ["CPUExecutionProvider"]
        quant, _ = self._files()
        key = (str(self.folder.resolve()), quant)
        if providers[0] == "CPUExecutionProvider" and key in _CPU_CACHE:
            self._model, self._provider = _CPU_CACHE[key], "CPUExecutionProvider"
            return self._model
        try:
            model = onnx_asr.load_model(MODEL_NAME, str(self.folder), quantization=quant, providers=providers)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clear error, never silent
            if providers[0] != "CPUExecutionProvider":
                self.notices.append("GPU transcription failed — used CPU")
                providers = ["CPUExecutionProvider"]
                model = onnx_asr.load_model(MODEL_NAME, str(self.folder), quantization=quant, providers=providers)
            else:
                raise SpeechError("ASR_FAILED", f"Parakeet could not load ({type(exc).__name__}).") from exc
        self._provider = providers[0]
        self._model = model.with_timestamps()
        if self._provider == "CPUExecutionProvider":
            _CPU_CACHE[key] = self._model
        return self._model

    def windows(self, samples: np.ndarray, wav: Path) -> tuple[list[chunking.Window], str]:
        regions = None
        if self.vad_model and self.vad_binary:
            regions = silero_regions(self.vad_binary, self.vad_model, wav)
        method = "silero"
        if regions is None:
            regions, method = chunking.energy_regions(samples), "energy"
            if self.vad_model:
                self.notices.append("Silero VAD unavailable — used the energy detector to cut audio")
        return chunking.plan_windows(regions, energy=chunking.frame_energy(samples)), method

    def transcribe(self, wav: Path, info: dict, session_id: str, workdir: Path) -> tuple[list[TranscriptSegment], dict]:
        samples = read_wav(wav)
        windows, method = self.windows(samples, wav)
        model = self._load()
        segments: list[TranscriptSegment] = []
        duration = info["duration_ms"]
        for w in windows:
            piece = samples[w.start_ms * 16: w.end_ms * 16]
            if len(piece) < 1600:  # under 0.1 s
                continue
            try:
                result = model.recognize(piece, sample_rate=chunking.RATE)
            except Exception as exc:  # noqa: BLE001
                raise SpeechError("ASR_FAILED", f"Parakeet failed on audio at {w.start_ms // 1000} s ({type(exc).__name__}).") from exc
            tokens, stamps = list(getattr(result, "tokens", None) or []), list(getattr(result, "timestamps", None) or [])
            if not tokens and getattr(result, "text", "").strip():
                tokens, stamps = [result.text], [0.0]
            for start, end, text in chunking.map_window(w, tokens, stamps):
                if start >= duration:
                    continue
                segments.append(TranscriptSegment(id=f"seg-{uuid.uuid4().hex}", session_id=session_id, start_ms=start,
                                                  end_ms=min(end, duration), speaker_role="UNKNOWN", text=text, revision=1))
        return segments, {"device": "gpu" if self._provider == "CUDAExecutionProvider" else "cpu", "provider": self._provider,
                          "windows": len(windows), "vad_method": method}

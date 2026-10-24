"""Bounded local media decoding. Never fetch remote media."""
import json
import subprocess
import wave
from array import array
from pathlib import Path

MAX_SECONDS = 300
MAX_BYTES = 100 * 1024 * 1024


class SpeechError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def run_media(args: list[str], timeout: int = 60) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SpeechError("DECODER_MISSING", "Install FFmpeg and ffprobe locally.") from exc
    except subprocess.TimeoutExpired as exc:
        raise SpeechError("DECODE_TIMEOUT", "Audio decoding exceeded its time limit.") from exc
    if result.returncode:
        raise SpeechError("INVALID_AUDIO", "The recording could not be decoded as supported audio.")
    return result.stdout


def inspect_audio(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_BYTES:
        raise SpeechError("INVALID_AUDIO", "Audio must be nonempty and at most 100 MB.")
    try:
        info = json.loads(run_media([
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-format_whitelist", "wav,matroska,webm,ogg,mov", "-show_streams",
            "-show_format", "-of", "json", str(path),
        ]))
        streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
        if len(streams) != 1 or any(s["codec_type"] == "video" for s in info["streams"]):
            raise ValueError()
        stream = streams[0]
        duration = float(info.get("format", {}).get("duration", stream.get("duration", 0)))
        channels = int(stream["channels"])
        if channels not in (1, 2) or duration > MAX_SECONDS + .1:
            raise ValueError()
        return {"duration_ms": round(duration * 1000), "sample_rate": int(stream["sample_rate"]), "channels": channels}
    except (KeyError, ValueError, TypeError) as exc:
        raise SpeechError("INVALID_AUDIO", "Use audio with one or two channels, no video, and at most five minutes.") from exc


def decode_audio(path: Path, output: Path, channel: int | None = None) -> dict:
    info = inspect_audio(path)
    if channel is not None and not 0 <= channel < info["channels"]:
        raise SpeechError("INVALID_CHANNEL", "Requested channel does not exist.")
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-format_whitelist", "wav,matroska,webm,ogg,mov", "-i", str(path),
            "-map", "0:a:0", "-t", str(MAX_SECONDS + 1)]
    if channel is not None:
        args += ["-af", f"pan=mono|c0=c{channel}"]
    args += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)]
    run_media(args)
    with wave.open(str(output), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
        if not 0 < duration <= MAX_SECONDS + .1:
            raise SpeechError("INVALID_DURATION", "Recording must contain audio and be no longer than five minutes.")
        samples = array("h", wav.readframes(wav.getnframes()))
    info["duration_ms"] = round(duration * 1000)
    # Reject digital silence before ASR, rather than inventing speech in empty audio.
    info["silent"] = not samples or max(abs(v) for v in samples) <= 8
    return info

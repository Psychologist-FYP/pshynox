"""Bounded local media decoding. Never fetch remote media."""
import json
import subprocess
import wave
from array import array
from pathlib import Path

# One recording (browser recording or imported file) may be up to 45 minutes. 600 MiB fits a 45-minute 48 kHz stereo
# 16-bit WAV (about 520 MB); browser recordings (Opus) are far smaller. Online meetings are unaffected: they upload
# separate one-minute chunks.
MAX_MINUTES = 45
MAX_SECONDS = MAX_MINUTES * 60
MAX_BYTES = 600 * 1024 * 1024
MAX_MB = MAX_BYTES // (1024 * 1024)
DECODE_TIMEOUT = 300
SILENCE_BLOCK_FRAMES = 16000 * 10


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
        raise SpeechError("INVALID_AUDIO", f"Audio must be nonempty and at most {MAX_MB} MB.")
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
        raise SpeechError("INVALID_AUDIO", f"Use audio with one or two channels, no video, and at most {MAX_MINUTES} minutes.") from exc


def decode_audio(path: Path, output: Path, channel: int | None = None) -> dict:
    info = inspect_audio(path)
    if channel is not None and not 0 <= channel < info["channels"]:
        raise SpeechError("INVALID_CHANNEL", "Requested channel does not exist.")
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-format_whitelist", "wav,matroska,webm,ogg,mov", "-i", str(path),
            "-map", "0:a:0", "-t", str(MAX_SECONDS + 1)]
    if channel is not None:
        args += ["-af", f"pan=mono|c0=c{channel}"]
    args += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)]
    run_media(args, timeout=DECODE_TIMEOUT)
    with wave.open(str(output), "rb") as wav:
        duration = wav.getnframes() / wav.getframerate()
        if not 0 < duration <= MAX_SECONDS + .1:
            raise SpeechError("INVALID_DURATION", f"Recording must contain audio and be no longer than {MAX_MINUTES} minutes.")
        # Reject digital silence before ASR, rather than inventing speech in empty audio. Read in blocks and stop at
        # the first audible one, so long recordings are not held in memory as one array.
        silent = True
        while silent:
            block = wav.readframes(SILENCE_BLOCK_FRAMES)
            if not block:
                break
            samples = array("h", block)
            silent = max(map(abs, samples), default=0) <= 8
    info["duration_ms"] = round(duration * 1000)
    info["silent"] = silent
    return info

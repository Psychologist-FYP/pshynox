"""One-command ASR smoke test per mode (fictional synthetic speech; nothing is saved to the app database).

    python -m services.speech.smoke --mode english
    python -m services.speech.smoke --mode urdu_mixed
    python -m services.speech.smoke --mode urdu_mixed --wav path/to/sample.wav

Without ``--wav`` a short sample is synthesised with espeak-ng in a temporary folder: English for ``english``, and for
``urdu_mixed`` Urdu phrases with English words in between (Urdu and English voices, joined). Prints the model used
(name, file, SHA-256, licence), engine, language, device, VAD, fallback notices, segment count, real-time factor, peak
VRAM and the segments. Peak VRAM is sampled from ``nvidia-smi`` every 100 ms for the whole GPU, so it also shows the
rise above the idle level measured just before. Exit code 0 when at least one segment was produced, 1 otherwise.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
import wave
from pathlib import Path

from services.speech.audio import SpeechError
from services.speech.transcriber import transcribe_with_metrics

SAMPLES = {
    "english": [("en", "Good morning. I have been sleeping badly for about two weeks. "
                       "Most nights I wake up at four and cannot get back to sleep.")],
    # Fictional. Urdu in Urdu script with English words spoken by the English voice, as Urdu Sukhan would write them.
    "urdu_mixed": [("ur", "السلام علیکم۔ میں دو ہفتوں سے ٹھیک طرح سو نہیں پا رہا۔ آج کل مجھے بہت"), ("en", "anxiety"),
                   ("ur", "ہوتی ہے اور"), ("en", "office"), ("ur", "میں"), ("en", "panic attacks"), ("ur", "بھی آتے ہیں۔")],
}


def synthesise(mode: str, folder: Path) -> Path:
    if not shutil.which("espeak-ng"):
        raise SystemExit("espeak-ng is not installed; pass --wav with a sample recording instead.")
    parts = []
    for i, (voice, text) in enumerate(SAMPLES[mode]):
        part = folder / f"part-{i}.wav"
        subprocess.run(["espeak-ng", "-v", voice, "-s", "145", "-w", str(part), text], check=True, capture_output=True)
        parts.append(part)
    out = folder / f"sample-{mode}.wav"
    with wave.open(str(out), "wb") as target:
        for i, part in enumerate(parts):
            with wave.open(str(part), "rb") as source:
                if i == 0:
                    target.setparams(source.getparams())
                target.writeframes(source.readframes(source.getnframes()))
    return out


class GpuMemory:
    """Peak GPU memory in use (MiB) while the block runs, from ``nvidia-smi -lms 100``. ``None`` without nvidia-smi."""

    def __init__(self):
        self.baseline = self.peak = None
        self._proc = self._thread = None

    @staticmethod
    def _query(*extra: str) -> list[str]:
        return ["nvidia-smi", "--query-gpu=memory.used,memory.total,name", "--format=csv,noheader,nounits", *extra]

    def __enter__(self):
        if not shutil.which("nvidia-smi"):
            return self
        try:
            first = subprocess.run(self._query(), capture_output=True, text=True, timeout=10).stdout.splitlines()[0]
            used, self.total, self.name = [v.strip() for v in first.split(",", 2)]
            self.baseline = self.peak = int(used)
            self._proc = subprocess.Popen(self._query("-lms", "100"), stdout=subprocess.PIPE, text=True)
        except (OSError, subprocess.TimeoutExpired, IndexError, ValueError):
            return self
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()
        return self

    def _read(self):
        for line in self._proc.stdout:
            try:
                self.peak = max(self.peak, int(line.split(",")[0]))
            except ValueError:
                pass

    def __exit__(self, *exc):
        if self._proc:
            self._proc.terminate()
            self._proc.wait(timeout=5)
            self._thread.join(timeout=5)

    def describe(self) -> str:
        if self.baseline is None:
            return "n/a (nvidia-smi not available)"
        return (f"{self.peak} MiB of {self.total} MiB on {self.name} "
                f"({self.peak - self.baseline} MiB above the {self.baseline} MiB in use before the run)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["english", "urdu_mixed"], required=True)
    parser.add_argument("--wav", type=Path, help="sample WAV (default: synthesised with espeak-ng)")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="pshynox-asr-smoke-") as temp:
        wav = args.wav or synthesise(args.mode, Path(temp))
        try:
            with GpuMemory() as vram:
                segments, m = transcribe_with_metrics(wav, session_id="smoke-test", mode=args.mode)
        except SpeechError as exc:
            print(f"FAILED {exc.code}: {exc}")
            return 1
    device = m.get("device", "?") + (f" ({m.get('gpu_backend')}, {m.get('gpu_device', 'unknown GPU')})" if m.get("device") == "gpu" else "")
    print(f"model {m.get('asr_model_label', m['asr_model'])} · file {m['asr_model']} · sha256 {str(m.get('model_sha256', ''))[:12]}… "
          f"· manifest {m.get('model_manifest')} · licence {m.get('model_licence')}"
          f"{' (non-commercial use only)' if m.get('model_non_commercial') else ''}")
    print(f"mode {m['asr_mode']} · engine {m['asr_engine']} · language {m['language']} · device {device} · "
          f"VAD {'on' if m.get('vad') else 'off'}")
    for notice in m.get("asr_notices", []):
        print(f"NOTICE: {notice}")
    print(f"audio {m.get('duration_ms', 0) / 1000:.1f} s · {len(segments)} segment(s) · real-time factor "
          f"{m.get('real_time_factor', 0):.2f} ({m.get('elapsed_seconds', 0):.1f} s incl. model load) · peak VRAM {vram.describe()}")
    for s in segments:
        print(f"  [{s.start_ms / 1000:6.2f}–{s.end_ms / 1000:6.2f} s] {s.text}")
    return 0 if segments else 1


if __name__ == "__main__":
    sys.exit(main())

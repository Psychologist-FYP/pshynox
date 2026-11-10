"""One-command ASR smoke test per mode (fictional synthetic speech; nothing is saved to the app database).

    python -m services.speech.smoke --mode english
    python -m services.speech.smoke --mode urdu_mixed
    python -m services.speech.smoke --mode english --wav path/to/sample.wav

Without ``--wav`` a short sample is synthesised with espeak-ng (English, or Urdu for ``urdu_mixed``) in a temporary
folder. Prints the engine, model, language, fallback notices, device, real-time factor and segments. Exit code 0 when
at least one segment was produced, 1 otherwise.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from services.speech.audio import SpeechError
from services.speech.transcriber import transcribe_with_metrics

SAMPLES = {
    "english": ("en", "Good morning. I have been sleeping badly for about two weeks. "
                      "Most nights I wake up at four and cannot get back to sleep."),
    "urdu_mixed": ("ur", "السلام علیکم۔ میں دو ہفتوں سے ٹھیک طرح سو نہیں پا رہا۔ رات کو چار بجے آنکھ کھل جاتی ہے۔"),
}


def synthesise(mode: str, folder: Path) -> Path:
    if not shutil.which("espeak-ng"):
        raise SystemExit("espeak-ng is not installed; pass --wav with a sample recording instead.")
    voice, text = SAMPLES[mode]
    out = folder / f"sample-{mode}.wav"
    subprocess.run(["espeak-ng", "-v", voice, "-s", "145", "-w", str(out), text], check=True, capture_output=True)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mode", choices=["english", "urdu_mixed"], required=True)
    parser.add_argument("--wav", type=Path, help="sample WAV (default: synthesised with espeak-ng)")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="pshynox-asr-smoke-") as temp:
        wav = args.wav or synthesise(args.mode, Path(temp))
        try:
            segments, m = transcribe_with_metrics(wav, session_id="smoke-test", mode=args.mode)
        except SpeechError as exc:
            print(f"FAILED {exc.code}: {exc}")
            return 1
    print(f"mode {m['asr_mode']} · engine {m['asr_engine']} · model {m['asr_model']} · language {m['language']} · "
          f"device {m.get('device', '?')} · VAD {'on' if m.get('vad') else 'off'} · manifest {m.get('model_manifest')}")
    for notice in m.get("asr_notices", []):
        print(f"NOTICE: {notice}")
    print(f"{len(segments)} segment(s); real-time factor {m.get('real_time_factor', 0):.2f}")
    for s in segments:
        print(f"  [{s.start_ms / 1000:6.2f}–{s.end_ms / 1000:6.2f} s] {s.text}")
    return 0 if segments else 1


if __name__ == "__main__":
    sys.exit(main())

import argparse
import json
import sys
from services.speech.audio import SpeechError
from services.speech.transcriber import transcribe_with_metrics

parser = argparse.ArgumentParser(description="Transcribe local audio offline with whisper.cpp")
parser.add_argument("audio")
parser.add_argument("--session-id", default="cli-session")
args = parser.parse_args()
try:
    segments, metrics = transcribe_with_metrics(args.audio, session_id=args.session_id)
    print(json.dumps({"segments": [s.model_dump(mode="json") for s in segments], "metrics": metrics}, ensure_ascii=False, indent=2))
except SpeechError as exc:
    print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}), file=sys.stderr)
    sys.exit(1)

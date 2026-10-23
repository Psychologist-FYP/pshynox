"""Synthetic regression fixtures: no user recordings or transcript text."""
import json
import subprocess
from pathlib import Path

import pytest

from services.speech.transcriber import Config, parse_segments, transcribe


def item(start, end):
    return {"text": "Fictional test utterance.", "offsets": {"from": start, "to": end}}


def test_end_estimate_beyond_eof_preserves_all_segments():
    segments = parse_segments({"transcription": [item(0, 10620), item(10620, 21020), item(21020, 23560)]},
                              session_id="test-session", duration_ms=23280)
    assert [(s.start_ms, s.end_ms) for s in segments] == [(0, 10620), (10620, 21020), (21020, 23280)]
    assert all(s.speaker_role == "UNKNOWN" and s.revision == 1 for s in segments)


@pytest.mark.parametrize("start,end", [(-1, 100), (200, 100), (1000, 1200), (1200, 1300), (True, 500), (0, "500"), (0, None)])
def test_invalid_offsets_are_not_repaired(start, end):
    with pytest.raises(ValueError):
        parse_segments({"transcription": [item(start, end)]}, session_id="test", duration_ms=1000)


def test_valid_source_offsets_are_unchanged():
    segments = parse_segments({"transcription": [item(100, 800), item(800, 1000)]}, session_id="test", duration_ms=1000)
    assert [(s.start_ms, s.end_ms) for s in segments] == [(100, 800), (800, 1000)]


def test_runtime_adapter_accepts_estimated_end_overrun(monkeypatch, tmp_path):
    from services.speech import transcriber
    monkeypatch.setattr(Config, "identity", lambda self: {})
    monkeypatch.setattr(transcriber, "decode_audio", lambda *args: {"duration_ms": 23280, "channels": 1, "silent": False})

    def run(args, **kwargs):
        output = Path(args[args.index("-of") + 1]).with_suffix(".json")
        output.write_text(json.dumps({"transcription": [item(21020, 23560)]}))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(transcriber.subprocess, "run", run)
    segments = transcribe(tmp_path / "synthetic.webm", session_id="test", config=Config("fake", "fake"))
    assert len(segments) == 1
    assert segments[0].end_ms == 23280

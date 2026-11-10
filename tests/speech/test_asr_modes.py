"""ASR language modes: selection, visible fallback, per-track override, manifest checks, Parakeet chunk timestamps."""
import json
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select as sql_select

from services.api.db import RecordingRecord, TranscriptionJobRecord
from services.api.main import app, session_factory
from services.api.schemas import TranscriptSegment
from services.speech.asr import chunking, manifest, resolve_mode
from services.speech.asr import selection as sel
from services.speech.asr.chunking import Window, map_window, plan_windows
from services.speech.asr.parakeet import Parakeet, parse_vad_output
from services.speech.asr.whisper_cpp import WhisperCpp
from services.speech.audio import SpeechError
from services.speech.worker import process_one
from tests.api.test_meetings import chunk, consent_pair, create, headers
from tests.speech.test_pipeline import queue, session, upload, wav_bytes


@pytest.fixture
def models(tmp_path, monkeypatch):
    """Point every ASR setting at files under tmp_path; each test creates the ones it needs."""
    paths = {"ASR_ENGLISH_MODEL": tmp_path / "parakeet", "ASR_URDU_MODEL": tmp_path / "urdu.bin",
             "ASR_FALLBACK_MODEL": tmp_path / "base.bin", "ASR_VAD_MODEL": tmp_path / "vad.bin"}
    for key, value in paths.items():
        monkeypatch.setenv(key, str(value))
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    paths["ASR_FALLBACK_MODEL"].write_bytes(b"fallback")
    return paths


def install_parakeet(folder: Path):
    folder.mkdir()
    for name in ("config.json", "vocab.txt", "nemo128.onnx", "encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"):
        (folder / name).write_bytes(b"x")


# ---------------------------------------------------------------- mode selection and fallback

def test_mode_resolution_prefers_track_then_session_then_default():
    assert resolve_mode() == "urdu_mixed"
    assert resolve_mode("english") == "english"
    assert resolve_mode("english", "urdu_mixed") == "urdu_mixed"
    assert resolve_mode(None, "english") == "english"
    with pytest.raises(ValueError):
        resolve_mode("auto")


def test_english_uses_parakeet_when_installed(models, monkeypatch):
    install_parakeet(models["ASR_ENGLISH_MODEL"])
    monkeypatch.setattr(sel, "parakeet_runtime", lambda: True)
    s = sel.select("english")
    assert (s.engine, s.model, s.language, s.fallback) == ("parakeet-onnx", models["ASR_ENGLISH_MODEL"], "en", False)


def test_missing_english_model_or_runtime_falls_back_with_a_notice(models, monkeypatch):
    s = sel.select("english")
    assert (s.engine, s.model.name, s.language, s.fallback) == ("whisper.cpp", "base.bin", "en", True)
    assert s.notices[0] == "English model not installed — using fallback"
    install_parakeet(models["ASR_ENGLISH_MODEL"])
    monkeypatch.setattr(sel, "parakeet_runtime", lambda: False)
    assert sel.select("english").notices[0].startswith("English runtime (onnx-asr, onnxruntime) not installed")


def test_urdu_uses_sukhan_when_present_and_never_auto(models):
    s = sel.select("urdu_mixed")
    assert (s.model.name, s.language, s.fallback, s.notices[0]) == ("base.bin", "ur", True, "Urdu model not installed — using fallback")
    models["ASR_URDU_MODEL"].write_bytes(b"sukhan")
    s = sel.select("urdu_mixed")
    assert (s.model.name, s.language, s.fallback) == ("urdu.bin", "ur", False)
    assert sel.select(None).mode == "urdu_mixed"  # the default mode
    with pytest.raises(SpeechError):
        WhisperCpp(models["ASR_URDU_MODEL"], "auto", Path("whisper-cli"))


def test_missing_vad_model_is_reported_not_silent(models):
    s = sel.select("urdu_mixed")
    assert s.vad_model is None and any("VAD model not installed" in n for n in s.notices)
    models["ASR_VAD_MODEL"].write_bytes(b"vad")
    assert sel.select("urdu_mixed").vad_model == models["ASR_VAD_MODEL"]


def test_status_lists_both_modes_for_the_ui(models):
    with TestClient(app) as c:
        data = c.get("/api/v1/asr/status").json()
    assert data["default_mode"] == "urdu_mixed" and set(data["modes"]) == {"english", "urdu_mixed"}
    assert data["modes"]["urdu_mixed"]["notices"][0] == "Urdu model not installed — using fallback"


def test_whisper_command_has_explicit_language_vad_and_cpu_fallback(tmp_path):
    w = WhisperCpp(tmp_path / "m.bin", "ur", tmp_path / "cpu-cli", tmp_path / "gpu-cli", tmp_path / "vad.bin")
    cpu = w.command(w.cpu_binary, Path("in.wav"), Path("out"), gpu=False)
    assert cpu[cpu.index("-l") + 1] == "ur" and "--vad" in cpu and cpu[cpu.index("-vm") + 1].endswith("vad.bin") and "-ng" in cpu
    assert "-ng" not in w.command(w.gpu_binary, Path("in.wav"), Path("out"), gpu=True)


def test_failed_gpu_run_retries_on_cpu_and_says_so(tmp_path, monkeypatch):
    import subprocess
    for name in ("m.bin", "cpu-cli", "gpu-cli"):
        (tmp_path / name).write_bytes(b"x")
    w = WhisperCpp(tmp_path / "m.bin", "en", tmp_path / "cpu-cli", tmp_path / "gpu-cli")
    calls = []

    def run(args, **kw):
        calls.append(args[0])
        if args[0].endswith("gpu-cli"):
            return subprocess.CompletedProcess(args, 1)
        Path(args[args.index("-of") + 1]).with_suffix(".json").write_text(json.dumps({"transcription": [{"text": "hello", "offsets": {"from": 0, "to": 500}}]}))
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(subprocess, "run", run)
    segments, run_info = w.transcribe(Path("in.wav"), {"duration_ms": 1000}, "s", tmp_path)
    assert [Path(c).name for c in calls] == ["gpu-cli", "cpu-cli"] and run_info["device"] == "cpu"
    assert w.notices == ["GPU transcription failed — used CPU"] and segments[0].text == "hello"


# ---------------------------------------------------------------- manifest

def test_manifest_refuses_a_changed_or_partial_model(tmp_path, monkeypatch):
    model = tmp_path / "base.bin"
    model.write_bytes(b"original")
    entry = {"id": "t", "licence": "MIT", "files": [{"path": str(model), "size": 8, "sha256": manifest.hashlib.sha256(b"original").hexdigest()}]}
    monkeypatch.setattr(manifest, "entries", lambda: [entry])
    assert manifest.verify(model)["manifest"] == "verified" and manifest.verify(model)["licence"] == "MIT"
    model.write_bytes(b"0riginal")
    with pytest.raises(SpeechError) as exc:
        manifest.verify(model)
    assert exc.value.code == "MODEL_CHANGED"
    model.write_bytes(b"part")
    assert not manifest.complete(model)
    with pytest.raises(SpeechError) as exc:
        manifest.verify(model)
    assert exc.value.code == "MODEL_INCOMPLETE"


def test_repository_manifest_lists_every_model_with_a_licence():
    ids = {m["id"]: m for m in manifest.entries()}
    assert {"parakeet-tdt-0.6b-v2-onnx-int8", "urdu-sukhan-asr-v1-q8_0", "whisper-base-ggml", "silero-vad-v5.1.2-ggml"} <= set(ids)
    assert ids["urdu-sukhan-asr-v1-q8_0"]["licence"] == "CC-BY-NC-4.0" and ids["urdu-sukhan-asr-v1-q8_0"]["non_commercial"]
    assert all(m["licence"] for m in ids.values())
    assert all(len(f["sha256"]) == 64 for m in ids.values() if m["id"] != "urdu-sukhan-asr-v1-q8_0" for f in m["files"])


# ---------------------------------------------------------------- Parakeet chunking and timestamp mapping

def test_long_speech_is_cut_into_windows_of_at_most_25_seconds_at_quiet_points():
    energy = np.ones(70_000 // chunking.FRAME_MS, dtype=np.float32)
    energy[20_010 // chunking.FRAME_MS] = 0.0   # a pause at about 20 s
    windows = plan_windows([(0, 70_000)], energy=energy)
    assert all(w.duration_ms <= 25_000 for w in windows)
    assert windows[0].start_ms == 0 and abs(windows[0].end_ms - 20_010) <= chunking.FRAME_MS
    assert windows[-1].end_ms == 70_000 and all(a.end_ms == b.start_ms for a, b in zip(windows, windows[1:]))


def test_short_regions_share_a_window_and_far_ones_do_not():
    windows = plan_windows([(1_000, 3_000), (3_500, 6_000), (40_000, 41_000)])
    assert windows == [Window(1_000, 6_000), Window(40_000, 41_000)]


def test_window_timestamps_map_back_to_recording_time():
    w = Window(61_250, 80_000)  # second window of a long recording
    tokens = ["▁I", "▁slept", "▁badly", ".", "▁Then", "▁I", "▁woke", "."]
    stamps = [0.40, 0.72, 1.10, 1.50, 4.00, 4.30, 4.50, 4.90]
    # 2.5 s pause after the first sentence: it ends just after its last token, not at the next sentence
    assert map_window(w, tokens, stamps) == [(61_650, 62_830, "I slept badly."), (65_250, 80_000, "Then I woke.")]


def test_mapping_splits_at_long_pauses_and_clips_to_the_window():
    w = Window(10_000, 12_000)
    out = map_window(w, [" hello", " there", " again"], [0.10, 0.30, 1.60])
    assert out[0] == (10_100, 10_380, "hello there") and out[1] == (11_600, 12_000, "again")
    assert map_window(Window(0, 1_000), [" late"], [3.0]) == [(1_000, 1_000, "late")]  # never outside the window
    with pytest.raises(ValueError):
        map_window(w, ["a"], [])


def test_vad_tool_output_is_in_centiseconds():
    text = "Detected 2 speech segments:\nSpeech segment 0: start = 0.00, end = 86.00\nSpeech segment 1: start = 426.00, end = 854.00\n"
    assert parse_vad_output(text) == [(0, 860), (4_260, 8_540)]
    assert parse_vad_output("Detected 0 speech segments:") == [] and parse_vad_output("error") is None


def test_parakeet_adapter_maps_each_window_with_its_offset(tmp_path, monkeypatch):
    import wave
    samples = np.zeros(16_000 * 60, dtype=np.int16)
    samples[16_000 * 2: 16_000 * 4] = 8000     # speech at 2–4 s
    samples[16_000 * 50: 16_000 * 52] = 8000   # and at 50–52 s
    wav = tmp_path / "in.wav"
    with wave.open(str(wav), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16_000); f.writeframes(samples.tobytes())

    class Result:
        def __init__(self): self.text, self.tokens, self.timestamps = "word.", ["▁word", "."], [0.2, 0.5]

    class Model:
        def __init__(self): self.lengths = []
        def recognize(self, piece, sample_rate):
            self.lengths.append(len(piece) / sample_rate)
            return Result()
    adapter = Parakeet(tmp_path)
    adapter._model, adapter._provider = Model(), "CPUExecutionProvider"
    segments, info = adapter.transcribe(wav, {"duration_ms": 60_000}, "s1", tmp_path)
    assert info["windows"] == 2 and info["vad_method"] == "energy" and info["device"] == "cpu"
    assert all(n <= 25 for n in adapter._model.lengths)
    starts = [s.start_ms for s in segments]
    assert len(segments) == 2 and 1_800 <= starts[0] <= 2_400 and 49_800 <= starts[1] <= 50_400
    assert all(isinstance(s, TranscriptSegment) and s.session_id == "s1" and s.speaker_role.value == "UNKNOWN" for s in segments)


# ---------------------------------------------------------------- API, worker and meetings

def mode_transcriber(seen):
    def run(path, *, session_id, mode):
        seen.append(mode)
        model = "parakeet-tdt-0.6b-v2 (int8)" if mode == "english" else "ggml-base.bin"
        return [TranscriptSegment(id=f"seg-{uuid4().hex}", session_id=session_id, start_ms=0, end_ms=900, speaker_role="UNKNOWN",
                                  text="Synthetic speech.", revision=1)], {"asr_mode": mode, "asr_model": model, "asr_fallback": mode != "english",
                                                                          "asr_notices": [] if mode == "english" else ["Urdu model not installed — using fallback"]}
    return run


def test_session_mode_is_stored_on_recordings_and_jobs():
    seen = []
    with TestClient(app) as c:
        sid = session(c)
        assert c.get(f"/api/v1/sessions/{sid}").json()["asr_mode"] == "urdu_mixed"  # default
        assert c.patch(f"/api/v1/sessions/{sid}/asr-mode", json={"asr_mode": "english"}).json() == {"asr_mode": "english"}
        assert c.patch(f"/api/v1/sessions/{sid}/asr-mode", json={"asr_mode": "auto"}).status_code == 422
        rid = upload(c, sid).json()["id"]
        other = c.post(f"/api/v1/sessions/{sid}/recordings?asr_mode=urdu_mixed", content=wav_bytes(), headers={"Content-Type": "audio/wav"}).json()["id"]
        for r in (rid, other):
            queue(c, sid, r, key=r)
            process_one(session_factory, mode_transcriber(seen))
        assert seen == ["english", "urdu_mixed"]
        files = {f["id"]: f for f in c.get(f"/api/v1/sessions/{sid}/recordings").json()}
        assert files[rid]["asr_mode"] == "english" and files[other]["asr_mode"] == "urdu_mixed"
        jobs = {j["recording_id"]: j for j in c.get(f"/api/v1/sessions/{sid}/transcription-jobs").json()}
        assert (jobs[rid]["asr_mode"], jobs[rid]["asr_model"], jobs[rid]["asr_fallback"]) == ("english", "parakeet-tdt-0.6b-v2 (int8)", False)
        assert jobs[other]["asr_fallback"] and jobs[other]["asr_notices"] == ["Urdu model not installed — using fallback"]


def test_new_session_can_choose_its_mode():
    with TestClient(app) as c:
        r = c.post("/api/v1/sessions", json={"session_type": "FOLLOW_UP", "consent": {}, "asr_mode": "english"})
        assert r.status_code == 201 and r.json()["asr_mode"] == "english"


def test_meeting_tracks_can_use_different_modes():
    seen = []
    with TestClient(app) as c:
        sid, room = create(c)
        c.patch(f"/api/v1/sessions/{sid}/asr-mode", json={"asr_mode": "urdu_mixed"})
        r = c.put(f"/api/v1/meetings/{room['id']}/asr-modes", json={"CLINICIAN": "english"}, headers=headers(room))
        assert r.json()["CLINICIAN"] == "english" and r.json()["CLIENT"] == "urdu_mixed"  # patient track follows the session
        assert c.put(f"/api/v1/meetings/{room['id']}/asr-modes", json={"CLIENT": "english"}, headers=headers(room, guest=True)).status_code == 403
        assert c.get(f"/api/v1/meetings/{room['id']}", headers=headers(room)).json()["asr_modes"]["CLINICIAN"] == "english"
        assert "asr_modes" not in c.get(f"/api/v1/meetings/{room['id']}", headers=headers(room, guest=True)).json()
        consent_pair(c, room)
        assert chunk(c, room, role="CLINICIAN").status_code == 201
        assert chunk(c, room, role="CLIENT", offset=1000).status_code == 201
        while process_one(session_factory, mode_transcriber(seen)):
            pass
        assert sorted(seen) == ["english", "urdu_mixed"]
        with session_factory() as db:
            rows = db.execute(sql_select(RecordingRecord.asr_mode, TranscriptionJobRecord.asr_mode, TranscriptionJobRecord.asr_model)
                              .join(TranscriptionJobRecord, TranscriptionJobRecord.recording_id == RecordingRecord.id)
                              .where(RecordingRecord.session_id == sid)).all()
        assert sorted(rows) == [("english", "english", "parakeet-tdt-0.6b-v2 (int8)"), ("urdu_mixed", "urdu_mixed", "ggml-base.bin")]


def test_meeting_can_be_created_with_track_modes():
    with TestClient(app) as c:
        sid = c.post("/api/v1/sessions", json={"session_type": "FOLLOW_UP", "consent": {}}).json()["id"]
        room = c.post(f"/api/v1/sessions/{sid}/meetings", json={"recording_consent": True, "asr_modes": {"CLIENT": "english"}}).json()
        assert room["asr_modes"]["CLIENT"] == "english" and room["asr_modes"]["CLINICIAN"] == "urdu_mixed"

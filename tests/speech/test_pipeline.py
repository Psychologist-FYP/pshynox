import io
import json
import math
import struct
import wave
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from services.api.main import app, session_factory
from services.api.db import TranscriptionJobRecord, TranscriptSegmentRecord, SegmentSourceRecord
from services.api.schemas import TranscriptSegment
from services.speech.audio import SpeechError, decode_audio
from services.speech.transcriber import Config, transcribe
from services.speech.worker import process_one, recover_interrupted


def wav_bytes(channels=1, silent=False):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        for i in range(16000):
            value = 0 if silent else int(1000 * math.sin(i * .1))
            wav.writeframesraw(struct.pack("<h", value) + (struct.pack("<h", 0) if channels == 2 else b""))
    return output.getvalue()


def session(client, consent=True):
    return client.post("/api/v1/sessions", json={"session_type": "FOLLOW_UP", "consent": {"recording_consent": consent}}).json()["id"]


def upload(client, sid, content=None):
    return client.post(f"/api/v1/sessions/{sid}/recordings", content=content if content is not None else wav_bytes(), headers={"Content-Type": "audio/wav"})


def queue(client, sid, rid, key="first"):
    return client.post(f"/api/v1/sessions/{sid}/transcribe", json={"recording_id": rid, "idempotency_key": key})


def fake_transcriber(path, *, session_id):
    return [TranscriptSegment(id=f"seg-{uuid4().hex}", session_id=session_id, start_ms=0, end_ms=900,
        speaker_role="UNKNOWN", text="I slept four hours.", revision=1)], {"test_double": True}


def test_audio_to_database_edits_draft_and_restart():
    with TestClient(app) as c:
        sid = session(c)
        assert c.get(f"/api/v1/sessions/{sid}/transcript").json() == []
        r = upload(c, sid)
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        job = queue(c, sid, rid).json()
        assert queue(c, sid, rid).json()["id"] == job["id"]
        assert process_one(session_factory, fake_transcriber)
        assert c.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "SUCCEEDED"
        segment = c.get(f"/api/v1/sessions/{sid}/transcript").json()[0]
        c.patch(f"/api/v1/sessions/{sid}/transcript/{segment['id']}", json={"speaker_role": "CLIENT"})
        draft = c.post(f"/api/v1/sessions/{sid}/note-draft/populate").json()
        assert draft["fields"][0]["evidence_ids"] == [segment["id"]]
        assert "four hours" in draft["fields"][0]["text"]
        assert draft["risk_review_status"] == "PENDING"
        assert c.post(f"/api/v1/sessions/{sid}/risk-review/complete", json={"confirmation": True, "reviewer_name": "Demo"}).status_code == 200
        c.patch(f"/api/v1/sessions/{sid}/transcript/{segment['id']}", json={"text": "I slept five hours."})
        stale = c.get(f"/api/v1/sessions/{sid}/note-draft").json()
        assert stale["sources_stale"] and stale["risk_review_status"] == "PENDING"
        assert c.post(f"/api/v1/sessions/{sid}/approve", json={"actor_name": "Demo"}).status_code == 409
        assert c.post(f"/api/v1/sessions/{sid}/risk-review/complete", json={"confirmation": True, "reviewer_name": "Demo"}).status_code == 409
        refreshed = c.post(f"/api/v1/sessions/{sid}/note-draft/populate").json()
        assert not refreshed["sources_stale"]
        assert "five hours" in refreshed["fields"][0]["text"]
    with TestClient(app) as c:
        assert c.get(f"/api/v1/sessions/{sid}/transcript").json()[0]["text"] == "I slept five hours."
        assert c.get(f"/api/v1/sessions/{sid}/recordings/{rid}/audio").content == wav_bytes()
        assert queue(c, sid, rid).json()["status"] == "SUCCEEDED"
        assert len(c.get(f"/api/v1/sessions/{sid}/transcript").json()) == 1


def test_consent_invalid_media_and_session_isolation():
    with TestClient(app) as c:
        sid = session(c, False)
        assert upload(c, sid).status_code == 403
        c.patch(f"/api/v1/sessions/{sid}/recording-consent", json={"recording_consent": True})
        assert upload(c, sid, b"not audio").status_code == 422
        assert upload(c, sid, b"").status_code == 422
        rid = upload(c, sid).json()["id"]
        other = session(c)
        assert queue(c, other, rid).status_code == 404
        assert c.get(f"/api/v1/sessions/{other}/recordings/{rid}/audio").status_code == 404


def test_failure_retry_and_interrupted_job():
    with TestClient(app) as c:
        sid = session(c)
        rid = upload(c, sid).json()["id"]
        jid = queue(c, sid, rid).json()["id"]
        def fail(*args, **kwargs):
            raise SpeechError("MODEL_MISSING", "Missing local model")
        process_one(session_factory, fail)
        assert c.get(f"/api/v1/jobs/{jid}").json()["error_code"] == "MODEL_MISSING"
        assert queue(c, sid, rid).json()["status"] == "FAILED"
        assert queue(c, sid, rid, "retry").json()["attempt"] == 2
        with session_factory() as db:
            db.get(TranscriptionJobRecord, jid).status = "RUNNING"
            db.commit()
        recover_interrupted(session_factory)
        assert c.get(f"/api/v1/jobs/{jid}").json()["error_code"] == "WORKER_INTERRUPTED"
        queue(c, sid, rid, "retry-after-restart")
        process_one(session_factory, fake_transcriber)
        assert c.get(f"/api/v1/jobs/{jid}").json()["status"] == "SUCCEEDED"


def test_failed_batch_rolls_back_all_segments():
    with TestClient(app) as c:
        sid = session(c)
        rid = upload(c, sid).json()["id"]
        jid = queue(c, sid, rid).json()["id"]
        def duplicates(path, *, session_id):
            segments, metrics = fake_transcriber(path, session_id=session_id)
            return [segments[0], segments[0]], metrics
        process_one(session_factory, duplicates)
        assert c.get(f"/api/v1/jobs/{jid}").json()["status"] == "FAILED"
        assert c.get(f"/api/v1/sessions/{sid}/transcript").json() == []
        with session_factory() as db:
            assert not db.scalar(select(SegmentSourceRecord).where(SegmentSourceRecord.job_id == jid))


def test_channel_routing_and_digital_silence(tmp_path):
    source = tmp_path / "stereo.wav"
    source.write_bytes(wav_bytes(2))
    left = decode_audio(source, tmp_path / "left.wav", channel=0)
    right = decode_audio(source, tmp_path / "right.wav", channel=1)
    assert left["duration_ms"] == right["duration_ms"] == 1000
    assert not left["silent"] and right["silent"]


def test_missing_model_and_silence_short_circuit(tmp_path):
    source = tmp_path / "silence.wav"
    source.write_bytes(wav_bytes(silent=True))
    with pytest.raises(SpeechError, match="model"):
        transcribe(source, session_id="test", config=Config("/bin/false", str(tmp_path / "missing.bin")))
    model = tmp_path / "placeholder.bin"
    model.write_bytes(b"test placeholder: never invoked")
    assert transcribe(source, session_id="test", config=Config("/bin/false", str(model))) == []


def test_manual_note_preservation_and_terminal_transcript():
    with TestClient(app) as c:
        sid = session(c)
        rid = upload(c, sid).json()["id"]
        queue(c, sid, rid)
        process_one(session_factory, fake_transcriber)
        segment = c.get(f"/api/v1/sessions/{sid}/transcript").json()[0]
        fields = c.get(f"/api/v1/sessions/{sid}/note-draft").json()["fields"]
        fields[0].update(text="Manual clinician note", provenance="MANUAL")
        c.put(f"/api/v1/sessions/{sid}/note-draft", json={"fields": fields})
        assert c.post(f"/api/v1/sessions/{sid}/note-draft/populate").status_code == 409
        c.post(f"/api/v1/sessions/{sid}/risk-review/complete", json={"confirmation": True, "reviewer_name": "Demo"})
        assert c.post(f"/api/v1/sessions/{sid}/approve", json={"actor_name": "Demo"}).status_code == 200
        assert c.patch(f"/api/v1/sessions/{sid}/transcript/{segment['id']}", json={"text": "changed"}).status_code == 409


def test_multiple_recordings_append_preserve_edits_and_keep_sources():
    with TestClient(app) as c:
        sid = session(c)
        first = upload(c, sid).json()["id"]
        first_job = queue(c, sid, first).json()["id"]
        process_one(session_factory, fake_transcriber)
        original = c.get(f"/api/v1/sessions/{sid}/transcript").json()[0]
        c.patch(f"/api/v1/sessions/{sid}/transcript/{original['id']}", json={"text": "Manually corrected first recording."})
        second = upload(c, sid, wav_bytes(2)).json()["id"]
        response = queue(c, sid, second)
        assert response.status_code == 202, response.text
        second_job = response.json()["id"]
        assert second_job != first_job
        c.get(f"/api/v1/sessions/{sid}/note-draft")
        # A prior successful job must not hide a second pending job from review guards.
        assert c.post(f"/api/v1/sessions/{sid}/risk-review/complete", json={"confirmation": True, "reviewer_name": "Demo"}).status_code == 409
        process_one(session_factory, fake_transcriber)
        segments = c.get(f"/api/v1/sessions/{sid}/transcript").json()
        assert len(segments) == 2
        saved = next(s for s in segments if s["id"] == original["id"])
        assert saved["text"] == "Manually corrected first recording." and saved["revision"] == 2
        sources = c.get(f"/api/v1/sessions/{sid}/transcript-sources").json()
        assert sources[original["id"]] == first
        assert set(sources.values()) == {first, second}
        assert len(c.get(f"/api/v1/sessions/{sid}/transcription-jobs").json()) == 2
        assert queue(c, sid, second, "another-click").json()["id"] == second_job
        assert len(c.get(f"/api/v1/sessions/{sid}/transcript").json()) == 2


def test_stereo_adapter_mixes_without_modifying_original(monkeypatch, tmp_path):
    from services.speech import transcriber
    source = tmp_path / "stereo.wav"
    original = wav_bytes(2)
    source.write_bytes(original)
    monkeypatch.setattr(Config, "identity", lambda self: {})
    def run(args, **kwargs):
        import subprocess
        with wave.open(args[args.index("-f") + 1], "rb") as wav:
            assert wav.getnchannels() == 1
        Path(args[args.index("-of") + 1]).with_suffix(".json").write_text(json.dumps({
            "transcription": [{"text": "Synthetic utterance", "offsets": {"from": 0, "to": 900}}]}))
        return subprocess.CompletedProcess(args, 0)
    # Decode uses subprocess too, so patch only after real decoding has finished.
    real_decode = transcriber.decode_audio
    def decode(*args):
        result = real_decode(*args)
        monkeypatch.setattr(transcriber.subprocess, "run", run)
        return result
    monkeypatch.setattr(transcriber, "decode_audio", decode)
    segments, metrics = transcriber.transcribe_with_metrics(source, session_id="test", config=Config("fake", "fake"))
    assert len(segments) == 1 and metrics["channel_mode"] == "stereo_mix"
    assert source.read_bytes() == original

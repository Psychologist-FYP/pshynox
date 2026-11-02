import hashlib
from pathlib import Path
import wave

import pytest
from services.retrieval.import_data import import_primock
from services.retrieval.prepare import STARTER
from services.retrieval.prepare_audio import boundaries, prepare_consultation
from services.speech.audio import inspect_audio


def test_actual_primock_clips_are_synchronized_bounded_and_preserve_all_sources(tmp_path):
    for name in ('day1_consultation01', 'day1_consultation02'):
        csv = STARTER / 'data/primock57' / f'{name}_transcript.csv'
        originals = [csv.parent / f'{name}_{role}.wav' for role in ('doctor', 'patient')]
        hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in originals]
        manifest, conversations = prepare_consultation(csv, tmp_path, 'test')
        assert len(conversations) == 2
        assert not manifest['excluded_source_ids']
        assert all(c['split'] == 'test' and c['parent_conversation_id'] == name for c in conversations)
        reference = {s.id: s for s in import_primock(csv)[name]}
        assert {s['id'] for c in conversations for s in c['segments']} == set(reference)
        for clip, conv in zip(manifest['clips'], conversations):
            frames = []
            for track in clip['tracks'].values():
                path = tmp_path / name / track['file']
                assert inspect_audio(path)['duration_ms'] <= 300000
                with wave.open(str(path)) as audio:
                    frames.append(audio.getnframes())
                assert track['source_offset_ms'] == clip['start_ms']
            assert frames[0] == frames[1]
            for segment in conv['segments']:
                original = reference[segment['id']]
                assert segment['start_ms'] + clip['start_ms'] == original.start_ms
                assert segment['end_ms'] + clip['start_ms'] == original.end_ms
                assert segment['text'] == original.text
        assert hashes == [hashlib.sha256(p.read_bytes()).hexdigest() for p in originals]
        with pytest.raises(ValueError, match='Output exists'):
            prepare_consultation(csv, tmp_path)


def test_unavoidable_boundary_exclusions_are_explicit(tmp_path):
    name = 'long'
    csv = tmp_path / f'{name}_transcript.csv'
    csv.write_text('segment_id,source_file,speaker,start_seconds,end_seconds,text\nlong-turn,long.TextGrid,patient,0,7,Long fictional utterance\n')
    for role in ('doctor', 'patient'):
        with wave.open(str(tmp_path / f'{name}_{role}.wav'), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000); audio.writeframes(b'\0\0' * 16000 * 8)
    manifest, conversations = prepare_consultation(csv, tmp_path / 'output', maximum_seconds=3)
    assert manifest['excluded_source_ids'] == ['long-turn']
    assert all('long-turn' in clip['excluded_boundary_source_ids'] for clip in manifest['clips'])
    assert not any(c['segments'] for c in conversations)

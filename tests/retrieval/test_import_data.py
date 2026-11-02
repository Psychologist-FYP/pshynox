from services.retrieval.import_data import import_annomi, import_primock


def test_annomi_import_excludes_annotation_and_diagnosis_columns(tmp_path):
    path = tmp_path / "annomi.csv"
    path.write_text("transcript_id,utterance_id,interlocutor,timestamp,utterance_text,client_talk_type,diagnosis\n"
                    "1,2,client,00:03,I cannot sleep.,sustain,DO_NOT_INDEX\n")
    segment = import_annomi(path)["annomi-1"][0]
    assert segment.text == "I cannot sleep."
    assert segment.id == "annomi-1-utterance-2"
    assert segment.start_ms == segment.end_ms == 3000
    assert segment.speaker_role == "CLIENT"
    assert "DO_NOT_INDEX" not in segment.model_dump_json()


def test_primock_import_keeps_shared_timeline_and_stable_interval_ids(tmp_path):
    path = tmp_path / "day1_consultation01_transcript.csv"
    path.write_text('start,end,speaker,text,source_filename,interval_id\n'
                    '1.5,2.5,doctor,"Sleeping less?",doctor.TextGrid,4\n'
                    '2.4,3.0,patient,"No.",patient.TextGrid,5\n')
    cid = "day1_consultation01"
    first = import_primock(path)[cid]
    assert first[0].start_ms == 1500 and first[0].end_ms == 2500
    assert first[1].start_ms == 2400
    assert first[1].text == "No." and first[1].speaker_role == "CLIENT"
    path.write_text(path.read_text().replace("No.", "No, not anymore."))
    assert [s.id for s in import_primock(path)[cid]] == [s.id for s in first]


import csv
from pathlib import Path
import pytest
from services.retrieval.import_data import research_payload, timestamp_ms

STARTER = Path(__file__).resolve().parents[2] / 'docs/Pshynox_Retrieval_Starter/data'


def test_actual_supplied_schemas_preserve_ids_text_roles_times_and_sources():
    for name, count in [('day1_consultation01', 109), ('day1_consultation02', 130)]:
        path = STARTER / 'primock57' / f'{name}_transcript.csv'
        with path.open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        turns = import_primock(path)[name]
        assert len(turns) == count
        assert [s.id for s in turns] == [r['segment_id'] for r in rows]
        assert [s.text for s in turns] == [r['text'] for r in rows]
        for turn, row in zip(turns, rows):
            assert turn.start_ms == timestamp_ms(row['start_seconds'])
            assert turn.end_ms == timestamp_ms(row['end_seconds'])
            assert turn.speaker_role == ('CLINICIAN' if row['speaker'] == 'doctor' else 'CLIENT')
        payload = research_payload('primock', path)
        assert payload['conversations'][0]['source_files'][turns[0].id] == rows[0]['source_file']
        assert payload['judgments'] == payload['queries'] == []
    annomi = import_annomi(STARTER / 'annomi/AnnoMI-simple.csv')
    assert len(annomi) == 133 and sum(map(len, annomi.values())) == 9699


@pytest.mark.parametrize('start,end', [('NaN', '2'), ('-1', '2'), ('3', '2'), ('1', 'Infinity'), ('00:99', '100')])
def test_invalid_supplied_timestamps_are_rejected(tmp_path, start, end):
    path = tmp_path / 'c_transcript.csv'
    path.write_text(f'segment_id,source_file,speaker,start_seconds,end_seconds,text\nx,c.TextGrid,patient,{start},{end},Original text\n')
    with pytest.raises(ValueError):
        import_primock(path)


def test_duplicate_and_missing_ids_are_rejected(tmp_path):
    path = tmp_path / 'c_transcript.csv'
    header = 'segment_id,source_file,speaker,start_seconds,end_seconds,text\n'
    path.write_text(header + 'x,c.TextGrid,patient,1,2,Original text\n' * 2)
    with pytest.raises(ValueError, match='Duplicate'):
        import_primock(path)
    path.write_text(header + ',c.TextGrid,patient,1,2,Original text\n')
    with pytest.raises(ValueError, match='segment_id'):
        import_primock(path)
    path.write_text(header + 'x,,patient,1,2,Original text\n')
    with pytest.raises(ValueError, match='source_file'):
        import_primock(path)


def test_empty_or_headerless_inputs_are_rejected_and_legacy_blank_intervals_are_skipped(tmp_path):
    path = tmp_path / 'c_transcript.csv'
    path.write_text('text\n')
    with pytest.raises(ValueError, match='required transcript column'):
        import_primock(path)
    path.write_text('start,end,speaker,text,source_filename,interval_id\n1,2,patient,,x.TextGrid,1\n2,3,patient,Original,x.TextGrid,2\n')
    assert len(import_primock(path)['c']) == 1
    payload = research_payload('primock', path)
    assert list(payload['conversations'][0]['source_files'].values()) == ['x.TextGrid']

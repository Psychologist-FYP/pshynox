"""All labels here belong to tiny fictional test fixtures in temporary directories."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from services.retrieval.prepare import digest, prepare, select_pilot, write_json
from services.retrieval.workspace import ResearchError, ResearchStore
from tests.local_data import needs_starter_pack


def create_research_fixture(root):
    conversations = []
    for cid, split in [('example', 'dev'), ('heldout', 'test')]:
        conversations.append({'conversation_id': cid, 'split': split, 'segments': [
            {'id': f'{cid}-{i}', 'session_id': cid, 'text': text, 'start_ms': i * 1000, 'end_ms': i * 1000 + 900,
             'speaker_role': role, 'revision': 1}
            for i, (text, role) in enumerate([('How much do you sleep?', 'CLINICIAN'), ('Four hours.', 'CLIENT')])]})
    dataset = {'dataset_id': 'synthetic', 'kind': 'fictional', 'description': 'Fictional test-only dataset', 'version': 'test-v1', 'source': {'fixture': True},
               'conversations': conversations, 'selection': {'pilot_ids': ['example', 'heldout'], 'groups': [['example'], ['heldout']], 'seed': 1, 'target_queries': 8, 'identity_limit': 'Fictional'}}
    write_json(root / 'datasets/synthetic.json', dataset)
    write_json(root / 'annotations/synthetic.json', {'revision': 0, 'dataset_version': 'test-v1', 'pilot_extra': [], 'explored': [], 'queries': []})
    return ResearchStore(root)


def add_query(store, cid='example', text='sleep'):
    return store.save_query('synthetic', cid, {'revision': store.state('synthetic')['revision'], 'query_id': None,
                            'text': text, 'intent': 'Find fictional sleep evidence', 'draft': False, 'no_answer_confirmed': False})


def grade_all(store, detail, grades):
    for segment, grade in zip(detail['conversation']['segments'], grades):
        detail = store.grade('synthetic', detail['conversation']['conversation_id'], detail['queries'][0]['query_id'], segment['id'],
                             {'revision': detail['revision'], 'grade': grade, 'reviewer': 'fictional test author', 'rationale': 'Test fixture expectation'})
    return detail


def test_unreviewed_zero_persistence_and_complete_export(tmp_path):
    store = create_research_fixture(tmp_path)
    detail = add_query(store)
    assert detail['queries'][0]['progress']['reviewed'] == 0
    with pytest.raises(ResearchError, match='Export blocked'):
        store.export('synthetic', 'dev')
    query = detail['queries'][0]
    detail = store.grade('synthetic', 'example', query['query_id'], 'example-0',
                         {'revision': detail['revision'], 'grade': 0, 'reviewer': 'test', 'rationale': 'Fictional irrelevant turn'})
    restored = ResearchStore(tmp_path).detail('synthetic', 'example')
    assert restored['queries'][0]['judgments']['example-0']['grade'] == 0
    assert 'example-1' not in restored['queries'][0]['judgments']
    assert restored['queries'][0]['progress']['reviewed'] == 1
    detail = store.grade('synthetic', 'example', query['query_id'], 'example-1',
                         {'revision': detail['revision'], 'grade': 2, 'reviewer': 'test', 'rationale': 'Fictional direct reply'})
    exported = store.export('synthetic', 'dev')
    assert len(exported['judgments']) == 2 and exported['queries'][0]['intent']
    assert all(c['split'] == 'dev' for c in exported['conversations'])
    assert exported['annotation_revision'] == detail['revision']
    store.grade('synthetic', 'example', query['query_id'], 'example-0',
                {'revision': detail['revision'], 'grade': None, 'reviewer': '', 'rationale': ''})
    with pytest.raises(ResearchError, match='Export blocked'):
        store.export('synthetic', 'dev')


def test_no_answer_requires_full_review_confirmation_and_approved_query(tmp_path):
    store = create_research_fixture(tmp_path)
    detail = add_query(store, text='scuba diving')
    qid = detail['queries'][0]['query_id']
    payload = {'query_id': qid, 'text': 'scuba diving', 'intent': 'Find fictional sleep evidence',
               'draft': False, 'no_answer_confirmed': True, 'reviewer': 'test'}
    with pytest.raises(ResearchError, match='every turn'):
        store.save_query('synthetic', 'example', {**payload, 'revision': detail['revision']})
    detail = grade_all(store, detail, [0, 0])
    assert detail['queries'][0]['progress']['needs_no_answer_confirmation']
    with pytest.raises(ResearchError, match='Export blocked'):
        store.export('synthetic', 'dev')
    store.save_query('synthetic', 'example', {**payload, 'revision': detail['revision']})
    assert store.export('synthetic', 'dev')['queries'][0]['no_answer_confirmed']


def test_query_edits_clear_judgments_and_stale_tabs_cannot_overwrite(tmp_path):
    store = create_research_fixture(tmp_path)
    detail = grade_all(store, add_query(store), [0, 2])
    qid = detail['queries'][0]['query_id']
    with pytest.raises(ResearchError, match='another tab'):
        store.grade('synthetic', 'example', qid, 'example-0', {'revision': 0, 'grade': 1, 'reviewer': 'test', 'rationale': 'test'})
    result = store.save_query('synthetic', 'example', {'query_id': qid, 'revision': detail['revision'], 'text': 'different query',
                                                     'intent': 'Different intended evidence', 'draft': True})
    assert result['queries'][0]['judgments'] == {}
    assert not result['queries'][0]['progress']['complete']


def test_identity_and_source_groups_cannot_cross_splits_and_selection_is_reproducible():
    conversations = [{'conversation_id': str(i), 'participant_ids': ['shared'] if i < 2 else []} for i in range(12)]
    conversations[2]['source_video'] = conversations[3]['source_video'] = 'same-video'
    a = select_pilot(conversations, 33, 10)
    assert a == select_pilot(deepcopy(conversations), 33, 10)
    assert a['splits']['0'] == a['splits']['1']
    assert a['splits']['2'] == a['splits']['3']
    assert any(set(g) == {'0', '1'} for g in a['groups'])


@needs_starter_pack
def test_preparation_verifies_actual_pack_preserves_progress_and_separates_data(tmp_path):
    prepare(output=tmp_path)
    store = ResearchStore(tmp_path)
    before = store.state('annomi')
    assert len(store.dataset('annomi')['conversations']) == 133
    assert len(before['queries']) == 40
    assert all(q['draft'] and not q['judgments'] for q in before['queries'])
    assert len(store.dataset('primock')['conversations']) == 2
    prepare(output=tmp_path)
    assert store.state('annomi') == before
    assert not list(tmp_path.rglob('*.db'))
    assert len(store.dataset('annomi')['verification']['files']) == 17


def test_exploration_is_logged_and_test_search_is_blocked(tmp_path):
    store = create_research_fixture(tmp_path)
    with pytest.raises(ResearchError, match='held-out'):
        store.mark_explored('synthetic', 'heldout', 0)
    store.mark_explored('synthetic', 'example', 0)
    assert store.state('synthetic')['explored'] == ['example']


def test_missing_or_corrupt_manifest_inputs_are_rejected_without_download(tmp_path):
    from services.retrieval.prepare import verify_pack
    path = tmp_path / 'source.csv'
    path.write_text('original')
    write_json(tmp_path / 'manifest.json', {'files': [{'path': 'source.csv', 'bytes': 8, 'sha256': 'wrong'}]})
    with pytest.raises(ValueError, match='Missing/corrupt'):
        verify_pack(tmp_path)
    path.unlink()
    with pytest.raises(ValueError, match='Missing/corrupt'):
        verify_pack(tmp_path)


def test_shared_conversion_preserves_all_progress_and_keeps_legacy_backup(tmp_path):
    store = create_research_fixture(tmp_path)
    grade_all(store, add_query(store), [0, 2])
    store.mark_explored('synthetic', 'example', store.state('synthetic')['revision'])
    before = store.state('synthetic')
    backup = (tmp_path / 'annotations/synthetic.json').read_bytes()
    shared = store.share_annotations('synthetic')
    assert {k: v for k, v in shared.items() if k != 'revision'} == {k: v for k, v in before.items() if k != 'revision'}
    assert shared['revision'] < 2 ** 53
    assert store.export('synthetic', 'dev')['judgments'][0]['grade'] == 0
    assert (tmp_path / 'annotations/synthetic.json').read_bytes() == backup
    assert store.share_annotations('synthetic') == shared


def test_separate_conversation_edits_combine_after_pull_without_touching_other_files(tmp_path):
    root = tmp_path / 'first'
    store = create_research_fixture(root)
    first = grade_all(store, add_query(store), [0, 2])
    grade_all(store, add_query(store, cid='heldout'), [1, 2])
    store.share_annotations('synthetic')
    other_root = tmp_path / 'second'
    shutil.copytree(root, other_root)
    other = ResearchStore(other_root)
    stale_revision = other.state('synthetic')['revision']
    heldout_path = root / 'annotations/synthetic/heldout.json'
    untouched = heldout_path.read_bytes()
    store.grade('synthetic', 'example', first['queries'][0]['query_id'], 'example-0',
                {'revision': store.state('synthetic')['revision'], 'grade': None, 'reviewer': '', 'rationale': ''})
    assert heldout_path.read_bytes() == untouched
    heldout = other.detail('synthetic', 'heldout')
    other.grade('synthetic', 'heldout', heldout['queries'][0]['query_id'], 'heldout-0',
                {'revision': stale_revision, 'grade': 2, 'reviewer': 'second author', 'rationale': 'Fictional evidence'})
    # A Git pull combines independently changed conversation files.
    shutil.copyfile(root / 'annotations/synthetic/example.json', other_root / 'annotations/synthetic/example.json')
    assert 'example-0' not in other.detail('synthetic', 'example')['queries'][0]['judgments']
    assert other.export('synthetic', 'test')['judgments'][0]['reviewer'] == 'second author'
    with pytest.raises(ResearchError, match='another tab'):
        other.grade('synthetic', 'example', first['queries'][0]['query_id'], 'example-0',
                    {'revision': stale_revision, 'grade': 0, 'reviewer': 'test', 'rationale': 'test'})
    other.save_query('synthetic', 'heldout', {'revision': other.state('synthetic')['revision'],
                     'query_id': heldout['queries'][0]['query_id'], 'text': 'changed question', 'intent': 'New intent', 'draft': True})
    assert ResearchStore(other_root).detail('synthetic', 'heldout')['queries'][0]['judgments'] == {}


def test_shared_annotations_reject_dataset_version_mismatch(tmp_path):
    store = create_research_fixture(tmp_path)
    add_query(store)
    store.share_annotations('synthetic')
    path = tmp_path / 'datasets/synthetic.json'
    dataset = json.loads(path.read_text())
    dataset['version'] = 'different-source-version'
    write_json(path, dataset)
    with pytest.raises(ResearchError, match='Dataset changed'):
        store.state('synthetic')

import json
from pathlib import Path
import shutil

import pytest
from services.retrieval.workspace import ResearchStore, ResearchError
from services.retrieval.ai_suggestions import suggestions, review


def test_ai_review_preserves_originals_and_requires_explicit_human_approval(tmp_path):
    source = Path('data/research')
    for relative in ['datasets/annomi.json', 'annotations/annomi/annomi-24.json', 'ai-suggestions/annomi/annomi-24.json']:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
    # Start the temporary fixture before AI acceptance, regardless of live review progress.
    state_path = tmp_path / 'annotations/annomi/annomi-24.json'
    fixture_state = json.loads(state_path.read_text())
    fixture_state['queries'] = [q for q in fixture_state['queries'] if not q['query_id'].startswith('ai-')]
    state_path.write_text(json.dumps(fixture_state))
    store = ResearchStore(tmp_path)
    before = store.state('annomi')['queries']
    ai_path = tmp_path / 'ai-suggestions/annomi/annomi-24.json'
    original = ai_path.read_bytes()
    draft = suggestions(store, 'annomi', 'annomi-24')['queries'][0]
    pair = draft['suggestions'][1]
    payload = {'revision': store.state('annomi')['revision'], 'grade': 1, 'rationale': 'Human correction of the evidence boundary', 'reviewer': 'test human'}
    detail = review(store, 'annomi', 'annomi-24', draft['query_id'], pair['source_segment_id'], payload)
    for old in before:
        assert next(q for q in store.state('annomi')['queries'] if q['query_id'] == old['query_id']) == old
    query = next(q for q in detail['queries'] if q['query_id'] == draft['query_id'])
    assert query['draft'] and not query['no_answer_confirmed']
    assert query['judgments'][pair['source_segment_id']]['review_action'] == 'edited'
    assert query['judgments'][pair['source_segment_id']]['annotation_origin'] == 'human'
    assert ai_path.read_bytes() == original
    with pytest.raises(ResearchError, match='already exists'):
        review(store, 'annomi', 'annomi-24', draft['query_id'], pair['source_segment_id'], {**payload, 'revision': detail['revision']})
    with pytest.raises(ResearchError, match='Export blocked'):
        store.export('annomi', 'dev')
    corrupted = json.loads(original)
    corrupted['queries'][0]['suggestions'].pop()
    ai_path.write_text(json.dumps(corrupted))
    with pytest.raises(ResearchError, match='every turn exactly once'):
        suggestions(store, 'annomi', 'annomi-24')


def test_full_pilot_exploratory_exports_do_not_approve_or_modify_annotations(tmp_path):
    from services.retrieval.ai_evaluation import export, LABEL
    root = Path('data/research')
    before = {p: p.read_bytes() for p in (root / 'annotations').rglob('*.json')}
    store = ResearchStore(root)
    for split, conversations, queries, labels in [('dev', 7, 28, 1004), ('test', 3, 12, 1412)]:
        result = export(store, split, tmp_path / f'{split}.json')
        assert result['description'] == LABEL and result['kind'] == 'ai-exploratory'
        assert len(result['conversations']) == conversations
        assert len(result['queries']) == queries and len(result['judgments']) == labels
        assert all(c['split'] == split for c in result['conversations'])
        assert all(q['draft'] and not q['no_answer_confirmed'] for q in result['queries'])
        assert all(j['annotation_origin'] == 'ai' and j['human_review_status'] == 'pending' for j in result['judgments'])
    assert all(p.read_bytes() == value for p, value in before.items())

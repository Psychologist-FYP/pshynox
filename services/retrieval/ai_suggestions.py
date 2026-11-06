"""AI drafts are a separate contribution, never benchmark-ready human labels."""
import json
from services.retrieval.prepare import digest
from services.retrieval.workspace import ResearchError, identifier

POLICY = 'retrieval-ai-v1'

def suggestions(store, name, cid):
    path = store.root / 'ai-suggestions' / identifier(name) / f'{identifier(cid)}.json'
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    dataset, conv = store.dataset(name), store.conversation(name, cid)
    if name != 'annomi' or cid not in dataset['selection']['pilot_ids']:
        raise ResearchError('AI suggestions are restricted to the AnnoMI pilot', 409)
    if (value['dataset_version'] != dataset['version'] or value['dataset_hash'] != digest(dataset)
            or value['transcript_hash'] != digest(conv) or value['annotation_policy_version'] != POLICY):
        raise ResearchError('AI suggestions are stale for this transcript or policy', 409)
    if (value['conversation_id'] != cid or value['dataset_id'] != name or value['split'] != conv['split']
            or value['annotation_origin'] != 'ai' or value['human_review_status'] != 'pending'):
        raise ResearchError('Invalid AI contribution provenance', 409)
    ids = {s['id'] for s in conv['segments']}
    seen = set()
    for q in value['queries']:
        if (q['query_id'] in seen or q['conversation_id'] != cid or not q['draft']
                or q['no_answer_confirmed'] or q['annotation_origin'] != 'ai'
                or q['human_review_status'] != 'pending'):
            raise ResearchError('Invalid AI query reference', 409)
        seen.add(q['query_id'])
        revision = digest({'text': q['text'], 'intent': q['intent']})
        if q['query_revision'] != revision:
            raise ResearchError('Invalid AI query revision', 409)
        pairs = q['suggestions']
        if len(pairs) != len(ids) or {p['source_segment_id'] for p in pairs} != ids:
            raise ResearchError('AI suggestions must cover every turn exactly once', 409)
        for p in pairs:
            if (p['query_id'] != q['query_id'] or p['query_revision'] != revision
                    or type(p['suggested_grade']) is not int or p['suggested_grade'] not in (0, 1, 2)
                    or not p['rationale'].strip() or not set(p['context_turn_ids']) <= ids
                    or p['annotation_origin'] != 'ai' or p['human_review_status'] != 'pending'):
                raise ResearchError('Invalid AI turn suggestion', 409)
        if q['kind'] == 'no-answer' and any(p['suggested_grade'] for p in pairs):
            raise ResearchError('Inconsistent AI no-answer candidate; review required', 409)
    return value


def review(store, name, cid, qid, sid, payload):
    from datetime import datetime, timezone
    value = suggestions(store, name, cid)
    q = next((q for q in (value or {}).get('queries', []) if q['query_id'] == qid), None)
    p = next((p for p in (q or {}).get('suggestions', []) if p['source_segment_id'] == sid), None)
    if not p or not payload['reviewer'].strip() or not payload['rationale'].strip():
        raise ResearchError('A valid suggestion, human reviewer and rationale are required')
    if type(payload['grade']) is not int or payload['grade'] not in (0, 1, 2):
        raise ResearchError('Human grade must be 0, 1 or 2')
    with store.edit(name, payload['revision']) as state:
        query = next((v for v in state['queries'] if v['query_id'] == qid), None)
        if query is None:
            query = {k: q[k] for k in ('query_id', 'conversation_id', 'text', 'intent', 'kind')}
            query.update(draft=True, no_answer_confirmed=False, judgments={}, annotation_origin='ai', human_review_status='pending')
            state['queries'].append(query)
        if query['text'] != q['text'] or query['intent'] != q['intent']:
            raise ResearchError('Query changed: AI suggestions require re-annotation', 409)
        if sid in query['judgments']:
            raise ResearchError('Human judgment already exists; use the human annotation editor to revise it', 409)
        action = 'accepted' if (payload['grade'], payload['rationale']) == (p['suggested_grade'], p['rationale']) else 'edited'
        query['judgments'][sid] = {'grade': payload['grade'], 'rationale': payload['rationale'], 'reviewer': payload['reviewer'],
            'at': datetime.now(timezone.utc).isoformat(), 'annotation_origin': 'human', 'human_review_status': 'reviewed',
            'review_action': action, 'ai_reference': {'query_id': qid, 'source_segment_id': sid, 'query_revision': q['query_revision'],
            'transcript_hash': value['transcript_hash'], 'annotation_policy_version': POLICY}}
        query['no_answer_confirmed'] = False
        query.pop('no_answer_review', None)
    return store.detail(name, cid)

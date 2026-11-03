"""Record explicitly authorized sampled bulk acceptance without inventing per-item inspection."""
from datetime import datetime, timezone
from copy import deepcopy
from services.retrieval.ai_suggestions import suggestions
from services.retrieval.prepare import write_json


def accept(store, reviewer):
    now = datetime.now(timezone.utc).isoformat()
    added = preserved = 0
    dataset = store.dataset('annomi')
    for cid in dataset['selection']['pilot_ids']:
        source = suggestions(store, 'annomi', cid)
        with store.edit('annomi', store.state('annomi')['revision']) as state:
            for draft in source['queries']:
                query = next((q for q in state['queries'] if q['query_id'] == draft['query_id']), None)
                if query is None:
                    query = {k: deepcopy(v) for k,v in draft.items() if k != 'suggestions'}
                    query['judgments'] = {}
                    state['queries'].append(query)
                if (query['text'], query['intent']) != (draft['text'], draft['intent']):
                    raise ValueError('Existing query edited; requires individual review')
                query.update(draft=False, human_review_status='reviewed', query_review={
                    'reviewer': reviewer, 'at': now, 'action': 'bulk-accepted-wording',
                    'review_basis': 'User explicitly authorized bulk acceptance after inspecting a sample; not individual inspection of every label'})
                for pair in draft['suggestions']:
                    sid = pair['source_segment_id']
                    if sid in query['judgments']:
                        preserved += 1
                        continue
                    query['judgments'][sid] = {'grade': pair['suggested_grade'], 'rationale': pair['rationale'],
                        'reviewer': reviewer, 'at': now, 'annotation_origin': 'ai', 'human_review_status': 'reviewed',
                        'review_action': 'bulk-accepted', 'review_basis': 'User-approved sample-based bulk acceptance',
                        'ai_reference': {'query_id': draft['query_id'], 'source_segment_id': sid,
                                         'query_revision': draft['query_revision'], 'transcript_hash': source['transcript_hash']}}
                    added += 1
                # Bulk approval based on a sample does not assert full-transcript human verification.
                # Preserve any existing explicit no-answer confirmation, otherwise leave it pending.
    result = {'reviewer': reviewer, 'new_bulk_accepted_grades': added, 'existing_grades_preserved': preserved,
              'review_basis': 'sample-based bulk acceptance explicitly requested by user',
              'no_answer_confirmation': 'Existing confirmations preserved; no full-conversation inspection invented', 'at': now}
    write_json(store.root / 'review-experiments/bulk-acceptance.json', result)
    return result

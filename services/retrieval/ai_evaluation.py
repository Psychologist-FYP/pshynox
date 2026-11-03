"""Export AI-only pilot labels for explicitly exploratory benchmarks.

Does not approve queries, confirm human no-answer reviews or alter human labels.
"""
import argparse
from copy import deepcopy
from pathlib import Path

from services.retrieval.ai_suggestions import suggestions
from services.retrieval.benchmark import load_dataset
from services.retrieval.prepare import digest, write_json
from services.retrieval.workspace import ResearchStore

LABEL = 'AI-labelled exploratory evaluation'


def export(store, split, path):
    dataset = store.dataset('annomi')
    conversations = [c for c in dataset['conversations'] if c['conversation_id'] in dataset['selection']['pilot_ids'] and c['split'] == split]
    payload = {'description': LABEL, 'annotation_status': LABEL + '; human review pending; not human-validated or clinical accuracy',
               'kind': 'ai-exploratory', 'dataset_id': 'annomi-ai-pilot', 'dataset_version': dataset['version'],
               'source_dataset_hash': digest(dataset), 'source': dataset['source'], 'split': split,
               'conversations': deepcopy(conversations), 'queries': [], 'judgments': [], 'ai_contributions': []}
    for c in conversations:
        value = suggestions(store, 'annomi', c['conversation_id'])
        if not value:
            raise ValueError(f"Missing AI checkpoint for {c['conversation_id']}")
        payload['ai_contributions'].append({k: v for k, v in value.items() if k != 'queries'})
        for q in value['queries']:
            payload['queries'].append({k: v for k, v in q.items() if k != 'suggestions'})
            for p in q['suggestions']:
                payload['judgments'].append({**p, 'conversation_id': c['conversation_id'],
                                             'grade': p['suggested_grade'], 'reviewer': 'AI suggestion; no human review'})
    write_json(path, payload)
    load_dataset(path)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('data/research/ai-exploratory-evaluation'))
    args = parser.parse_args()
    for split in ('dev', 'test'):
        path = args.output_dir / f'{split}-dataset.json'
        value = export(ResearchStore(), split, path)
        print(f"{LABEL}: {split}: {len(value['conversations'])} conversations, {len(value['queries'])} queries, {len(value['judgments'])} labels -> {path}")


if __name__ == '__main__':
    main()

"""Run the frozen independent final evaluation after receiving completed human labels."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from services.retrieval.benchmark import load_dataset
from services.retrieval.prepare import digest,write_json


def validate(labels_path,packet_path,config_path):
    packet=json.loads(packet_path.read_text());config=json.loads(config_path.read_text())
    if digest(config)!=packet['frozen_config_sha256']:
        raise ValueError('Frozen configuration changed after packet preparation')
    if hashlib.sha256(Path('services/retrieval/engine.py').read_bytes()).hexdigest()!=config['ranker_sha256']:
        raise ValueError('Retrieval implementation changed after configuration freeze')
    raw = json.loads(labels_path.read_text())
    data,conversations,queries,grades=load_dataset(labels_path)
    if data.get('frozen_config_sha256')!=packet['frozen_config_sha256'] or data.get('dataset_version')!=packet['dataset_version']:
        raise ValueError('Labels do not match the frozen packet')
    if digest(raw['conversations'])!=digest(packet['conversations']):
        raise ValueError('Final transcripts, IDs or splits differ from the blind packet')
    for cid in conversations:
        qs=[q for q in queries.values() if q['conversation_id']==cid]
        if len(qs)<3:raise ValueError('Write at least three independent human questions per conversation')
        for q in qs:
            if q.get('annotation_origin')!='human' or q.get('draft',True) or not q.get('intent','').strip():
                raise ValueError('Final questions require explicit individual human authorship and approval')
            if not any(grades[q['query_id']].values()) and not q.get('no_answer_confirmed'):
                raise ValueError('No-answer questions require human full-conversation confirmation')
    for j in data['judgments']:
        if j.get('annotation_origin')!='human' or j.get('review_action')!='individually-labelled-blind':
            raise ValueError('Final labels must be individually labelled blind by a human')
    return config


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--labels',type=Path,required=True)
    args=parser.parse_args();folder=Path('data/research/review-experiments/independent-final');config_path=folder.parent/'frozen-method-config.json'
    config=validate(args.labels,folder/'packet.json',config_path)
    output=folder/'final-results.json'
    if output.exists():parser.error('Final report already exists; do not iterate or tune on final-set results')
    write_json(folder/'evaluation-started.json',{'labels_sha256':hashlib.sha256(args.labels.read_bytes()).hexdigest(),'frozen_config_sha256':digest(config),'started_at':datetime.now(timezone.utc).isoformat()})
    subprocess.run([sys.executable,'-m','services.retrieval.benchmark','--dataset',str(args.labels),'--split','test','--methods',*config['methods'],'--config',str(config_path),'--require-all','--output',str(output)],check=True)


if __name__=='__main__':main()

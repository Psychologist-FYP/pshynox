"""Prepare an unused, blind final-set packet; never author its questions or labels."""
import json
import random
from pathlib import Path
from services.retrieval.prepare import digest, write_json
from services.retrieval.workspace import ResearchStore


def prepare(store, config_path, output):
    config = json.loads(config_path.read_text())
    dataset, state = store.dataset('annomi'), store.state('annomi')
    used = set(dataset['selection']['pilot_ids']) | set(state['explored']) | {q['conversation_id'] for q in state['queries']}
    groups = dataset['selection']['groups']
    forbidden = set(used)
    for group in groups:
        if set(group) & used:
            forbidden.update(group)
    candidates = [c for c in dataset['conversations'] if c['conversation_id'] not in forbidden and c['split']=='test' and 10<=len(c['segments'])<=70]
    random.Random(20261103).shuffle(candidates)
    chosen=[]
    for conv in candidates:
        if conv['conversation_id'] in forbidden:
            continue
        chosen.append(conv)
        for group in groups:
            if conv['conversation_id'] in group: forbidden.update(group)
        if len(chosen)==3:break
    if len(chosen)!=3:raise ValueError('Not enough unused test source groups')
    packet={'description':'Independent blind human-labelled final set; labels pending', 'dataset_id':'annomi-independent-final',
      'dataset_version':dataset['version'],'dataset_hash':digest(dataset),'source':dataset['source'],
      'annotation_status':'awaiting independent blind human questions and grades','kind':'dataset',
      'frozen_config_sha256':digest(config),'frozen_config':config,'conversations':chosen,'queries':[],'judgments':[],
      'selection':{'seed':20261103,'excluded_used_conversations':sorted(used),'source_groups_disjoint':True,
       'identity_limit':dataset['selection']['identity_limit']}}
    output.mkdir(parents=True,exist_ok=True);write_json(output/'packet.json',packet)
    template=Path(__file__).with_name('blind_final.html').read_text()
    (output/'review.html').write_text(template.replace('__PACKET__',json.dumps(packet,ensure_ascii=False).replace('<','\\u003c')))
    print('Blind final packet:',[(c['conversation_id'],len(c['segments'])) for c in chosen])
    return packet


if __name__=='__main__':
    root=Path('data/research/review-experiments')
    prepare(ResearchStore(),root/'frozen-method-config.json',root/'independent-final')

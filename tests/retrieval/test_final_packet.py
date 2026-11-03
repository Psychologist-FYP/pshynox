from pathlib import Path
from services.retrieval.final_packet import prepare
from services.retrieval.prepare import digest
from services.retrieval.workspace import ResearchStore


def test_final_packet_is_blind_disjoint_and_bound_to_frozen_config(tmp_path):
    root = Path('data/research')
    store = ResearchStore(root)
    packet = prepare(store,root/'review-experiments/frozen-method-config.json',tmp_path)
    selected = {c['conversation_id'] for c in packet['conversations']}
    used = set(packet['selection']['excluded_used_conversations'])
    assert len(selected)==3 and not selected & used
    assert not packet['queries'] and not packet['judgments']
    assert packet['frozen_config_sha256']==digest(packet['frozen_config'])
    for group in store.dataset('annomi')['selection']['groups']:
        assert not (set(group) & selected and set(group) & used)
    assert all(c['split']=='test' for c in packet['conversations'])


def test_final_runner_rejects_packet_without_human_questions_and_labels():
    import pytest
    from services.retrieval.run_final import validate
    root=Path('data/research/review-experiments')
    with pytest.raises(ValueError,match='three independent human questions'):
        validate(root/'independent-final/packet.json',root/'independent-final/packet.json',root/'frozen-method-config.json')

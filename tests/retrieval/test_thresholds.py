import pytest
from services.retrieval.thresholds import choose,remeasure


def test_thresholds_respect_method_scale_and_preserve_direct_answers():
    grades={'answer':{'a':2,'b':0},'none':{'c':0}}
    def rows(scale):
        return [{'query_id':'answer','ranked_source_ids':['a','b'],'scores':[.9*scale,.4*scale]},
                {'query_id':'none','ranked_source_ids':['c'],'scores':[.6*scale]}]
    a,_=choose(rows(1),grades,'dev');b,_=choose(rows(10),grades,'dev')
    assert a['threshold'] != b['threshold']
    assert a['metrics']['direct_evidence_hit_at_5']==1 and a['metrics']['abstention_rate_no_answer']==1
    assert remeasure(rows(1),grades,a['threshold'])[1]['ranked_source_ids']==[]
    with pytest.raises(ValueError,match='development-only'):
        choose(rows(1),grades,'test')

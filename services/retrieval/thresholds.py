"""Development-only threshold selection over saved rankings, preserving label provenance."""
from copy import deepcopy
import math
from services.retrieval.benchmark import quality_metrics, summarize


def remeasure(rows, grades, threshold=None):
    result = []
    for original in rows:
        row = deepcopy(original)
        keep = [(sid, score) for sid, score in zip(row['ranked_source_ids'], row['scores']) if threshold is None or score >= threshold]
        row['ranked_source_ids'] = [sid for sid, _ in keep]
        row['scores'] = [score for _, score in keep]
        row['metrics'] = quality_metrics(row['ranked_source_ids'], grades[row['query_id']])
        result.append(row)
    return result


def choose(rows, grades, split):
    if split != 'dev':
        raise ValueError('Threshold selection is development-only')
    scores = sorted({score for row in rows for score in row['scores']})
    if not scores:
        raise ValueError('No scored rankings')
    candidates = [None] + [scores[round(i * (len(scores)-1)/min(60,len(scores)-1))] for i in range(min(60,len(scores)-1)+1)] if len(scores)>1 else [None,scores[0]]
    candidates.append(math.nextafter(scores[-1], math.inf))
    curve=[]
    for threshold in candidates:
        metrics = summarize(remeasure(rows, grades, threshold))
        if metrics['abstention_rate_no_answer'] is None or metrics['direct_evidence_hit_at_5'] is None:
            raise ValueError('Selection needs both no-answer and direct-answer development examples')
        objective = .5 * metrics['direct_evidence_hit_at_5'] + .5 * metrics['abstention_rate_no_answer']
        curve.append({'threshold':threshold,'objective':objective,'metrics':metrics})
    # Fixed selection rule: equal weight on direct-answer coverage and no-answer
    # abstention; ties retain more direct answers and then more direct evidence.
    minimum_hit = .8 * curve[0]['metrics']['direct_evidence_hit_at_5']
    eligible = [candidate for candidate in curve if candidate['metrics']['direct_evidence_hit_at_5'] >= minimum_hit]
    best=max(eligible,key=lambda c:(c['objective'],c['metrics']['direct_evidence_hit_at_5'],c['metrics']['direct_evidence_recall_at_10']))
    return best,curve

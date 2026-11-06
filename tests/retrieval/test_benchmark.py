import json

import pytest

from services.retrieval.benchmark import load_dataset, quality_metrics, summarize, word_error_counts


def test_metrics_deduplicate_and_do_not_use_context_as_new_evidence():
    metrics = quality_metrics(["a", "a", "c", "b"], {"a": 2, "b": 1, "c": 0})
    assert metrics["recall_at_5"] == 1
    assert metrics["precision_at_5"] == .4
    assert metrics["mrr_at_10"] == 1
    assert 0 < metrics["ndcg_at_10"] < 1
    assert quality_metrics(["a"], {"a": 0})["false_positive_return"]
    assert quality_metrics([], {"a": 0})["abstained"]
    summary = summarize([{"metrics": metrics}, {"metrics": quality_metrics([], {"a": 0})}])
    assert summary["answerable_queries"] == 1 and summary["no_answer_queries"] == 1
    assert summary["abstention_rate_no_answer"] == 1


def test_word_error_counts_include_insertions_negations_and_numbers():
    assert word_error_counts("I do not sleep four hours", "I do sleep five hours") == (2, 6)


def test_unjudged_turns_and_split_leakage_are_rejected(tmp_path):
    segment = {"id": "one", "session_id": "c", "start_ms": 0, "end_ms": 1,
               "speaker_role": "CLIENT", "revision": 1, "text": "sleep"}
    dataset = {"conversations": [{"conversation_id": "c", "split": "test", "segments": [segment]}],
               "queries": [{"query_id": "q", "conversation_id": "c", "text": "sleep"}], "judgments": []}
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="judge every source"):
        load_dataset(path)
    dataset["judgments"] = [{"query_id": "q", "conversation_id": "c", "source_segment_id": "one", "grade": 2,
                             "reviewer": "test-author", "rationale": "Fictional matching passage"}]
    path.write_text(json.dumps(dataset))
    load_dataset(path)
    dataset["conversations"][0]["participant_ids"] = ["same-person"]
    dataset["conversations"].append({"conversation_id": "d", "participant_ids": ["same-person"], "split": "dev",
                                      "segments": [{**segment, "session_id": "d"}]})
    path.write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match="identity crosses"):
        load_dataset(path)


def test_failed_methods_are_recorded_without_discarding_report(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from services.retrieval import benchmark
    fixture = __import__('pathlib').Path(__file__).parent / 'fixtures/engineering.json'
    output = tmp_path / 'report.json'
    monkeypatch.setattr(sys, 'argv', ['benchmark', '--dataset', str(fixture), '--output', str(output), '--methods', 'bm25', '--require-all'])
    monkeypatch.setattr(benchmark.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stderr='Fictional worker failure', stdout=''))
    with pytest.raises(SystemExit) as exc:
        benchmark.main()
    assert exc.value.code == 2
    report = json.loads(output.read_text())
    assert report['results'][0]['status'] == 'failed'
    assert 'quality' not in report['results'][0]
    assert report['result_kind'] == 'fictional'
    assert report['evaluation_dataset']['queries']


def test_cli_rejects_threshold_tuning_on_test(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from services.retrieval import benchmark
    fixture = Path(__file__).parent / 'fixtures/engineering.json'
    monkeypatch.setattr(sys, 'argv', ['benchmark', '--dataset', str(fixture), '--output', str(tmp_path / 'report.json'), '--methods', 'bm25', '--min-score', '.8'])
    with pytest.raises(SystemExit) as exc:
        benchmark.main()
    assert exc.value.code == 2
    assert not (tmp_path / 'report.json').exists()


def test_clips_from_one_consultation_cannot_cross_splits(tmp_path):
    from pathlib import Path
    dataset = json.loads((Path(__file__).parent / 'fixtures/engineering.json').read_text())
    conv = dataset['conversations'][0]
    conv['parent_conversation_id'] = 'same-consultation'
    dataset['conversations'].append({'conversation_id': 'other-clip', 'split': 'dev', 'parent_conversation_id': 'same-consultation',
                                    'segments': [{**conv['segments'][0], 'id': 'other-turn', 'session_id': 'other-clip'}]})
    path = tmp_path / 'clips.json'; path.write_text(json.dumps(dataset))
    with pytest.raises(ValueError, match='one consultation'):
        load_dataset(path)


def test_direct_evidence_metrics_distinguish_context_and_deduplicate():
    grades = {'direct-a': 2, 'direct-b': 2, 'context': 1, 'zero': 0}
    context_only = quality_metrics(['context', 'zero'], grades)
    assert context_only['mrr_at_10'] == 1
    assert context_only['direct_evidence_hit_at_5'] is False
    assert context_only['direct_evidence_recall_at_10'] == 0
    result = quality_metrics(['direct-a', 'direct-a', 'context'], grades)
    assert result['direct_evidence_hit_at_5'] is True
    assert result['direct_evidence_recall_at_5'] == .5
    only_partial = quality_metrics(['context'], {'context': 1})
    assert only_partial['direct_evidence_hit_at_5'] is None
    summary = summarize([{'metrics': result}, {'metrics': only_partial}, {'metrics': quality_metrics([], {'zero': 0})}])
    assert summary['queries_with_direct_evidence'] == 1
    assert summary['direct_evidence_hit_at_5'] == 1
    assert summary['direct_evidence_recall_at_10'] == .5


def test_frozen_per_method_thresholds_reach_correct_worker(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace
    from services.retrieval import benchmark
    fixture = Path(__file__).parent / 'fixtures/engineering.json'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'methods': ['bm25','e5'], 'settings': {},
        'calibration': {'split':'dev','dataset_sha256':'example'}, 'per_method_thresholds':{'bm25':2.5,'e5':.75}}))
    seen={}
    def run(command, **kwargs):
        seen[command[command.index('--worker')+1]]=json.loads(command[command.index('--settings')+1])['min_score']
        return SimpleNamespace(returncode=0,stdout=json.dumps({'status':'measured'}),stderr='')
    monkeypatch.setattr(benchmark.subprocess,'run',run)
    monkeypatch.setattr(sys,'argv',['benchmark','--dataset',str(fixture),'--methods','bm25','e5','--config',str(config),'--output',str(tmp_path/'result.json')])
    benchmark.main()
    assert seen=={'bm25':2.5,'e5':.75}

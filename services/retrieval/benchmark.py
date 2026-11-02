"""Reproducible source-level evaluation from externally authored judgments.

Each method/variant runs in a separate process so native peak RSS is comparable.
python -m services.retrieval.benchmark --help
"""
import argparse
from dataclasses import asdict
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
import resource
from datetime import datetime, timezone
import statistics
import subprocess
import sys
import time
import tracemalloc

from services.api.schemas import TranscriptSegment
from services.retrieval.config import RetrievalSettings
from services.retrieval.engine import EvidenceRetrievalProvider, RetrievalUnavailable, SearchScope
from services.retrieval.query import tokenize


def quality_metrics(ranked_ids: list[str], grades: dict[str, int]) -> dict:
    # Only central/matched source IDs count. Context neighbours never create
    # extra true positives, and repeated overlapping evidence counts once.
    ids = list(dict.fromkeys(ranked_ids))
    relevant = {sid for sid, grade in grades.items() if grade > 0}
    if not relevant:
        return {"no_answer": True, "false_positive_return": bool(ids), "abstained": not ids}
    dcg = sum((2 ** grades.get(sid, 0) - 1) / math.log2(rank + 1) for rank, sid in enumerate(ids[:10], 1))
    ideal = sorted(grades.values(), reverse=True)[:10]
    idcg = sum((2 ** grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, 1))
    return {
        "no_answer": False,
        "recall_at_5": len(set(ids[:5]) & relevant) / len(relevant),
        "recall_at_10": len(set(ids[:10]) & relevant) / len(relevant),
        "precision_at_5": len(set(ids[:5]) & relevant) / 5,
        "mrr_at_10": next((1 / rank for rank, sid in enumerate(ids[:10], 1) if sid in relevant), 0.),
        "ndcg_at_10": dcg / idcg,
    }


def summarize(rows):
    answered = [r for r in rows if not r["metrics"]["no_answer"]]
    no_answer = [r for r in rows if r["metrics"]["no_answer"]]
    metrics = {key: statistics.mean(r["metrics"][key] for r in answered) if answered else None
               for key in ("recall_at_5", "recall_at_10", "precision_at_5", "mrr_at_10", "ndcg_at_10")}
    metrics.update({"answerable_queries": len(answered), "no_answer_queries": len(no_answer),
                    "false_positive_return_rate": statistics.mean(r["metrics"]["false_positive_return"] for r in no_answer) if no_answer else None,
                    "abstention_rate_no_answer": statistics.mean(r["metrics"]["abstained"] for r in no_answer) if no_answer else None})
    return metrics


def load_dataset(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    conversations, queries, judgments = {}, {}, {}
    participants = {}
    source_splits = {}
    consultation_splits = {}
    for conv in payload["conversations"]:
        cid = conv["conversation_id"]
        if cid in conversations or conv["split"] not in {"dev", "test"}:
            raise ValueError("Conversations need unique IDs and dev/test splits")
        segments = [TranscriptSegment.model_validate(s) for s in conv["segments"]]
        if not segments or any(s.session_id != cid for s in segments) or len({s.id for s in segments}) != len(segments):
            raise ValueError("Conversation segments need unique IDs and matching session IDs")
        conv["segments"] = segments
        if "asr_segments" in conv:
            asr = [TranscriptSegment.model_validate(s) for s in conv["asr_segments"]]
            if {s.id for s in asr} != {s.id for s in segments} or len(asr) != len(segments) or any(s.session_id != cid for s in asr):
                raise ValueError("Paired ASR evaluation requires explicit alignment to the same source IDs")
            conv["asr_segments"] = asr
        for pid in conv.get("participant_ids", []):
            if pid in participants and participants[pid] != conv["split"]:
                raise ValueError("Participant identity crosses development/test splits")
            participants[pid] = conv["split"]
        if conv.get("source_video"):
            source = conv["source_video"]
            if source in source_splits and source_splits[source] != conv["split"]:
                raise ValueError("Shared source video crosses development/test splits")
            source_splits[source] = conv["split"]
        if conv.get("parent_conversation_id"):
            parent = conv["parent_conversation_id"]
            if parent in consultation_splits and consultation_splits[parent] != conv["split"]:
                raise ValueError("Clips from one consultation cross development/test splits")
            consultation_splits[parent] = conv["split"]
        conversations[cid] = conv
    for query in payload["queries"]:
        qid = query["query_id"]
        if qid in queries or query["conversation_id"] not in conversations or not query["text"].strip():
            raise ValueError("Queries need unique IDs, a known conversation and text")
        # Validate filters without accepting arbitrary extra metadata.
        SearchScope(**query.get("filters", {}))
        queries[qid] = query
        judgments[qid] = {}
    for judgment in payload["judgments"]:
        qid = judgment["query_id"]
        if qid not in queries:
            raise ValueError("Judgment references an unknown query")
        query = queries[qid]
        sid = judgment["source_segment_id"]
        if judgment["conversation_id"] != query["conversation_id"] or sid in judgments[qid]:
            raise ValueError("Judgment has mismatched conversation or duplicate source")
        if type(judgment["grade"]) is not int or judgment["grade"] not in (0, 1, 2):
            raise ValueError("Judgment grades must be 0, 1 or 2")
        if not judgment["reviewer"].strip() or not judgment["rationale"].strip():
            raise ValueError("Every judgment requires a reviewer and rationale")
        judgments[qid][sid] = judgment["grade"]
    for qid, query in queries.items():
        conv = conversations[query["conversation_id"]]
        if set(judgments[qid]) != {s.id for s in conv["segments"]}:
            raise ValueError(f"Query {qid}: judge every source turn before computing recall")
        scope = SearchScope(**query.get("filters", {}))
        if scope.patient_id and scope.patient_id != conv.get("patient_id"):
            raise ValueError("Query patient filter does not match conversation")
    return payload, conversations, queries, judgments


def word_error_counts(reference, hypothesis):
    ref, hyp = tokenize(reference), tokenize(hypothesis)
    previous = list(range(len(hyp) + 1))
    for i, token in enumerate(ref, 1):
        current = [i]
        for j, other in enumerate(hyp, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (token != other)))
        previous = current
    return previous[-1], len(ref)


def worker(args):
    _, conversations, queries, judgments = load_dataset(args.dataset)
    settings = RetrievalSettings(**json.loads(args.settings), method=args.worker)
    provider = EvidenceRetrievalProvider(settings)
    rows = []
    tracemalloc.start()
    try:
        for qid, query in queries.items():
            conv = conversations[query["conversation_id"]]
            if conv["split"] != args.split:
                continue
            if args.variant == "asr" and "asr_segments" not in conv:
                continue
            segments = conv["segments" if args.variant == "reference" else "asr_segments"]
            scope_values = {"patient_id": conv.get("patient_id"),
                            "recording_ids": conv.get("recording_ids", {}),
                            "timeline_ids": conv.get("timeline_ids", {})}
            scope_values.update(query.get("filters", {}))
            scope = SearchScope(**scope_values)
            started = time.perf_counter()
            hits = provider.search(query["text"], conv["conversation_id"], segments, k=10, scope=scope)
            first_ms = (time.perf_counter() - started) * 1000
            warm = []
            for _ in range(args.repeats):
                started = time.perf_counter()
                provider.search(query["text"], conv["conversation_id"], segments, k=10, scope=scope)
                warm.append((time.perf_counter() - started) * 1000)
            rows.append({"query_id": qid, "ranked_source_ids": [h.segment_id for h in hits],
                         "scores": [h.score for h in hits], "first_search_ms": first_ms,
                         "warm_search_ms": warm, "diagnostics": provider.diagnostics.copy(),
                         "metrics": quality_metrics([h.segment_id for h in hits], judgments[qid])})
    except RetrievalUnavailable as exc:
        return {"status": "unavailable", "reason": str(exc), "method": args.worker, "variant": args.variant}
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    latencies = sorted(t for row in rows for t in row["warm_search_ms"])
    return {"status": "measured", "method": args.worker, "variant": args.variant,
            "settings": settings.metadata(), "quality": summarize(rows), "queries": rows,
            "latency_ms": {"first_search_total": sum(r["first_search_ms"] for r in rows),
                           "warm_median": statistics.median(latencies) if latencies else None,
                           "warm_p95": latencies[max(0, math.ceil(len(latencies) * .95) - 1)] if latencies else None},
            "memory": {"python_tracemalloc_peak_bytes": peak,
                       "isolated_process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, help="JSON conversations, queries and complete manual judgments")
    parser.add_argument("--output", default="data/retrieval-benchmark.json")
    parser.add_argument("--methods", nargs="+", choices=["fixture", "tfidf", "bm25", "e5", "hybrid"], default=["fixture", "tfidf", "bm25", "e5", "hybrid"])
    parser.add_argument("--split", choices=["dev", "test"], default="test")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--require-all", action="store_true", help="Exit nonzero if any requested method is unavailable")
    parser.add_argument("--method-timeout", type=int, default=180, help="Maximum seconds per method; failures remain in the report")
    parser.add_argument("--min-score", type=float, help="Development-only threshold experiment")
    parser.add_argument("--config", help="Frozen configuration JSON from a completed development run")
    parser.add_argument("--worker", choices=["fixture", "tfidf", "bm25", "e5", "hybrid"], help=argparse.SUPPRESS)
    parser.add_argument("--variant", choices=["reference", "asr"], default="reference", help=argparse.SUPPRESS)
    parser.add_argument("--settings", default="{}", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1 or args.method_timeout < 1:
        parser.error("repeats and method-timeout must be positive")
    if args.worker:
        print(json.dumps(worker(args), ensure_ascii=False))
        return
    try:
        payload, conversations, queries, _ = load_dataset(args.dataset)
    except (ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    if not any(conversations[q["conversation_id"]]["split"] == args.split for q in queries.values()):
        parser.error("No queries in the selected split; add queries and reviewed judgments first")
    packages = {}
    for name in ("rank-bm25", "scikit-learn", "numpy", "sentence-transformers", "transformers", "torch", "tokenizers"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    config = asdict(RetrievalSettings.from_env())
    config.pop("method")
    calibration = None
    if args.config:
        frozen = json.loads(Path(args.config).read_text())
        calibration = frozen.get("calibration")
        if not calibration or calibration.get("split") != "dev" or not calibration.get("dataset_sha256"):
            parser.error("Frozen configurations require development dataset provenance")
        if not set(args.methods) <= set(frozen.get("methods", [])):
            parser.error("Frozen configuration must have been measured on the requested methods")
        config = frozen["settings"]
        RetrievalSettings(**config)
    if args.min_score is not None:
        if args.split != "dev" or args.config or len(args.methods) != 1:
            parser.error("Threshold experiments are development-only; use --config for a frozen test configuration")
        config["min_score"] = args.min_score
    if args.split == "test" and config.get("min_score") is not None and not args.config:
        parser.error("Test thresholds require --config from a development run; do not tune on held-out test conversations")
    RetrievalSettings(**config)
    snapshot = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    dataset_hash = hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest()
    if args.split == "dev":
        calibration = {"split": "dev", "dataset_sha256": dataset_hash}
    report = {"dataset_sha256": hashlib.sha256(Path(args.dataset).read_bytes()).hexdigest(),
              "dataset_description": payload.get("description", ""), "split": args.split,
              "python": platform.python_version(), "platform": platform.platform(), "packages": packages,
              "settings": config, "calibration": calibration, "results": [],
              "created_at": datetime.now(timezone.utc).isoformat(),
              "evaluation_dataset": snapshot,
              "snapshot_sha256": hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
              "dataset_version": snapshot.get("dataset_version", dataset_hash),
              "annotation_status": snapshot.get("annotation_status", "complete externally supplied judgments; review provenance in dataset"),
              "query_count": sum(conversations[q["conversation_id"]]["split"] == args.split for q in queries.values()),
              "result_kind": snapshot.get("kind", "fictional" if "fictional" in snapshot.get("description", "").lower() else "dataset"),
              "metric_policy": "grade > 0 is relevant; graded nDCG uses 2^grade-1; precision denominator 5; central source deduplication; no-answer queries reported separately",
              "memory_policy": "fresh process per method/variant; RSS includes interpreter/imports/native/model memory; tracemalloc excludes most native allocations"}
    paired = [c for c in conversations.values() if c["split"] == args.split and "asr_segments" in c]
    variants = ["reference", "asr"] if paired else ["reference"]
    for variant in variants:
        for method in args.methods:
            command = [sys.executable, "-m", "services.retrieval.benchmark", "--dataset", args.dataset,
                       "--split", args.split, "--repeats", str(args.repeats), "--worker", method,
                       "--variant", variant, "--settings", json.dumps(config)]
            try:
                completed = subprocess.run(command, text=True, capture_output=True, timeout=args.method_timeout)
                if completed.returncode:
                    result = {"status": "failed", "method": method, "variant": variant,
                              "reason": completed.stderr[-4000:] or f"Worker exited {completed.returncode}"}
                else:
                    try:
                        result = json.loads(completed.stdout)
                    except json.JSONDecodeError:
                        result = {"status": "failed", "method": method, "variant": variant,
                                  "reason": "Worker returned invalid JSON: " + completed.stdout[-1000:]}
            except subprocess.TimeoutExpired:
                result = {"status": "failed", "method": method, "variant": variant,
                          "reason": f"Exceeded {args.method_timeout} second method limit"}
            report["results"].append(result)
            print(f"{method}/{variant}: {result['status']}")
    report["all_requested_methods_measured"] = all(r["status"] == "measured" for r in report["results"])
    if paired:
        errors, words = 0, 0
        for conv in paired:
            hypotheses = {s.id: s.text for s in conv["asr_segments"]}
            for s in conv["segments"]:
                e, w = word_error_counts(s.text, hypotheses[s.id])
                errors += e
                words += w
        report["asr"] = {"paired_conversations": len(paired), "word_errors": errors, "reference_words": words,
                         "wer": errors / words if words else None,
                         "normalization": "same NFKC Unicode tokenizer; punctuation ignored; requires manually aligned source IDs",
                         "comparison": "Compare per-query reference/asr scores on paired conversations only"}
        comparisons = {}
        for method in args.methods:
            reference = next(r for r in report["results"] if r["method"] == method and r["variant"] == "reference")
            asr = next(r for r in report["results"] if r["method"] == method and r["variant"] == "asr")
            if reference["status"] != "measured" or asr["status"] != "measured":
                comparisons[method] = {"status": "unavailable"}
                continue
            paired_qids = {row["query_id"] for row in asr["queries"]}
            reference_quality = summarize([row for row in reference["queries"] if row["query_id"] in paired_qids])
            asr_quality = asr["quality"]
            comparisons[method] = {"reference": reference_quality, "asr": asr_quality,
                                   "delta_asr_minus_reference": {key: asr_quality[key] - value
                                                                 for key, value in reference_quality.items()
                                                                 if value is not None and asr_quality[key] is not None}}
        report["asr"]["paired_retrieval_comparisons"] = comparisons
    else:
        report["asr"] = {"status": "not_measured", "reason": "No aligned raw ASR transcripts supplied"}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {output}")
    if args.require_all and not report["all_requested_methods_measured"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

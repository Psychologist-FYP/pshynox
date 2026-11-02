"""Local research endpoints; no clinical database access or automatic patient import."""
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from services.api.schemas import TranscriptSegment
from services.retrieval.config import RetrievalSettings
from services.retrieval.benchmark import load_dataset
from services.retrieval.engine import EvidenceRetrievalProvider, RetrievalUnavailable
from services.retrieval.query import QuerySyntaxError
from services.retrieval.prepare import ROOT, digest, write_json
from services.retrieval.workspace import ResearchError, ResearchStore, identifier

METHODS = Literal["fixture", "tfidf", "bm25", "e5", "hybrid"]
RUNNING = {}
RUN_LOCK = threading.Lock()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryInput(Input):
    revision: int = Field(ge=0)
    query_id: str | None = None
    text: str = Field(min_length=1, max_length=2000)
    intent: str = Field(min_length=1, max_length=4000)
    draft: bool = True
    no_answer_confirmed: bool = False
    reviewer: str = Field(default="", max_length=200)


class GradeInput(Input):
    revision: int = Field(ge=0)
    grade: StrictInt | None = Field(default=None, ge=0, le=2)
    reviewer: str = Field(default="", max_length=200)
    rationale: str = Field(default="", max_length=4000)


class RevisionInput(Input):
    revision: int = Field(ge=0)


class ExploreInput(RevisionInput):
    text: str = Field(min_length=1, max_length=2000)
    method: METHODS = "bm25"


class RunInput(Input):
    split: Literal["dev", "test"] = "dev"
    methods: list[METHODS] = Field(default_factory=lambda: ["fixture", "tfidf", "bm25", "e5", "hybrid"], min_length=1, max_length=5)
    min_score: float | None = None
    frozen_run: str | None = None


def run_directory(store, rid):
    path = store.root / "runs" / identifier(rid)
    if not (path / "job.json").is_file():
        raise ResearchError("Run not found", 404)
    return path


def finish_job(rid, folder, process, log):
    try:
        status = process.wait()
        job = json.loads((folder / "job.json").read_text())
        if job["status"] != "interrupted":
            job["status"] = "completed" if status == 0 and (folder / "report.json").exists() else "failed"
            job["exit_code"] = status
            if status:
                job["error"] = (folder / "run.log").read_text()[-4000:]
            write_json(folder / "job.json", job)
    finally:
        log.close()
        with RUN_LOCK:
            RUNNING.pop(rid, None)


def shutdown_research():
    with RUN_LOCK:
        active = list(RUNNING.values())
    for process, folder in active:
        job = json.loads((folder / "job.json").read_text())
        job["status"] = "interrupted"
        write_json(folder / "job.json", job)
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass


def build_research_router():
    router = APIRouter(prefix="/api/v1/research")

    @router.get("/datasets")
    def datasets():
        store = ResearchStore()
        return [store.overview(p.stem) for p in sorted((store.root / "datasets").glob("*.json"))]

    @router.get("/datasets/{name}/conversations/{cid}")
    def conversation(name: str, cid: str):
        return ResearchStore().detail(name, cid)

    @router.put("/datasets/{name}/conversations/{cid}/queries")
    def query(name: str, cid: str, payload: QueryInput):
        return ResearchStore().save_query(name, cid, payload.model_dump())

    @router.put("/datasets/{name}/conversations/{cid}/queries/{qid}/turns/{sid}")
    def grade(name: str, cid: str, qid: str, sid: str, payload: GradeInput):
        return ResearchStore().grade(name, cid, qid, sid, payload.model_dump())

    @router.post("/datasets/{name}/conversations/{cid}/include")
    def expand(name: str, cid: str, payload: RevisionInput):
        return ResearchStore().expand(name, cid, payload.revision)

    @router.get("/datasets/{name}/export")
    def export(name: str, split: Literal["dev", "test"] = "dev"):
        payload = ResearchStore().export(name, split)
        return JSONResponse(payload, headers={"Content-Disposition": f'attachment; filename="{identifier(name)}-{split}.json"'})

    @router.post("/datasets/{name}/conversations/{cid}/explore")
    def explore(name: str, cid: str, payload: ExploreInput):
        store = ResearchStore()
        conv = store.conversation(name, cid)
        store.mark_explored(name, cid, payload.revision)
        try:
            hits = EvidenceRetrievalProvider(RetrievalSettings(
                **{**asdict(RetrievalSettings.from_env()), "method": payload.method}
            )).search(payload.text, cid, [TranscriptSegment.model_validate(s) for s in conv["segments"]])
        except (RetrievalUnavailable, QuerySyntaxError) as exc:
            raise ResearchError(str(exc)) from exc
        return {"mode": "exploration; not evaluation", "revision": store.state(name)["revision"],
                "hits": [hit.model_dump(mode="json") for hit in hits]}

    @router.post("/datasets/{name}/runs", status_code=202)
    def start_run(name: str, payload: RunInput):
        store = ResearchStore()
        if payload.min_score is not None and (not math.isfinite(payload.min_score) or payload.split != "dev" or len(payload.methods) != 1 or payload.frozen_run):
            raise ResearchError("Threshold experiments require one method and the development split. Test runs use a frozen development configuration.")
        dataset = store.export(name, payload.split)
        rid = "run-" + uuid4().hex
        folder = store.root / "runs" / rid
        command = [sys.executable, "-m", "services.retrieval.benchmark", "--dataset", str(folder / "dataset.json"),
                   "--split", payload.split, "--output", str(folder / "report.json"), "--methods", *payload.methods]
        config = None
        if payload.frozen_run:
            previous = run_directory(store, payload.frozen_run)
            report = json.loads((previous / "report.json").read_text()) if (previous / "report.json").exists() else {}
            measured = [r["method"] for r in report.get("results", []) if r["status"] == "measured"]
            if report.get("split") != "dev" or report.get("evaluation_dataset", {}).get("dataset_id") != name or report.get("dataset_version") != dataset["dataset_version"] or not set(payload.methods) <= set(measured):
                raise ResearchError("Use a measured development run from this dataset/version with the same methods.")
            config = {"settings": report["settings"], "calibration": report["calibration"], "methods": measured}
            command += ["--config", str(folder / "config.json")]
        elif payload.min_score is not None:
            command += ["--min-score", str(payload.min_score)]
        with RUN_LOCK:
            if RUNNING:
                raise ResearchError("Another benchmark is running. Wait for it to finish.", 409)
            write_json(folder / "dataset.json", dataset)
            if config:
                write_json(folder / "config.json", config)
            job = {"run_id": rid, "dataset_id": name, "split": payload.split,
                   "status": "running", "methods": payload.methods, "dataset_version": dataset["dataset_version"],
                   "annotation_status": dataset["annotation_status"], "query_count": len(dataset["queries"])}
            write_json(folder / "job.json", job)
            log = (folder / "run.log").open("w")
            env = os.environ.copy()
            # Thresholds are explicit per run, independent of clinical UI settings.
            env.pop("RETRIEVAL_MIN_SCORE", None)
            try:
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            except OSError:
                log.close()
                job["status"] = "failed"
                write_json(folder / "job.json", job)
                raise
            RUNNING[rid] = (process, folder)
            threading.Thread(target=finish_job, args=(rid, folder, process, log), daemon=True).start()
        return job

    @router.get("/runs")
    def runs():
        store = ResearchStore()
        items = []
        for path in sorted((store.root / "runs").glob("*/job.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            job = json.loads(path.read_text())
            if job["status"] == "running" and job["run_id"] not in RUNNING:
                job["status"] = "interrupted"
                job["error"] = "Backend restarted; launch a new run. Existing snapshot retained."
                write_json(path, job)
            items.append(job)
        if (ROOT / "data/retrieval-engineering-benchmark.json").is_file():
            items.append({"run_id": "fictional-smoke", "dataset_id": "fictional engineering fixture", "status": "completed", "split": "test", "query_count": 9,
                          "annotation_status": "fictional engineering labels; no clinical validation"})
        return items

    @router.post("/runs/import", status_code=201)
    async def import_result(request: Request):
        raw = await request.body()
        if len(raw) > 20 * 1024 * 1024:
            raise ResearchError("Results JSON must be at most 20 MB")
        try:
            report = json.loads(raw)
            snapshot = report["evaluation_dataset"]
            if not isinstance(snapshot, dict) or not isinstance(report["results"], list):
                raise ValueError("Expected dataset and method result objects")
            if report["split"] not in {"dev", "test"} or not report["results"]:
                raise ValueError("A split and method results are required")
            for result in report["results"]:
                if not isinstance(result, dict) or result.get("variant") not in {"reference", "asr"} or not isinstance(result.get("reason", ""), str):
                    raise ValueError("Invalid method result")
                if result["method"] not in {"fixture", "tfidf", "bm25", "e5", "hybrid"} or result["status"] not in {"measured", "unavailable", "failed"}:
                    raise ValueError("Unknown method or result status")
                for field in ("quality", "latency_ms", "memory"):
                    if not isinstance(result.get(field, {}), dict):
                        raise ValueError("Invalid metric object")
                    for value in result.get(field, {}).values():
                        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                            raise ValueError("Metrics must contain finite numbers or null")
            if not isinstance(report["settings"], dict) or len(report["dataset_sha256"]) != 64:
                raise ValueError("Missing benchmark settings or dataset hash")
            if report.get("snapshot_sha256") and report["snapshot_sha256"] != digest(snapshot):
                raise ValueError("Embedded dataset checksum mismatch")
        except (ValueError, KeyError, TypeError) as exc:
            raise ResearchError(f"Invalid benchmark report: {exc}") from exc
        store = ResearchStore()
        rid = "import-" + uuid4().hex
        folder = store.root / "runs" / rid
        write_json(folder / "dataset.json", snapshot)
        try:
            _, conversations, queries, _ = load_dataset(folder / "dataset.json")
            for result in report["results"]:
                for row in result.get("queries", []):
                    if row["query_id"] not in queries:
                        raise ValueError("Ranked query not present in dataset")
                    conv = conversations[queries[row["query_id"]]["conversation_id"]]
                    ids = {s.id for s in conv["segments" if result["variant"] == "reference" else "asr_segments"]}
                    if not set(row["ranked_source_ids"]) <= ids or len(row["ranked_source_ids"]) != len(row["scores"]):
                        raise ValueError("Ranked passages do not match the report dataset")
        except (ValueError, KeyError, TypeError) as exc:
            raise ResearchError(f"Invalid report dataset: {exc}") from exc
        report["query_count"] = sum(conversations[q["conversation_id"]]["split"] == report["split"] for q in queries.values())
        report["result_kind"] = "fictional" if "fictional" in snapshot.get("description", "").lower() or snapshot.get("kind") == "fictional" else "dataset"
        report.setdefault("annotation_status", snapshot.get("annotation_status", "Externally supplied complete judgments; inspect reviewer provenance"))
        report.setdefault("asr", {"status": "not_measured"})
        job = {"run_id": rid, "status": "completed", "dataset_id": snapshot.get("dataset_id", report["result_kind"]),
               "split": report["split"], "query_count": report["query_count"], "dataset_version": report.get("dataset_version"),
               "annotation_status": report["annotation_status"], "imported": True}
        write_json(folder / "report.json", report)
        write_json(folder / "job.json", job)
        return job

    @router.get("/runs/{rid}")
    def result(rid: str):
        store = ResearchStore()
        if rid == "fictional-smoke":
            path = ROOT / "data/retrieval-engineering-benchmark.json"
            if not path.exists():
                raise ResearchError("Fictional benchmark not available", 404)
            report = json.loads(path.read_text())
            report.update(result_kind="fictional", evaluation_dataset=json.loads((ROOT / "tests/retrieval/fixtures/engineering.json").read_text()),
                          annotation_status="Fictional engineering labels; no human clinical review")
            report["query_count"] = len(report["evaluation_dataset"]["queries"])
            return {"job": {"run_id": rid, "status": "completed"}, "report": report}
        folder = run_directory(store, rid)
        job = json.loads((folder / "job.json").read_text())
        report = json.loads((folder / "report.json").read_text()) if (folder / "report.json").exists() else None
        return {"job": job, "report": report}

    return router

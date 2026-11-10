"""Runtime settings and per-call measurement for the fast DSM pipeline.

``FastRuntime`` is the existing ``services.note_generation.runtime.Runtime`` (one owned llama-server, same budgeting,
same JSON-schema grammar), with two additions that need no change to the benchmark-bound runtime file:

* **Server settings.** ``Runtime.load()`` passes ``-c``, ``-np 1``, ``-ngl``, ``-b`` and ``-ub`` and inherits the process
  environment. llama-server reads every other flag from ``LLAMA_ARG_*`` variables, so the settings are applied by
  setting those variables (and ``NOTE_BATCH_SIZE``/``NOTE_UBATCH_SIZE``) only around ``super().load()``. Callers hold
  the ``memory_slot`` lock, so only one model process starts at a time. ``settings=None`` changes nothing, which is how
  ``dsm-v1`` keeps today's server exactly.
* **Call log.** After every model call one JSON row is appended to ``calls.jsonl``: token counts, cache reuse, timings,
  the caller's stage/group/window and hashes. Never transcript or model text.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from services.note_generation.runtime import GenerationError, Runtime

REUSE_TOLERANCE = 8  # tokens: a template boundary may re-tokenise slightly differently
_ENV_LOCK = threading.Lock()


@dataclass(frozen=True)
class ServerSettings:
    """llama-server settings for one runtime. ``None`` leaves the server default (or today's value) in place."""
    context: int = 4096
    batch: int = 256
    ubatch: int = 128
    flash_attn: str | None = None          # on | off | auto (server default auto)
    cache_type_k: str | None = None        # f16 (default) | q8_0 | ...
    cache_type_v: str | None = None
    threads: int | None = None
    cache_reuse: int | None = None
    spec_type: str | None = None           # ngram-simple | ngram-mod | ... (draft-free only; no draft models)
    spec_draft_n_max: int | None = None
    name: str = "custom"
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if not 2048 <= self.context <= 32768 or self.batch < self.ubatch or self.ubatch < 1:
            raise GenerationError("Invalid fast-runtime settings: context 2048..32768 and batch >= ubatch >= 1")
        if self.flash_attn not in {None, "on", "off", "auto"}:
            raise GenerationError("flash_attn must be on, off or auto")
        if (self.cache_type_v or "f16") != "f16" and self.flash_attn == "off":
            raise GenerationError("A quantised V cache needs flash attention")
        if any(not str(k).startswith("LLAMA_ARG_") for k in self.extra):
            raise GenerationError("extra settings must be LLAMA_ARG_* variables")

    def env(self) -> dict[str, str]:
        values = {"NOTE_BATCH_SIZE": str(self.batch), "NOTE_UBATCH_SIZE": str(self.ubatch)}
        for key, value in (("LLAMA_ARG_FLASH_ATTN", self.flash_attn), ("LLAMA_ARG_CACHE_TYPE_K", self.cache_type_k),
                           ("LLAMA_ARG_CACHE_TYPE_V", self.cache_type_v), ("LLAMA_ARG_THREADS", self.threads),
                           ("LLAMA_ARG_CACHE_REUSE", self.cache_reuse), ("LLAMA_ARG_SPEC_TYPE", self.spec_type),
                           ("LLAMA_ARG_SPEC_DRAFT_N_MAX", self.spec_draft_n_max)):
            if value is not None:
                values[key] = str(value)
        return values | {k: str(v) for k, v in self.extra.items()}

    def describe(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v not in (None, {})}

    def sha256(self) -> str:
        body = {k: v for k, v in self.describe().items() if k != "name"}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


# Today's clinical server (runtime.py defaults plus .env): the reference point for every measurement.
CURRENT = ServerSettings(context=4096, batch=256, ubatch=128, name="current")
# Phase 1 sweep (plan §4.8): batch/micro-batch, threads and KV type give <= 3% on this GPU, so the tuned server is
# today's server with flash attention pinned on (the "auto" default already enabled it). Speed comes from shorter
# prompts and outputs instead: prefill falls from 142 to 99 tokens/s at 3,000 tokens of depth.
TUNED = replace(CURRENT, flash_attn="on", name="tuned")


@contextmanager
def scoped_env(values: dict[str, str]):
    """Set environment variables for the duration of a model start, then restore them exactly."""
    with _ENV_LOCK:
        saved = {k: os.environ.get(k) for k in values}
        os.environ.update(values)
        try:
            yield
        finally:
            for key, old in saved.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old


class CallLog:
    """Append-only JSONL of model calls and timed phases. Rows hold counts, timings and hashes only."""

    TEXT_FREE = ("run_id", "pipeline", "session_id", "stage", "group", "window", "attempt", "prefix_tokens")

    def __init__(self, path: Path | None, run_id: str = "", pipeline: str = ""):
        self.path = Path(path) if path else None
        self.base = {"run_id": run_id, "pipeline": pipeline}
        self._context: list[dict] = []
        self.rows: list[dict] = []
        self.warnings: list[dict] = []
        self._last_window: tuple | None = None

    @contextmanager
    def context(self, **fields):
        unknown = set(fields) - set(self.TEXT_FREE)
        if unknown:
            raise ValueError(f"call-log context accepts only {self.TEXT_FREE}; got {sorted(unknown)}")
        self._context.append(fields)
        try:
            yield
        finally:
            self._context.pop()

    def current(self) -> dict:
        out = dict(self.base)
        for c in self._context:
            out.update(c)
        return out

    def _append(self, row: dict) -> None:
        self.rows.append(row)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(row, sort_keys=True) + "\n")

    @contextmanager
    def phase(self, name: str, **fields):
        started = time.monotonic()
        try:
            yield
        finally:
            self._append({**self.current(), **fields, "kind": "phase", "phase": name, "seconds": round(time.monotonic() - started, 3),
                          "at": time.time()})

    def record_call(self, entry: dict, *, settings_sha: str, schema_sha: str, error: str | None = None) -> dict:
        t = entry.get("timings") or {}
        ctx = self.current()
        row = {**ctx, "kind": "call", "at": time.time(),
               "prompt_tokens": entry.get("formatted_prompt_tokens"), "cache_tokens": t.get("cache_n"),
               "prefill_tokens": t.get("prompt_n"), "prefill_ms": t.get("prompt_ms"),
               "generated_tokens": t.get("predicted_n", entry.get("tokens_predicted")), "generation_ms": t.get("predicted_ms"),
               "request_s": round(entry.get("request_seconds") or 0, 3), "output_reserved": entry.get("reserved_output_tokens"),
               "finish_reason": entry.get("finish_reason"), "runtime_stage": entry.get("stage"),
               "server_config_sha256": settings_sha, "model_sha256": entry.get("model_sha256"),
               "prompt_sha256": entry.get("prompt_sha256"), "schema_sha256": schema_sha, "error": error}
        window = (ctx.get("session_id"), ctx.get("window"), ctx.get("stage"))
        prefix = ctx.get("prefix_tokens")
        if window == self._last_window and prefix and (row["cache_tokens"] or 0) < prefix - REUSE_TOLERANCE:
            warning = {"event": "cache.reuse_low", "session_id": window[0], "window": window[1], "expected_prefix": prefix,
                       "cache_tokens": row["cache_tokens"]}
            self.warnings.append(warning)
            row["warning"] = "cache.reuse_low"
        self._last_window = window
        self._append(row)
        return row

    def summary(self) -> dict:
        calls = [r for r in self.rows if r["kind"] == "call"]
        total = lambda k: sum(r.get(k) or 0 for r in calls)  # noqa: E731
        return {"calls": len(calls), "prompt_tokens": total("prompt_tokens"), "cache_tokens": total("cache_tokens"),
                "prefill_tokens": total("prefill_tokens"), "prefill_s": round(total("prefill_ms") / 1000, 2),
                "generated_tokens": total("generated_tokens"), "generation_s": round(total("generation_ms") / 1000, 2),
                "request_s": round(total("request_s"), 2), "reuse_warnings": len(self.warnings),
                "phases": {r["phase"]: r["seconds"] for r in self.rows if r["kind"] == "phase"}}


class Instrumented:
    """Mixin: log every ``generate`` call (also failed ones that reached the server) and time model loads."""

    settings: ServerSettings | None = None
    calllog: CallLog

    def _settings_sha(self) -> str:
        return self.settings.sha256() if self.settings else "unchanged"

    def load(self, writer):
        if getattr(self, "writer", None) == writer:
            return
        with self.calllog.phase("model_load"):
            if self.settings is None:
                super().load(writer)
            else:
                with scoped_env(self.settings.env()):
                    super().load(writer)
        self.metrics["fast_server_settings"] = self.settings.describe() if self.settings else "unchanged"

    def generate(self, system, data, output=700):
        before = len(self.metrics.get("calls", []))
        schema_sha = hashlib.sha256(json.dumps(getattr(self, "response_schema", None), sort_keys=True).encode()).hexdigest()
        try:
            value = super().generate(system, data, output)
        except GenerationError as exc:
            calls = self.metrics.get("calls", [])
            if len(calls) > before:
                self.calllog.record_call(calls[-1], settings_sha=self._settings_sha(), schema_sha=schema_sha, error=type(exc).__name__)
            raise
        calls = self.metrics.get("calls", [])
        if len(calls) > before:
            self.calllog.record_call(calls[-1], settings_sha=self._settings_sha(), schema_sha=schema_sha)
        return value


class FastRuntime(Instrumented, Runtime):
    """The owned llama-server with optional settings and a call log. ``settings=None`` keeps today's server."""

    def __init__(self, folder, cancel=None, *, settings: ServerSettings | None = None, calllog: CallLog | None = None):
        super().__init__(folder, cancel)
        self.settings = settings
        if settings is not None:
            self.context = settings.context
            if not 0 <= self.safety_margin < self.context:
                raise GenerationError("Invalid safety margin for the configured context")
        self.calllog = calllog or CallLog(Path(folder) / "calls.jsonl")

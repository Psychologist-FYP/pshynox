"""FastRuntime settings, scoped environment and the per-call log (no model, no network)."""
import json
import os

import pytest

from services.note_generation.runtime import GenerationError, OutputLimitError
from services.progress.dsm.fast.runtime import CURRENT, CallLog, Instrumented, ServerSettings, scoped_env
from tests.progress.fake_runtime import FakeRuntime


class TimedFake(FakeRuntime):
    """A fake server that reports llama-server style timings, with a prompt cache keyed on the shared prefix."""

    def __init__(self, *a, prefix_reuse=True, limit=False, **k):
        super().__init__(lambda s, d: {"ok": True}, *a, **k)
        self.prefix_reuse, self.limit, self.last = prefix_reuse, limit, ""
        self.loaded_env = None

    def load(self, writer):
        self.loaded_env = {k: os.environ.get(k) for k in ("NOTE_UBATCH_SIZE", "LLAMA_ARG_CACHE_TYPE_K", "LLAMA_ARG_FLASH_ATTN")}
        self.writer = writer

    def generate(self, system, data, output=700):
        text = self.prompt(system, data)
        common = 0
        if self.prefix_reuse:
            while common < min(len(text), len(self.last)) and text[common] == self.last[common]:
                common += 1
        self.last = text
        n = self.count(text)
        self.metrics["calls"].append({"timings": {"cache_n": common // 4, "prompt_n": n - common // 4, "prompt_ms": 10.0 * n,
                                                  "predicted_n": 20, "predicted_ms": 600.0},
                                      "tokens_predicted": 20, "formatted_prompt_tokens": n, "reserved_output_tokens": output,
                                      "request_seconds": 1.0, "finish_reason": "output_limit" if self.limit else "complete",
                                      "model_sha256": "f" * 64, "prompt_sha256": "a" * 64, "stage": "x"})
        if self.limit:
            raise OutputLimitError("Incomplete output")
        return {"ok": True}


class Logged(Instrumented, TimedFake):
    def __init__(self, path, settings=None, **k):
        super().__init__(**k)
        self.settings = settings
        self.calllog = CallLog(path, run_id="r1", pipeline="dsm-fast")


PACKET = {"turns": ["0 T: How are you sleeping?", "1 C: Badly, I wake at four every night."] * 40}


def test_settings_env_and_hash():
    s = ServerSettings(context=8192, batch=512, ubatch=512, flash_attn="on", cache_type_k="q8_0", cache_type_v="q8_0", name="a")
    env = s.env()
    assert env["NOTE_UBATCH_SIZE"] == "512" and env["LLAMA_ARG_CACHE_TYPE_V"] == "q8_0" and env["LLAMA_ARG_FLASH_ATTN"] == "on"
    assert "LLAMA_ARG_SPEC_TYPE" not in env
    assert s.sha256() == ServerSettings(context=8192, batch=512, ubatch=512, flash_attn="on", cache_type_k="q8_0", cache_type_v="q8_0", name="b").sha256()
    assert s.sha256() != CURRENT.sha256()
    assert CURRENT.env() == {"NOTE_BATCH_SIZE": "256", "NOTE_UBATCH_SIZE": "128"}  # today's server, nothing else changed


@pytest.mark.parametrize("kwargs", [{"context": 1024}, {"batch": 128, "ubatch": 256}, {"flash_attn": "maybe"},
                                    {"cache_type_v": "q8_0", "flash_attn": "off"}, {"extra": {"PATH": "/tmp"}}])
def test_invalid_settings_rejected(kwargs):
    with pytest.raises(GenerationError):
        ServerSettings(**kwargs)


def test_scoped_env_restores(monkeypatch):
    monkeypatch.setenv("NOTE_UBATCH_SIZE", "128")
    monkeypatch.delenv("LLAMA_ARG_CACHE_TYPE_K", raising=False)
    with scoped_env({"NOTE_UBATCH_SIZE": "512", "LLAMA_ARG_CACHE_TYPE_K": "q8_0"}):
        assert os.environ["NOTE_UBATCH_SIZE"] == "512" and os.environ["LLAMA_ARG_CACHE_TYPE_K"] == "q8_0"
    assert os.environ["NOTE_UBATCH_SIZE"] == "128" and "LLAMA_ARG_CACHE_TYPE_K" not in os.environ


def test_settings_apply_only_during_load(tmp_path, monkeypatch):
    monkeypatch.delenv("LLAMA_ARG_CACHE_TYPE_K", raising=False)
    rt = Logged(tmp_path / "calls.jsonl", settings=ServerSettings(batch=512, ubatch=512, cache_type_k="q8_0", cache_type_v="q8_0", flash_attn="on"))
    rt.load("qwen")
    assert rt.loaded_env == {"NOTE_UBATCH_SIZE": "512", "LLAMA_ARG_CACHE_TYPE_K": "q8_0", "LLAMA_ARG_FLASH_ATTN": "on"}
    assert "LLAMA_ARG_CACHE_TYPE_K" not in os.environ
    plain = Logged(tmp_path / "plain.jsonl")
    plain.load("qwen")
    assert plain.loaded_env["LLAMA_ARG_CACHE_TYPE_K"] is None  # settings=None: today's server untouched
    assert [r["phase"] for r in rt.calllog.rows] == ["model_load"]


def test_call_rows_are_text_free_and_complete(tmp_path):
    rt = Logged(tmp_path / "calls.jsonl")
    with rt.calllog.context(session_id="s1", stage="A", group="depressive", window=0):
        rt.generate("SYSTEM", {**PACKET, "task": "depressive"}, 300)
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    for key in ("prompt_tokens", "cache_tokens", "prefill_tokens", "prefill_ms", "generated_tokens", "generation_ms", "stage", "group",
                "window", "session_id", "server_config_sha256", "schema_sha256"):
        assert key in row
    assert row["group"] == "depressive" and row["pipeline"] == "dsm-fast" and row["generated_tokens"] == 20
    dumped = json.dumps(rows)
    assert "sleeping" not in dumped and "SYSTEM" not in dumped  # counts and hashes only


def test_context_rejects_free_text_fields(tmp_path):
    log = CallLog(tmp_path / "c.jsonl")
    with pytest.raises(ValueError):
        with log.context(basis="the client said..."):
            pass


def test_reuse_detected_between_calls_of_one_window(tmp_path):
    rt = Logged(tmp_path / "calls.jsonl")
    prefix = rt.count(rt.prompt("SYSTEM", PACKET)) - 4
    with rt.calllog.context(session_id="s1", stage="A", window=0, prefix_tokens=prefix):
        for group in ("depressive", "anxiety", "social"):
            with rt.calllog.context(group=group):
                rt.generate("SYSTEM", {**PACKET, "task": group}, 300)
    assert rt.calllog.warnings == []
    calls = [r for r in rt.calllog.rows if r["kind"] == "call"]
    assert calls[1]["cache_tokens"] >= prefix - 8 and calls[2]["cache_tokens"] >= prefix - 8


def test_reuse_dropping_to_zero_fails_loudly(tmp_path):
    rt = Logged(tmp_path / "calls.jsonl", prefix_reuse=False)
    prefix = rt.count(rt.prompt("SYSTEM", PACKET)) - 4
    with rt.calllog.context(session_id="s1", stage="A", window=0, prefix_tokens=prefix):
        rt.generate("SYSTEM", {**PACKET, "task": "depressive"}, 300)
        rt.generate("SYSTEM", {**PACKET, "task": "anxiety"}, 300)
    assert [w["event"] for w in rt.calllog.warnings] == ["cache.reuse_low"]
    assert rt.calllog.summary()["reuse_warnings"] == 1


def test_new_window_is_not_a_reuse_failure(tmp_path):
    rt = Logged(tmp_path / "calls.jsonl", prefix_reuse=False)
    for window in (0, 1):
        with rt.calllog.context(session_id="s1", stage="A", window=window, prefix_tokens=100):
            rt.generate("SYSTEM", {**PACKET, "task": f"w{window}"}, 300)
    assert rt.calllog.warnings == []


def test_failed_calls_are_still_logged(tmp_path):
    rt = Logged(tmp_path / "calls.jsonl", limit=True)
    with rt.calllog.context(session_id="s1", stage="A", group="elevated", window=0):
        with pytest.raises(OutputLimitError):
            rt.generate("SYSTEM", {**PACKET, "task": "elevated"}, 300)
    (row,) = [r for r in rt.calllog.rows if r["kind"] == "call"]
    assert row["error"] == "OutputLimitError" and row["finish_reason"] == "output_limit"


def test_summary_totals(tmp_path):
    rt = Logged(tmp_path / "calls.jsonl")
    with rt.calllog.phase("triage"):
        pass
    with rt.calllog.context(session_id="s1", stage="A", window=0):
        rt.generate("SYSTEM", {**PACKET, "task": "a"}, 300)
        rt.generate("SYSTEM", {**PACKET, "task": "b"}, 300)
    s = rt.calllog.summary()
    assert s["calls"] == 2 and s["generated_tokens"] == 40 and s["generation_s"] == 1.2 and "triage" in s["phases"]
    assert s["cache_tokens"] > 0 and s["prefill_tokens"] < s["prompt_tokens"]

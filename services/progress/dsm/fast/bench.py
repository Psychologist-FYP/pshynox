"""Phase 1 measurements: llama-bench sweep and a real-call replay of DSM Stage A prompt shapes.

    python -m services.progress.dsm.fast.bench sweep  --out data/research/progress-runs/dsm-fast-bench
    python -m services.progress.dsm.fast.bench replay --out data/research/progress-runs/dsm-fast-bench [--settings current,b512 ...]

``replay`` builds its calls once from the fictional role-play fixture (session 1) with the unchanged ``dsm-v1`` Stage A
code (same system prompt, packet planning and grammar), then sends the identical calls under each server setting:

* ``dep1`` depressive chunk 1 (cold prompt), ``dep2`` chunk 2 (same packet, so the prefix is cached);
* ``dep2-pattern`` the same call with the alias enum in the grammar replaced by a pattern (grammar-cost test);
* with context >= 6144 only: ``full-dep`` and ``full-elev``, two groups sharing one whole-transcript packet (prefix
  caching across groups on the existing prompts).

Every call is logged to ``calls.jsonl`` (counts and timings only). Nothing touches the clinical database.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

from .runtime import CURRENT, TUNED, CallLog, FastRuntime, ServerSettings

ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "tests/fixtures/roleplay_dsm"

SETTINGS = {
    "current": CURRENT,
    "tuned": TUNED,
    "b512": replace(CURRENT, batch=512, ubatch=512, name="b512"),
    "b1024u512": replace(CURRENT, batch=1024, ubatch=512, name="b1024u512"),
    "b1024": replace(CURRENT, batch=1024, ubatch=1024, name="b1024"),
}


def setting(name: str) -> ServerSettings:
    """Named settings; ``<base>+fa-off``, ``<base>+q8``, ``<base>+ctx8192`` and ``<base>+ngram`` compose."""
    base, *mods = name.split("+")
    s = SETTINGS[base]
    for m in mods:
        if m == "fa-off":
            s = replace(s, flash_attn="off")
        elif m == "fa-on":
            s = replace(s, flash_attn="on")
        elif m == "q8":
            s = replace(s, cache_type_k="q8_0", cache_type_v="q8_0", flash_attn="on")
        elif m.startswith("ctx"):
            s = replace(s, context=int(m[3:]))
        elif m.startswith("ngram"):
            s = replace(s, spec_type=m if m.startswith("ngram-") else "ngram-simple")
        elif m.startswith("t") and m[1:].isdigit():
            s = replace(s, threads=int(m[1:]))
        else:
            raise SystemExit(f"unknown setting modifier {m!r}")
    return replace(s, name=name)


def _env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except ImportError:
        pass


# ---------------------------------------------------------------- llama-bench sweep

def sweep(out: Path) -> list[dict]:
    _env()
    server = Path(os.environ.get("NOTE_LLAMA_SERVER", ROOT / "models/llama.cpp/build/bin/llama-server"))
    binary = server.parent / "llama-bench"
    from services.note_generation.runtime import model_path, runtime_environment
    model = str(model_path("qwen"))
    runs = [
        # micro-batch sweep, flash attention on (today's effective setting)
        ["-b", "256,512,1024", "-ub", "128,256,512", "-fa", "on", "-p", "2048", "-n", "0"],
        ["-b", "1024", "-ub", "1024", "-fa", "on", "-p", "2048", "-n", "0"],
        # flash attention and KV type, prompt and generation at depth (generation after a 3,000-token prompt)
        ["-b", "512", "-ub", "512", "-fa", "on,off", "-p", "2048", "-n", "128", "-d", "0,3000"],
        ["-b", "512", "-ub", "512", "-fa", "on", "-ctk", "q8_0", "-ctv", "q8_0", "-p", "2048", "-n", "128", "-d", "0,3000,7000"],
        # CPU threads (host-side work only; the model is fully on the GPU)
        ["-b", "512", "-ub", "512", "-fa", "on", "-t", "4,8", "-p", "0", "-n", "128"],
    ]
    results = []
    from services.note_generation.storage import memory_slot
    with memory_slot(timeout=600):
        for args in runs:
            cmd = [str(binary), "-m", model, "-ngl", "99", "-r", "2", "-o", "jsonl", *args]
            started = time.monotonic()
            proc = subprocess.run(cmd, capture_output=True, text=True, env=runtime_environment(), timeout=1800)
            if proc.returncode != 0:
                results.append({"args": args, "error": proc.stderr[-2000:]})
                continue
            for line in proc.stdout.splitlines():
                if line.startswith("{"):
                    r = json.loads(line)
                    results.append({k: r.get(k) for k in ("n_batch", "n_ubatch", "flash_attn", "type_k", "type_v", "n_threads", "n_prompt", "n_gen",
                                                          "n_depth", "avg_ts", "stddev_ts")} | {"args": args})
            print(f"llama-bench {' '.join(args)}: {time.monotonic() - started:.0f} s", flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "llama-bench.json").write_text(json.dumps(results, indent=1))
    for r in results:
        if "error" in r:
            print("ERROR", r["args"], r["error"][-300:])
        else:
            kind = "pp" if r["n_prompt"] else "tg"
            print(f"{kind}{r['n_prompt'] or r['n_gen']}@d{r['n_depth']} b={r['n_batch']} ub={r['n_ubatch']} fa={r['flash_attn']} kv={r['type_k']}/{r['type_v']} "
                  f"t={r['n_threads']}: {r['avg_ts']:.1f} tok/s")
    return results


# ---------------------------------------------------------------- replay

def _pattern_schema(schema, aliases: list[str]):
    """Replace every enum of transcript aliases with a pattern: the same strings allowed, a far smaller grammar."""
    schema = copy.deepcopy(schema)

    def walk(node):
        if isinstance(node, dict):
            if node.get("enum") == aliases:
                node.pop("enum")
                node["pattern"] = "^T[0-9]{1,3}$"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)
    return schema


def build_specs(runtime) -> list[dict]:
    """Calls with exactly the dsm-v1 Stage A shapes for role-play session 1 (packet planned at 4,096 context)."""
    from services.retrieval.engine import digest

    from services.progress.evidence import Retriever, aliases_for, model_row

    from .. import extraction as ex
    from ..fixture_run import load_fixture
    from ..prompts import EXTRACT_DSM
    from ..registry import dsm_registry

    reg = dsm_registry()
    s1 = load_fixture(FIXTURE)[0]
    snap = {"session_id": s1["session_id"], "revision": digest(s1["segments"]), "segments": s1["segments"]}
    aliases = aliases_for(snap["segments"])
    by_id = {row["id"]: a for a, row in aliases.items()}
    full_rows = [model_row(a, r) for a, r in aliases.items()]
    retriever = Retriever()
    tasks = ex.group_tasks(reg, "depressive")

    def payload_for(chosen):
        return lambda chunk: {"sources": chosen, "requested": ex.requested(reg, chunk)}
    rows, plan, mode = ex._packet(runtime, EXTRACT_DSM, tasks, payload_for, full_rows, snap, aliases, by_id,
                                  ex.group_queries(reg, tasks), retriever, None, "auto")
    payload = payload_for(rows)
    specs = []
    for name, chunk in (("dep1", plan[0]), ("dep2", plan[1])):
        data = payload(chunk)
        specs.append({"name": name, "data": data, "schema": ex.response_schema(data), "output": ex.chunk_output(chunk), "packet": mode})
    patterned = dict(specs[-1], name="dep2-pattern")
    patterned["schema"] = _pattern_schema(specs[-1]["schema"], [r["id"] for r in specs[-1]["data"]["sources"]])
    specs.append(patterned)
    for name, group in (("full-dep", "depressive"), ("full-elev", "elevated")):
        chunk = ex.group_tasks(reg, group)[:3]
        data = {"sources": full_rows, "requested": ex.requested(reg, chunk)}
        specs.append({"name": name, "data": data, "schema": ex.response_schema(data), "output": ex.chunk_output(chunk), "packet": "full", "min_context": 6144})
    return specs


def replay(out: Path, names: list[str]) -> list[dict]:
    _env()
    from services.note_generation.storage import memory_slot

    from ..prompts import EXTRACT_DSM
    out.mkdir(parents=True, exist_ok=True)
    log = CallLog(out / "calls.jsonl", run_id=time.strftime("replay-%Y%m%dT%H%M%S"), pipeline="bench")
    specs_path = out / "replay-specs.json"
    rows, reference = [], {}
    cancel = threading.Event()
    with memory_slot(cancel, timeout=600):
        for name in names:
            s = setting(name)
            runtime = FastRuntime(out / "work", cancel, settings=s, calllog=log)
            runtime.max_requests = 10_000
            try:
                runtime.load("qwen")
                if not specs_path.exists():
                    if s.context != 4096:
                        raise SystemExit("build the replay calls with a 4,096-context setting first (run 'current' first)")
                    specs_path.write_text(json.dumps(build_specs(runtime), ensure_ascii=False))
                specs = json.loads(specs_path.read_text())
                for spec in specs:
                    if spec.get("min_context", 0) > s.context:
                        continue
                    runtime.response_schema = spec["schema"]
                    runtime.prompt_version = "bench-replay"
                    runtime.deadline = time.monotonic() + 1800
                    with log.context(stage="bench", group=f"{name}:{spec['name']}", session_id="roleplay_dsm-s1",
                                     window=f"{name}:{spec['packet']}"):
                        started = time.monotonic()
                        try:
                            value = runtime.generate(EXTRACT_DSM, spec["data"], spec["output"])
                            content = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                            error = None
                        except Exception as exc:  # report and continue with the next call
                            content, error = None, f"{type(exc).__name__}: {str(exc)[:200]}"
                    row = {**log.rows[-1], "setting": name, "call": spec["name"], "wall_s": round(time.monotonic() - started, 2),
                           "output_sha256": content, "error": error}
                    if name == names[0] or spec["name"] not in reference:
                        reference.setdefault(spec["name"], content)
                    row["identical_to_first_setting"] = content == reference.get(spec["name"])
                    row["vram_peak_mib"] = (runtime.metrics.get("vram") or {}).get("peak_sampled_mib")
                    rows.append(row)
                    pf = (row["prefill_tokens"] or 0) / ((row["prefill_ms"] or 1) / 1000)
                    tg = (row["generated_tokens"] or 0) / ((row["generation_ms"] or 1) / 1000)
                    print(f"{name:24s} {spec['name']:13s} prompt={row['prompt_tokens']} cached={row['cache_tokens']} prefill={row['prefill_tokens']} "
                          f"@{pf:.0f}/s gen={row['generated_tokens']} @{tg:.1f}/s wall={row['wall_s']} same={row['identical_to_first_setting']} {error or ''}", flush=True)
            finally:
                runtime.close()
    existing = json.loads((out / "replay.json").read_text()) if (out / "replay.json").exists() else []
    (out / "replay.json").write_text(json.dumps(existing + rows, indent=1))
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["sweep", "replay"])
    parser.add_argument("--out", type=Path, default=ROOT / "data/research/progress-runs/dsm-fast-bench")
    parser.add_argument("--settings", default="current,b512")
    args = parser.parse_args(argv)
    if args.command == "sweep":
        sweep(args.out)
    else:
        replay(args.out, args.settings.split(","))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Benchmark CLI behaviour with a scripted runtime on the fictional mini fixture (no model, no GPU)."""
import json

import pytest

from services.progress.evaluation import benchmark, calibration, report, run_final, subsets
from tests.progress.evaluation.helpers import EDITION, prepared
from tests.progress.fake_runtime import FakeRuntime, scripted
from tests.progress.test_prediction import stage_b

MODEL = {"path": "fake.gguf", "sha256": "f" * 64, "size_bytes": 0}


@pytest.fixture
def env(tmp_path, monkeypatch):
    out = prepared(tmp_path)
    manifests = subsets.select(EDITION, 42, val_n=2, test_n=1, out=tmp_path / "subsets", prepared=out)
    calls = {"a": 0, "b": 0, "payloads": [], "stop_after": None, "cancel": None}
    responder_a = scripted(domains={"low_mood": {"rating": 2, "refs_text": ["low"]}}, items={"phq9:2": {"score": 2, "refs_text": ["low"]}, "phq9:3": {"score": 1, "refs_text": ["four"]}})
    responder_b = stage_b({"mde.depressed_mood": ("met", ["low"])}, label="possible")

    def respond(system, data):
        calls["payloads"].append(json.dumps(data))
        if calls["stop_after"] is not None and calls["a"] + calls["b"] >= calls["stop_after"]:
            calls["cancel"].set()
        if "condition" in data:
            calls["b"] += 1
            return responder_b(system, data)
        calls["a"] += 1
        return responder_a(system, data)

    def factory(folder, cancel):
        rt = FakeRuntime(respond)
        rt.cancel = cancel
        calls["cancel"] = cancel
        return rt
    monkeypatch.setattr(benchmark, "RUNTIME_FACTORY", factory)
    monkeypatch.setattr(calibration, "CAL_DIR", tmp_path / "calibration")
    return {"tmp": tmp_path, "prepared": out, "val": tmp_path / "subsets" / "val-arms-2.json" if (tmp_path / "subsets" / "val-arms-2.json").exists() else tmp_path / "subsets" / manifests[0]["name"].__add__(".json"),
            "test": tmp_path / "subsets" / f"{manifests[1]['name']}.json", "runs": tmp_path / "runs", "calls": calls}


def run(env, arm, run_id, **kw):
    return benchmark.run_subset(env["val"], arm, run_id, runs=env["runs"], prepared=env["prepared"], model=MODEL, install_signals=False, echo=False, **kw)


def test_run_checkpoints_every_session_and_resumes(env):
    assert run(env, "retrieval-current", "rc") == 0
    folder = env["runs"] / "rc"
    checkpoints = sorted(p.name for p in (folder / "sessions").glob("*.json"))
    assert len(checkpoints) == 10 and checkpoints[0] == "mini_a__s1.json"
    c = json.loads((folder / "sessions" / "mini_a__s1.json").read_text())
    assert c["stage_a"]["phq_items"]["2"] == 2 and c["stage_a"]["phq8_total"] == 3 and c["stage_b"]["prediction"] == "possible"
    assert json.loads((folder / "status.json").read_text())["state"] == "completed"
    log = (folder / "run.log").read_text()
    assert "ETA" in log and "I feel empty" not in log                          # progress only, never transcript text
    before = env["calls"]["a"] + env["calls"]["b"]
    assert run(env, "retrieval-current", "rc") == 0                             # rerun = resume; nothing left to do
    assert env["calls"]["a"] + env["calls"]["b"] == before


def test_prompts_never_contain_labels(env):
    run(env, "full-current", "fc")
    joined = "\n".join(env["calls"]["payloads"])
    for leak in ("ground_truth", "overall_score", "severity_band", "phq_score", "hidden_state", "self_report"):
        assert leak not in joined


def test_stage_a_cache_is_reused_across_history_settings(env):
    run(env, "retrieval-current", "rc")
    stage_a_calls = env["calls"]["a"]
    run(env, "retrieval-prior", "rp")
    assert env["calls"]["a"] == stage_a_calls                                   # no new Stage A requests
    c = json.loads((env["runs"] / "rp" / "sessions" / "mini_b__s3.json").read_text())
    assert c["stage_a"]["cache"] == "hit"
    run(env, "full-prior", "fp")
    assert env["calls"]["a"] > stage_a_calls                                     # a different evidence setting computes Stage A


def test_refuses_mixed_configs_and_test_manifests(env):
    run(env, "retrieval-current", "rc")
    with pytest.raises(SystemExit, match="Refusing to mix configurations"):
        run(env, "full-current", "rc")
    with pytest.raises(SystemExit, match="run_final"):
        benchmark.run_subset(env["test"], "retrieval-current", "t", runs=env["runs"], prepared=env["prepared"], model=MODEL, install_signals=False, echo=False)


def test_stop_request_leaves_no_partial_checkpoint_and_resume_finishes(env):
    env["calls"]["stop_after"] = 7                                              # stop inside the second session
    code = run(env, "retrieval-current", "stop")
    folder = env["runs"] / "stop"
    assert code == 130
    done = sorted(p.name for p in (folder / "sessions").glob("*.json"))
    assert done == ["mini_a__s1.json"]
    assert json.loads((folder / "status.json").read_text())["state"] == "stopped"
    cache = list((env["runs"] / "_stage-a").rglob("mini_a__s2.json"))
    assert cache == []                                                          # cancelled Stage A never cached
    assert "stopped at 1/10" in (folder / "run.log").read_text()
    env["calls"]["stop_after"] = None
    assert run(env, "retrieval-current", "stop") == 0
    assert len(list((folder / "sessions").glob("*.json"))) == 10


def test_report_on_partial_and_complete_runs(env):
    env["calls"]["stop_after"] = 30
    run(env, "retrieval-current", "part")
    partial = report.build("part", runs=env["runs"], prepared=env["prepared"], resamples=50, citations=5)
    assert partial["complete"] is False and 0 < partial["sessions_completed"] < 10
    assert "INCOMPLETE RUN" in (env["runs"] / "part" / "report" / "report.md").read_text()
    env["calls"]["stop_after"] = None
    run(env, "retrieval-current", "part")
    full = report.build("part", runs=env["runs"], prepared=env["prepared"], resamples=50, citations=5)
    assert full["complete"] and full["sessions_completed"] == 10 and full["metrics"]["current_mae"]["ci95"]
    assert full["label"] == "Synthetic benchmark (LongCounsel-8): depression track only."
    assert full["baselines"]["global_median"]["value"] == 5                    # mini_t self-reports 7,6,5,4,3
    rows = (env["runs"] / "part" / "report" / "citation_rating.csv").read_text().splitlines()
    assert rows[0].startswith("run_id,trajectory") and rows[1].endswith(",,,")    # rating columns left empty


def test_freeze_and_run_final_guards(env, monkeypatch):
    run(env, "retrieval-prior", "chosen")
    frozen_path = env["tmp"] / "frozen" / "frozen-progress-config.json"
    frozen = benchmark.freeze("chosen", "lowest change MAE", runs=env["runs"], prepared=env["prepared"], frozen_path=frozen_path)
    assert frozen["evidence"] == "retrieval" and frozen["history"] == "prior" and calibration.load_active_mapping() is not None
    with pytest.raises(SystemExit, match="frozen once"):
        benchmark.freeze("chosen", "again", runs=env["runs"], prepared=env["prepared"], frozen_path=frozen_path)
    assert run_final.validate(env["test"], frozen_path)["chosen_run_id"] == "chosen"
    with pytest.raises(SystemExit, match="test subset only"):
        run_final.validate(env["val"], frozen_path)
    data = json.loads(frozen_path.read_text())
    data["code_stage_b"] = {"changed": "x"}
    frozen_path.write_text(json.dumps(data))
    with pytest.raises(SystemExit, match="code changed"):
        run_final.validate(env["test"], frozen_path)


def test_estimate_from_pilot_timings():
    timings = {"model_load_seconds": 36, "stage_a": [{"evidence": "full", "seconds": 120}, {"evidence": "retrieval", "seconds": 60}],
               "stage_b": [{"history": "current", "seconds": 30}, {"history": "prior", "seconds": 40}]}
    rows = benchmark.estimate(timings)
    assert rows[1]["hours"] == round((36 + 60 * 90) / 3600, 2)                  # retrieval-current: 60 x (A_ret + B_cur)
    assert rows[2]["hours"] == round((36 + 60 * 40) / 3600, 2)                  # retrieval-prior reuses Stage A
    assert rows[-1]["hours_full"] == round((36 + 185 * 160) / 3600, 2)


def test_extract_prompt_version_is_part_of_the_run_config(env, monkeypatch):
    run(env, "retrieval-current", "pv")
    config = json.loads((env["runs"] / "pv" / "config.json").read_text())
    assert config["extract_prompt"] == "v2"
    monkeypatch.setenv("PROGRESS_EXTRACT_PROMPT", "v1")
    with pytest.raises(SystemExit, match="extract_prompt"):
        run(env, "retrieval-current", "pv")                                  # v1 and v2 never mix in one run-id

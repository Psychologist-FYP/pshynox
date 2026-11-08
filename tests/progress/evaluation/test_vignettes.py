"""Vignette set: schema validation, a scripted run (no model) and the report, on the one fictional fixture."""
import csv
import json
import shutil
from pathlib import Path

import pytest

from services.progress.evaluation import benchmark, vignettes
from tests.progress.fake_runtime import FakeRuntime, scripted
from tests.progress.test_prediction import stage_b

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "vignettes_mini"
CASE = "fx-worry-001"
MODEL = {"path": "fake.gguf", "sha256": "f" * 64, "size_bytes": 0}


@pytest.fixture
def root(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "vignettes")
    return tmp_path / "vignettes"


def edit(path: Path, change):
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_fixture_validates_and_reports_the_case_mix(root):
    summary = vignettes.validate(root)
    assert summary["cases"] == 1 and summary["sessions"] == 2
    assert summary["condition_mix"]["gad"]["present"] == 1 and summary["condition_mix"]["bipolar"]["absent"] == 1


@pytest.mark.parametrize("target,change,message", [
    ("sessions/s1.json", lambda d: d["turns"][1].update(phq_score=2), "phq_score"),                         # label leaking into a transcript
    ("sessions/s2.json", lambda d: d.update(session_index=3), "session_index"),
    ("sessions/s1.json", lambda d: d["turns"][0].update(speaker="Counselor"), "speaker"),
    ("labels.json", lambda d: d["conditions"]["gad"]["criteria"].update({"mde.sleep": "met"}), "does not belong"),
    ("labels.json", lambda d: d["conditions"].update(bulimia={"status": "absent"}), "unknown or disabled condition"),
    ("labels.json", lambda d: d["conditions"]["bipolar"].update(subtype_key="bipolar_i_pattern"), "needs status present"),
    ("labels.json", lambda d: d["session_domains"]["1"].update(worry=4), "0-3"),
    ("labels.json", lambda d: d["session_domains"]["1"].update(mood=1), "unknown domain"),
    ("labels.json", lambda d: d.update(blind_to_model_output=False), "blind_to_model_output"),
    ("case.json", lambda d: d.update(case_id="other-case"), "folder name"),
])
def test_invalid_cases_are_rejected_with_a_reason(root, target, change, message):
    edit(root / CASE / target, change)
    with pytest.raises(ValueError, match=message):
        vignettes.validate(root)


@pytest.fixture
def env(root, tmp_path, monkeypatch):
    calls = {"a": 0, "b": 0, "payloads": []}
    responder_a = scripted(domains={"worry": {"rating": 3, "refs_text": ["worry about everything"]},
                                    "restlessness_tension": {"rating": 2, "refs_text": ["shoulders ache"]}})
    responder_b = stage_b({"gad.excessive_worry": ("met", ["worry about everything"]), "gad.muscle_tension": ("met", ["shoulders ache"]),
                           "mde.depressed_mood": ("not_met", [])}, label="possible", only="gad")

    def respond(system, data):
        calls["payloads"].append(json.dumps(data))
        if "condition" in data:
            calls["b"] += 1
            return responder_b(system, data)
        calls["a"] += 1
        return responder_a(system, data)

    def factory(folder, cancel):
        rt = FakeRuntime(respond)
        rt.cancel = cancel
        return rt
    monkeypatch.setattr(benchmark, "RUNTIME_FACTORY", factory)
    return {"root": root, "runs": tmp_path / "runs", "calls": calls}


def run(env, run_id="v1"):
    return vignettes.run(run_id, root=env["root"], runs=env["runs"], model=MODEL, install_signals=False, echo=False)


def test_run_checkpoints_cases_never_sends_labels_and_resumes(env):
    assert run(env) == 0
    checkpoint = json.loads((env["runs"] / "v1" / "cases" / f"{CASE}.json").read_text())
    assert [s["session_index"] for s in checkpoint["sessions"]] == [1, 2]
    assert set(checkpoint["predictions"]) == {"depressive_episode", "bipolar", "gad", "social_anxiety"}
    assert checkpoint["predictions"]["gad"]["prediction"] == "possible"
    joined = "\n".join(env["calls"]["payloads"])
    for leak in ("labelled_by", "blind_to_model_output", "session_domains", "test fixture (not a consultant)", "Fictional fixture"):
        assert leak not in joined
    before = env["calls"]["a"] + env["calls"]["b"]
    assert run(env) == 0                                    # rerun = resume; nothing left
    assert env["calls"]["a"] + env["calls"]["b"] == before
    edit(env["root"] / CASE / "sessions" / "s2.json", lambda d: d["turns"][3].update(text="Changed after the run."))
    with pytest.raises(SystemExit, match="Refusing to mix configurations"):
        run(env)                                            # edited sessions need a new run-id


def test_report_scores_conditions_criteria_domains_and_exports_citations(env):
    run(env)
    r = vignettes.report("v1", root=env["root"], runs=env["runs"], resamples=50)
    assert r["complete"] and r["cases_completed"] == 1
    gad = r["conditions"]["gad"]["at_possible"]
    assert (gad["tp"], gad["fn"], gad["sensitivity"]) == (1, 0, 1.0)
    assert r["conditions"]["gad"]["at_likely"]["fn"] == 1            # 'possible' is not positive at the likely threshold
    assert r["conditions"]["bipolar"]["at_possible"]["tn"] == 1
    crit = r["criteria"]
    assert crit["confusion"]["met"]["met"] == 2                      # excessive worry, muscle tension
    assert crit["confusion"]["met"]["insufficient_evidence"] == 3    # difficult to control, sleep, impairment
    assert r["domains"]["n_both_rated"] == 2 and r["domains"]["exact"] == 1.0
    assert r["domains"]["model_abstained_when_rated"] == pytest.approx(4 / 6)   # 6 integer labels, 2 rated by the model
    rows = list(csv.DictReader((env["runs"] / "v1" / "report" / "citation_rating.csv").open()))
    assert rows and all(row["supports_claim (yes/partly/no)"] == "" for row in rows)
    assert "Vignette report" in (env["runs"] / "v1" / "report" / "report.md").read_text()
    # Sessions edited after the run are excluded from the report, not mixed in.
    edit(env["root"] / CASE / "sessions" / "s1.json", lambda d: d["turns"][1].update(text="Edited."))
    again = vignettes.report("v1", root=env["root"], runs=env["runs"], resamples=10, citations=0)
    assert again["cases_changed_since_run"] == [CASE] and not again["complete"]


def test_binary_metrics_and_kappa_by_hand():
    m = vignettes.binary_metrics([(True, True), (True, False), (False, False), (False, True), (False, False)])
    assert (m["tp"], m["fn"], m["tn"], m["fp"]) == (1, 1, 2, 1)
    assert m["sensitivity"] == 0.5 and m["specificity"] == pytest.approx(2 / 3) and m["f1"] == pytest.approx(0.5)
    assert vignettes.cohen_kappa([(True, True), (False, False)]) == 1.0
    assert vignettes.cohen_kappa([(True, False), (False, True)]) == -1.0
    assert vignettes.binary_metrics([])["sensitivity"] is None


def test_research_api_lists_vignette_runs_and_serves_reports(env, monkeypatch):
    from fastapi.testclient import TestClient
    from services.api.main import app
    monkeypatch.setattr(vignettes, "RUNS", env["runs"])
    run(env)
    with TestClient(app) as client:
        listed = client.get("/api/v1/research/progress/vignette-runs").json()
        assert listed == [{"run_id": "v1", "state": "completed", "completed": 1, "total": 1, "has_report": False}]
        assert client.get("/api/v1/research/progress/vignette-runs/v1/report").status_code == 404
        vignettes.report("v1", root=env["root"], runs=env["runs"], resamples=10, citations=0)
        body = client.get("/api/v1/research/progress/vignette-runs/v1/report").json()
        assert body["conditions"]["gad"]["at_possible"]["tp"] == 1
        assert client.get("/api/v1/research/progress/vignette-runs/..%2Fx/report").status_code == 404

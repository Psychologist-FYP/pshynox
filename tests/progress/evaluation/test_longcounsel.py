import json

import pytest

from services.progress.evaluation import longcounsel, subsets
from tests.progress.evaluation.helpers import EDITION, build_raw, prepared


def test_prepare_verifies_hashes_and_applies_leakage_guard(tmp_path):
    out = prepared(tmp_path)
    manifest = json.loads((out / EDITION / "manifest.json").read_text())
    assert manifest["trajectories"] == 4 and manifest["splits"] == {"train": 1, "val": 2, "test": 1}
    assert manifest["leakage_guard"]["dropped_fields"] == {"phq_score": 1, "trend": 1, "hidden_state": 1}
    assert all(v["sha256"] and v["bytes"] for v in manifest["source_files"].values())
    for path in (out / EDITION / "transcripts").glob("*.json"):
        text = path.read_text()
        for forbidden in ("phq_score", "trend", "hidden_state", "severity_band", "overall_score", "ground_truth"):
            assert forbidden not in text
    t = longcounsel.load_transcript("mini_a", EDITION, out)
    s1 = t["sessions"][0]["segments"]
    assert [s["speaker_role"] for s in s1[:2]] == ["CLINICIAN", "CLIENT"]
    assert [(s["start_ms"], s["end_ms"]) for s in s1[:3]] == [(0, 9000), (10000, 19000), (20000, 29000)]
    assert set(s1[0]) == {"id", "start_ms", "end_ms", "speaker_role", "text", "revision", "exchange_index"}
    labels = longcounsel.load_labels("mini_a", EDITION, out)
    assert [g["total"] for g in labels["ground_truth"]] == [12, 10, 8, 6, 4] and labels["ground_truth"][0]["items"]["1"] == 1.5


def test_prepare_rejects_checksum_mismatch_and_missing_licence(tmp_path):
    raw = build_raw(tmp_path)
    manifest = json.loads((raw / "manifest.json").read_text())
    manifest["datasets"][EDITION]["archive_sha256"] = "0" * 64
    (raw / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Checksum mismatch"):
        longcounsel.prepare(EDITION, raw, tmp_path / "out")
    (raw / "LICENSE").unlink()
    with pytest.raises(FileNotFoundError):
        longcounsel.prepare(EDITION, raw, tmp_path / "out")


def test_questionnaire_terms_are_counted(tmp_path):
    report = {"dropped_fields": {}, "questionnaire_term_rows": 0, "questionnaire_terms": {}}
    raw = b'{"session_id":1,"turn_id":1,"speaker":"Counselor","text":"On a scale of one to ten, how low?","message_id":"m1"}\n'
    longcounsel.clean_transcript(raw, "r", report)
    assert report["questionnaire_term_rows"] == 1 and report["questionnaire_terms"] == {"scale": 1}


def test_subsets_are_deterministic_stratified_and_never_overwritten(tmp_path):
    out = prepared(tmp_path)
    first = subsets.select(EDITION, 42, val_n=2, test_n=1, out=tmp_path / "s1", prepared=out)
    second = subsets.select(EDITION, 42, val_n=2, test_n=1, out=tmp_path / "s2", prepared=out)
    assert [m["run_ids"] for m in first] == [m["run_ids"] for m in second]
    val, test = first
    assert val["split"] == "val" and set(val["run_ids"]) == {"mini_a", "mini_b"} and test["run_ids"] == ["mini_c"]
    assert val["trajectory_types"] == {"mini_a": "decreasing", "mini_b": "fluctuating"}
    assert val["large_change_transitions"] == 4                                 # mini_b: +6, -5, +7, -6
    with pytest.raises(FileExistsError):
        subsets.select(EDITION, 42, val_n=2, test_n=1, out=tmp_path / "s1", prepared=out)
    assert subsets.allocate({"a": ["1"] * 6, "b": ["2"] * 3, "c": ["3"]}, 5) == {"a": 3, "b": 2, "c": 0}

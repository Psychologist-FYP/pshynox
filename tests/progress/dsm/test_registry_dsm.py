"""Registry v2 validation, the v1 mapping, instrument terms and the benchmark guard."""
import ast
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from services.progress.dsm.registry import DSM_DIR, MAX_PARAPHRASE_WORDS, load_dsm_registry, word_count
from services.progress.registry import registry as v1_registry

from .helpers import REG

ROOT = Path(__file__).resolve().parents[3]


def test_ids_are_unique_and_follow_the_dsm_pattern():
    ids = [i.id for i in REG.items.items]
    assert len(ids) == len(set(ids)) == 93
    assert all(re.fullmatch(r"[A-Z0-9]+\.[A-Z0-9.]+", i) for i in ids)
    assert {"MDE.A4", "MANIA.B2", "GAD.C6", "SAD.A", "HYPO.F", "BP2.D", "PDD.B6"} <= set(ids)


def test_every_paraphrase_is_at_most_twenty_words():
    long = [(i.id, word_count(i.paraphrase)) for i in REG.items.items if word_count(i.paraphrase) > MAX_PARAPHRASE_WORDS]
    assert long == []


def test_a_long_paraphrase_is_rejected(tmp_path):
    for name in DSM_DIR.glob("*.json"):
        (tmp_path / name.name).write_text(name.read_text())
    data = json.loads((tmp_path / "items.json").read_text())
    data["items"][0]["paraphrase"] = " ".join(["word"] * 21)
    (tmp_path / "items.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="exceeds 20 words"):
        load_dsm_registry(tmp_path)


def test_no_known_verbatim_dsm_phrases():
    # A small tripwire for copied wording; paraphrases are written in our own words.
    banned = ["markedly diminished interest or pleasure in all, or almost all", "apprehensive expectation", "abnormally and persistently elevated",
              "flight of ideas or subjective experience", "clinically significant distress or impairment in social, occupational"]
    text = json.dumps(json.loads((DSM_DIR / "items.json").read_text())).lower()
    assert [b for b in banned if b in text] == []


def test_verification_fields_on_every_item_and_condition_all_pending():
    assert all(i.verification.status == "pending" for i in REG.items.items)
    assert all(c.verification.status == "pending" for c in REG.conditions.conditions)


def test_rules_reference_existing_items_and_icd_codes_present():
    ids = {i.id for i in REG.items.items}
    for cond in REG.conditions.conditions:
        assert cond.icd10cm and cond.icd11 and cond.chapter
        for el in cond.elements:
            assert set(([el.item] if el.item else []) + el.items) <= ids


def test_cross_check_rejects_an_unknown_item(tmp_path):
    for name in DSM_DIR.glob("*.json"):
        (tmp_path / name.name).write_text(name.read_text())
    data = json.loads((tmp_path / "conditions.json").read_text())
    data["conditions"][0]["elements"][0]["items"].append("MDE.Z9")
    (tmp_path / "conditions.json").write_text(json.dumps(data))
    with pytest.raises(ValueError, match="unknown items"):
        load_dsm_registry(tmp_path)


def test_shared_and_separated_features():
    concentration = {i.id for i in REG.items_for_feature("feat.concentration")}
    assert concentration == {"MDE.A8", "PDD.B5", "GAD.C3", "ANXD.3"}
    sleep = {f: {i.id for i in REG.items_for_feature(f)} for f in ("feat.sleep_insomnia_hypersomnia", "feat.reduced_sleep_need", "feat.sleep_worry")}
    assert sleep == {"feat.sleep_insomnia_hypersomnia": {"MDE.A4", "PDD.B2"}, "feat.reduced_sleep_need": {"MANIA.B2", "HYPO.B2"},
                     "feat.sleep_worry": {"GAD.C6"}}
    reasons = {s.feature for s in REG.feature("feat.sleep_insomnia_hypersomnia").separate_from}
    assert reasons == {"feat.reduced_sleep_need", "feat.sleep_worry"}
    assert "SAD.A" not in {i.id for i in REG.items_for_feature("feat.social_avoidance")}
    for n in range(1, 8):
        assert REG.item(f"HYPO.B{n}").same_as == f"MANIA.B{n}"


def test_v1_mapping_covers_every_v1_domain():
    v1 = {d.key for d in v1_registry().domains.domains}
    assert set(REG.v1_mapping["domains"]) == v1
    for row in REG.v1_mapping["domains"].values():
        assert row["items"] or row.get("impairment")


def test_instrument_map_and_clinician_only_item_9():
    phq = REG.instrument_map["maps"]["phq9"]
    assert set(phq) == {str(i) for i in range(1, 10)} and phq["1"] == ["MDE.A2"] and phq["2"] == ["MDE.A1"] and phq["9"] == ["MDE.A9"]
    assert set(REG.instrument_map["maps"]["gad7"]) == {str(i) for i in range(1, 8)}
    assert v1_registry().instruments.get("phq9").item("9").clinician_only


def test_new_instruments_are_clinician_only_and_licence_confirmed():
    keys = {i.key for i in REG.instruments}
    assert keys == {"whodas12", "ccsm1", "asrm"}
    for inst in REG.instruments:
        assert inst.licence_confirmed and inst.ai_estimable_items == [] and all(i.clinician_only for i in inst.items)
    assert next(i for i in REG.instruments if i.key == "whodas12").total_max == 48


def test_excluded_conditions_are_not_defined():
    assert "bulimia_nervosa" in REG.conditions.excluded_by_proposal
    assert not {c.key for c in REG.conditions.conditions} & set(REG.conditions.excluded_by_proposal)


# ---------------------------------------------------------------- benchmark guard

def test_benchmark_bound_files_are_unchanged():
    pins = json.loads((Path(__file__).parent / "benchmark_hashes.json").read_text())["files"]
    from services.progress.evaluation.benchmark import STAGE_A_FILES, STAGE_B_FILES
    assert set(pins) == set(STAGE_A_FILES + STAGE_B_FILES)
    changed = [f for f, h in pins.items() if hashlib.sha256((ROOT / f).read_bytes()).hexdigest() != h]
    assert changed == [], f"benchmark-bound files changed: {changed}. These are pinned to the val12 runs; ask before changing them."


@pytest.mark.parametrize("module", ["benchmark.py", "run_final.py", "longcounsel.py", "subsets.py", "metrics.py", "report.py", "calibration.py"])
def test_benchmark_code_never_imports_the_dsm_pipeline(module):
    tree = ast.parse((ROOT / "services/progress/evaluation" / module).read_text())
    names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [n for n in names if "dsm" in n]

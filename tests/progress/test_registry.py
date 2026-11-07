import json

import pytest

from services.api.schemas import AI_FORBIDDEN_ITEMS
from services.progress.registry import load_registry, registry
from services.progress.registry.loader import REGISTRY_DIR


def test_registry_loads_with_versions_and_hashes():
    reg = registry()
    assert set(reg.versions) == {"instruments", "domains", "conditions"}
    assert all(len(v.sha256) == 64 and v.tag.startswith(f"{v.kind}@") for v in reg.versions.values())
    assert [c.key for c in reg.conditions.enabled] == ["depressive_episode", "bipolar", "gad", "social_anxiety"]
    assert "bulimia_nervosa" not in {c.key for c in reg.conditions.conditions}
    assert len(reg.domains.domains) == 20


def test_conditions_cite_source_and_pending_verification():
    for condition in registry().conditions.conditions:
        assert condition.source.startswith("DSM-5-TR")
        assert "pending clinical consultant verification" in condition.verification
        assert all(len(c.summary) <= 300 for c in condition.criteria)


def test_phq_item_9_is_clinician_only_and_consistent_with_contract():
    phq = registry().instruments.get("phq9")
    assert phq.item("9").clinician_only and "9" not in phq.ai_estimable_items
    forbidden = {(i.key, item.id) for i in registry().instruments.instruments for item in i.items if item.clinician_only}
    assert forbidden == set(AI_FORBIDDEN_ITEMS)
    assert phq.reliable_change_threshold == 5
    assert registry().instruments.get("gad7").reliable_change_threshold is None


def test_domain_mapping_feeds_instruments_and_criteria():
    reg = registry()
    phq = reg.instruments.get("phq9")
    for item in phq.items:
        if item.domain:
            assert reg.domains.get(item.domain).phq_item == item.id
    criteria = {c.id for cond in reg.conditions.conditions for c in cond.criteria}
    assert all(set(d.criteria) <= criteria for d in reg.domains.domains)


def test_changed_registry_changes_hash_and_invalid_registry_is_rejected(tmp_path):
    for name in ("instruments", "domains", "conditions"):
        (tmp_path / f"{name}.json").write_text((REGISTRY_DIR / f"{name}.json").read_text())
    base = load_registry(tmp_path).versions["domains"].sha256
    data = json.loads((tmp_path / "domains.json").read_text())
    data["domains"][0]["label"] = "Changed"
    (tmp_path / "domains.json").write_text(json.dumps(data))
    assert load_registry(tmp_path).versions["domains"].sha256 != base
    data["domains"][0]["criteria"] = ["unknown.criterion"]
    (tmp_path / "domains.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_registry(tmp_path)
    conditions = json.loads((tmp_path / "conditions.json").read_text())
    conditions["enabled_conditions"].append("bulimia_nervosa")
    (tmp_path / "domains.json").write_text((REGISTRY_DIR / "domains.json").read_text())
    (tmp_path / "conditions.json").write_text(json.dumps(conditions))
    with pytest.raises(ValueError):
        load_registry(tmp_path)

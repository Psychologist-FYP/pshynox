"""Load and check the fast-pipeline config (``config.json``)."""
from __future__ import annotations

import hashlib
import json
import os
from functools import lru_cache
from pathlib import Path

from ..registry import dsm_registry

PATH = Path(__file__).with_name("config.json")


@lru_cache(maxsize=1)
def fast_config() -> dict:
    cfg = json.loads(PATH.read_text())
    reg = dsm_registry()
    keys = [f["key"] for f in cfg["features"]]
    codes = [f["code"] for f in cfg["features"]]
    registry_keys = [f.key for f in reg.features.features]
    if sorted(keys) != sorted(registry_keys) or len(set(keys)) != len(keys):
        raise ValueError("dsm-fast config must cover every registry feature exactly once")
    if len(set(codes)) != len(codes) or any(not c.isalpha() or len(c) != 2 for c in codes):
        raise ValueError("dsm-fast feature codes must be unique two-letter codes")
    grouped = [g for grp in cfg["groups"] for g in grp["registry_groups"]]
    if sorted(grouped) != sorted({f.group for f in reg.features.features}):
        raise ValueError("dsm-fast groups must cover every registry group exactly once")
    if set(cfg["contexts"].values()) != set(reg.features.contexts):
        raise ValueError("dsm-fast context codes must match the registry contexts")
    if set(cfg["risk_codes"].values()) != {r["key"] for r in reg.risk["flags"] + reg.risk["supporting"]}:
        raise ValueError("dsm-fast risk codes must match the registry risk categories")
    cfg["sha256"] = hashlib.sha256(PATH.read_bytes()).hexdigest()
    cfg["by_code"] = {f["code"]: f for f in cfg["features"]}
    cfg["by_key"] = {f["key"]: f for f in cfg["features"]}
    return cfg


def group_features(group: str) -> list[dict]:
    cfg, reg = fast_config(), dsm_registry()
    wanted = next(g for g in cfg["groups"] if g["key"] == group)["registry_groups"]
    return [f for f in cfg["features"] if reg.feature(f["key"]).group in wanted]


def dsm_pipeline() -> str:
    """PROGRESS_DSM_PIPELINE=dsm-v1|dsm-fast for the clinical app (registry v2 only). The benchmark never reads it."""
    value = os.getenv("PROGRESS_DSM_PIPELINE", "dsm-v1").strip().lower()
    if value not in {"dsm-v1", "dsm-fast"}:
        raise ValueError("PROGRESS_DSM_PIPELINE must be dsm-v1 or dsm-fast")
    return value

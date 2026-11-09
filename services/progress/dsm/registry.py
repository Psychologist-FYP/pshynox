"""Loader for registry v2 (``services/progress/registry/dsm/*.json``), validated by Pydantic with cross-checks.

Every criterion item, feature and condition gets a content SHA-256; clinician verification decisions are bound to it,
so editing a paraphrase returns that entry to "pending".
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.progress.registry.loader import Instrument

DSM_DIR = Path(__file__).resolve().parents[1] / "registry" / "dsm"
MAX_PARAPHRASE_WORDS = 20
STATUSES = ("present", "subthreshold", "absent", "explicit_denial", "insufficient_evidence", "conflicting")
EXCLUSION_STATUSES = ("cause_evidence", "ruled_out_cited", "not_assessed")
LABELS = ("likely", "possible", "insufficient_evidence", "not_met")
ItemKind = Literal["symptom", "stem", "impairment", "exclusion", "structural", "specifier", "judgement"]
ElementType = Literal["item", "count", "duration", "exclusion", "judgement", "absence", "episode", "all_episodes",
                      "never_episode", "subthreshold_course", "note"]


def registry_kind() -> str:
    """PROGRESS_REGISTRY=v1|dsm. The clinical default is dsm (since Phase 3, 9 Nov 2026); the benchmark never reads this."""
    value = os.getenv("PROGRESS_REGISTRY", "dsm").strip().lower()
    if value not in {"v1", "dsm"}:
        raise ValueError("PROGRESS_REGISTRY must be v1 or dsm")
    return value


def word_count(text: str) -> int:
    return len([t for t in text.split() if re.search(r"\w", t)])


def content_sha256(value: dict) -> str:
    """Hash of an entry's clinical content: the verification block itself is excluded."""
    body = {k: v for k, v in value.items() if k != "verification"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Verification(_Model):
    status: Literal["pending", "verified", "changed"]
    verified_by: str | None = None
    verified_on: str | None = None
    comment: str = ""


class Leaf(_Model):
    feature: str
    context: list[str] = Field(default_factory=list)
    degree: Literal["marked"] | None = None


class AnyOf(_Model):
    any_of: list["Expr"]


class AllOf(_Model):
    all_of: list["Expr"]


Expr = Union[Leaf, AnyOf, AllOf]
AnyOf.model_rebuild()
AllOf.model_rebuild()


def leaves(expr: Expr | None) -> list[Leaf]:
    if expr is None:
        return []
    if isinstance(expr, Leaf):
        return [expr]
    return [leaf for child in (expr.any_of if isinstance(expr, AnyOf) else expr.all_of) for leaf in leaves(child)]


class Item(_Model):
    id: str
    disorder: str
    criterion: str
    label: str
    paraphrase: str
    chapter: str
    kind: ItemKind
    model_assessed: bool = True
    satisfied_by: Expr | None = None
    notes: str = ""
    same_as: str | None = None
    verification: Verification

    @model_validator(mode="after")
    def check(self):
        if self.id != f"{self.disorder}.{self.criterion}":
            raise ValueError(f"{self.id}: id must be <DISORDER>.<CRITERION>")
        if word_count(self.paraphrase) > MAX_PARAPHRASE_WORDS:
            raise ValueError(f"{self.id}: paraphrase exceeds {MAX_PARAPHRASE_WORDS} words")
        return self


class SeparateFrom(_Model):
    feature: str
    reason: str


class Feature(_Model):
    key: str
    label: str
    group: str
    kind: Literal["symptom", "impairment", "exclusion"] = "symptom"
    definition: str
    queries: list[str]
    counter_queries: list[str]
    qualifiers: list[str] = Field(default_factory=list)
    degree: bool = False
    separate_from: list[SeparateFrom] = Field(default_factory=list)


class Group(_Model):
    key: str
    label: str


class FeatureRegistry(_Model):
    kind: Literal["dsm_features"]
    version: str
    source: str
    statuses: list[str]
    exclusion_statuses: list[str]
    frequencies: list[str]
    contexts: list[str]
    groups: list[Group]
    features: list[Feature]


class Element(_Model):
    id: str
    type: ElementType
    ask: str = ""
    label: str | None = None
    item: str | None = None
    items: list[str] = Field(default_factory=list)
    min: int | None = None
    must_include_any: list[str] = Field(default_factory=list)
    min_if_only: dict | None = None
    min_days: int | None = None
    hospitalised_any: bool = False
    features: list[str] = Field(default_factory=list)
    condition: str | None = None
    conditions: list[str] = Field(default_factory=list)
    groups: list[str] = Field(default_factory=list)
    on_present: Literal["not_met", "possible"] | None = None


class Differential(_Model):
    label: str
    why: str


class Condition(_Model):
    key: str
    label: str
    role: Literal["episode", "target", "differential"]
    chapter: str
    icd10cm: str
    icd11: str
    core: str
    per_period: bool
    group: str | None = None
    elements: list[Element]
    specifiers: list[str] = Field(default_factory=list)
    differentials: list[Differential] = Field(default_factory=list)
    verification: Verification

    def element(self, element_id: str) -> Element:
        return next(e for e in self.elements if e.id == element_id)


class ConditionRegistry(_Model):
    kind: Literal["dsm_conditions"]
    version: str
    source: str
    enabled_conditions: list[str]
    differential_conditions: list[str]
    episode_conditions: list[str]
    excluded_by_proposal: list[str]
    label_footer: str
    conditions: list[Condition]


class ItemRegistry(_Model):
    kind: Literal["dsm_items"]
    version: str
    source: str
    items: list[Item]


class DsmRegistry(BaseModel):
    items: ItemRegistry
    features: FeatureRegistry
    conditions: ConditionRegistry
    instruments: list[Instrument]
    instrument_meta: dict
    instrument_map: dict
    v1_mapping: dict
    risk: dict
    sha256: str
    version: str

    @property
    def tag(self) -> str:
        return f"dsm@{self.version}+{self.sha256[:8]}"

    def item(self, item_id: str) -> Item:
        return self._items[item_id]

    def feature(self, key: str) -> Feature:
        return self._features[key]

    def condition(self, key: str) -> Condition:
        return self._conditions[key]

    def model_post_init(self, _ctx) -> None:
        object.__setattr__(self, "_items", {i.id: i for i in self.items.items})
        object.__setattr__(self, "_features", {f.key: f for f in self.features.features})
        object.__setattr__(self, "_conditions", {c.key: c for c in self.conditions.conditions})

    def items_for_feature(self, key: str) -> list[Item]:
        return [i for i in self.items.items if any(leaf.feature == key for leaf in leaves(i.satisfied_by))]

    def features_for_items(self, item_ids: list[str]) -> list[str]:
        out: list[str] = []
        for item_id in item_ids:
            for leaf in leaves(self.item(item_id).satisfied_by):
                if leaf.feature not in out:
                    out.append(leaf.feature)
        return out

    def condition_features(self, key: str) -> list[str]:
        """Every feature a condition's rule can read (items, exclusions, irritable-only checks), in registry order."""
        cond = self.condition(key)
        wanted: set[str] = set()
        for e in cond.elements:
            ids = ([e.item] if e.item else []) + e.items
            wanted |= set(self.features_for_items([i for i in ids if i in self._items]))
            wanted |= set(e.features)
            if e.min_if_only:
                wanted |= {e.min_if_only["present"], e.min_if_only["absent"]}
            if e.type == "subthreshold_course":
                wanted |= {f.key for f in self.features.features if f.group in e.groups}
        return [f.key for f in self.features.features if f.key in wanted]

    def entries(self) -> list[tuple[str, str, dict]]:
        """(entity_kind, id, raw json) for verification export, in display order."""
        raw = self._raw
        return ([("item", i["id"], i) for i in raw["items"]["items"]] + [("condition", c["key"], c) for c in raw["conditions"]["conditions"]]
                + [("feature", f["key"], f) for f in raw["features"]["features"]])

    def content_hash(self, entity_kind: str, entity_id: str) -> str:
        return next(content_sha256(v) for k, i, v in self.entries() if k == entity_kind and i == entity_id)


def _check(reg: DsmRegistry) -> None:
    item_ids = [i.id for i in reg.items.items]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("Duplicate criterion item ids")
    feature_keys = [f.key for f in reg.features.features]
    if len(feature_keys) != len(set(feature_keys)):
        raise ValueError("Duplicate feature keys")
    groups = {g.key for g in reg.features.groups}
    contexts = set(reg.features.contexts)
    for feat in reg.features.features:
        if feat.group not in groups:
            raise ValueError(f"{feat.key}: unknown group {feat.group}")
        for sep in feat.separate_from:
            if sep.feature not in feature_keys:
                raise ValueError(f"{feat.key}: separate_from references unknown feature {sep.feature}")
    for item in reg.items.items:
        for leaf in leaves(item.satisfied_by):
            if leaf.feature not in feature_keys:
                raise ValueError(f"{item.id}: satisfied_by references unknown feature {leaf.feature}")
            if set(leaf.context) - contexts:
                raise ValueError(f"{item.id}: unknown context {set(leaf.context) - contexts}")
            if leaf.degree and not reg.feature(leaf.feature).degree:
                raise ValueError(f"{item.id}: {leaf.feature} has no degree")
        if item.same_as:
            twin = reg.item(item.same_as)
            if twin.paraphrase != item.paraphrase or [l.feature for l in leaves(twin.satisfied_by)] != [l.feature for l in leaves(item.satisfied_by)]:
                raise ValueError(f"{item.id}: same_as {item.same_as} must share paraphrase and features")
        if item.kind in {"symptom"} and item.model_assessed and item.satisfied_by is None:
            raise ValueError(f"{item.id}: a model-assessed symptom needs satisfied_by")
    used = {leaf.feature for i in reg.items.items for leaf in leaves(i.satisfied_by)}
    used |= {k for c in reg.conditions.conditions for e in c.elements for k in e.features}
    unused = set(feature_keys) - used
    if unused:
        raise ValueError(f"Features linked to no criterion: {sorted(unused)}")
    cond_keys = {c.key for c in reg.conditions.conditions}
    for listing in (reg.conditions.enabled_conditions, reg.conditions.differential_conditions, reg.conditions.episode_conditions):
        if set(listing) - cond_keys:
            raise ValueError(f"Unknown conditions listed: {set(listing) - cond_keys}")
    if set(reg.conditions.excluded_by_proposal) & cond_keys:
        raise ValueError("A condition excluded by the proposal is defined")
    for cond in reg.conditions.conditions:
        ids = [e.id for e in cond.elements]
        if cond.core not in ids:
            raise ValueError(f"{cond.key}: core element {cond.core} is not an element")
        for e in cond.elements:
            refs = ([e.item] if e.item else []) + e.items + e.must_include_any
            if set(refs) - set(item_ids):
                raise ValueError(f"{cond.key}/{e.id}: unknown items {set(refs) - set(item_ids)}")
            if set(e.features) - set(feature_keys):
                raise ValueError(f"{cond.key}/{e.id}: unknown features")
            linked = ([e.condition] if e.condition else []) + e.conditions
            if set(linked) - cond_keys:
                raise ValueError(f"{cond.key}/{e.id}: unknown conditions {set(linked) - cond_keys}")
            if e.type == "count" and (e.min is None or not e.items or set(e.must_include_any) - set(e.items)):
                raise ValueError(f"{cond.key}/{e.id}: count needs items, min and core items among them")
            if e.type == "duration" and e.min_days is None:
                raise ValueError(f"{cond.key}/{e.id}: duration needs min_days")
            if e.type in {"item", "judgement"} and not e.item:
                raise ValueError(f"{cond.key}/{e.id}: needs an item")
            if e.type in {"exclusion"} and not e.features:
                raise ValueError(f"{cond.key}/{e.id}: exclusion needs features")
            if e.type in {"episode"} and not e.condition:
                raise ValueError(f"{cond.key}/{e.id}: episode needs a condition")
            if e.type in {"never_episode", "all_episodes"} and not e.conditions:
                raise ValueError(f"{cond.key}/{e.id}: needs conditions")
    for inst in reg.instruments:
        if not inst.licence_confirmed:
            raise ValueError(f"{inst.key}: instruments load only after licence confirmation")
    for key, mapping in reg.instrument_map["maps"].items():
        for item_list in mapping.values():
            if set(item_list) - set(item_ids):
                raise ValueError(f"instrument map {key}: unknown items {set(item_list) - set(item_ids)}")
    for domain, row in reg.v1_mapping["domains"].items():
        if set(row["items"]) - set(item_ids):
            raise ValueError(f"v1 mapping {domain}: unknown items")
        if row.get("impairment") and row["impairment"] not in feature_keys:
            raise ValueError(f"v1 mapping {domain}: unknown impairment feature")


def load_dsm_registry(directory: Path | None = None) -> DsmRegistry:
    directory = Path(directory or DSM_DIR)
    names = ("items", "features", "conditions", "instruments", "instrument_map", "v1_mapping", "risk")
    raw = {n: json.loads((directory / f"{n}.json").read_text(encoding="utf-8")) for n in names}
    sha = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    reg = DsmRegistry(items=ItemRegistry.model_validate(raw["items"]), features=FeatureRegistry.model_validate(raw["features"]),
                      conditions=ConditionRegistry.model_validate(raw["conditions"]),
                      instruments=[Instrument.model_validate(i) for i in raw["instruments"]["instruments"]],
                      instrument_meta={k: v for k, v in raw["instruments"].items() if k not in {"instruments", "kind", "version"}},
                      instrument_map=raw["instrument_map"], v1_mapping=raw["v1_mapping"], risk=raw["risk"],
                      sha256=sha, version=raw["items"]["version"])
    object.__setattr__(reg, "_raw", raw)
    _check(reg)
    return reg


@lru_cache(maxsize=1)
def dsm_registry() -> DsmRegistry:
    return load_dsm_registry()

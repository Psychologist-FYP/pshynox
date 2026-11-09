"""Versioned JSON registries for instruments, session domains and target conditions.

Each registry is validated by Pydantic and identified by ``<version>+<sha8>`` where the
hash covers canonical JSON (sorted keys). The tag is recorded on every generated value.
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REGISTRY_DIR = Path(__file__).resolve().parent


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Band(_Model):
    min: int
    max: int
    label: str


class InstrumentItem(_Model):
    id: str
    label: str
    domain: str | None
    clinician_only: bool = False


class Subtotal(_Model):
    key: str
    label: str
    items: list[str]
    max: int
    bands: list[Band]


class Instrument(_Model):
    key: str
    label: str
    licence_confirmed: bool
    licence_note: str
    item_min: int
    item_max: int
    reporting_period: str
    items: list[InstrumentItem]
    ai_estimable_items: list[str]
    total_max: int
    bands: list[Band]
    subtotals: list[Subtotal] = Field(default_factory=list)
    chart_subtotal: str | None = None
    reliable_change_threshold: float | None
    reliable_change_source: str

    @model_validator(mode="after")
    def check(self):
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.key}: duplicate item ids")
        clinician_only = {i.id for i in self.items if i.clinician_only}
        if not set(self.ai_estimable_items) <= set(ids) or set(self.ai_estimable_items) & clinician_only:
            raise ValueError(f"{self.key}: AI-estimable items must exist and exclude clinician-only items")
        if self.total_max != len(ids) * self.item_max:
            raise ValueError(f"{self.key}: total_max does not match items")
        for sub in self.subtotals:
            if not set(sub.items) <= set(ids):
                raise ValueError(f"{self.key}: subtotal {sub.key} references unknown items")
        if self.chart_subtotal and self.chart_subtotal not in {s.key for s in self.subtotals}:
            raise ValueError(f"{self.key}: unknown chart subtotal")
        return self

    def item(self, item_id: str) -> InstrumentItem:
        return next(i for i in self.items if i.id == item_id)

    def band(self, total: float | None, subtotal: str | None = None) -> str | None:
        if total is None:
            return None
        bands = next((s.bands for s in self.subtotals if s.key == subtotal), self.bands) if subtotal else self.bands
        return next((b.label for b in bands if b.min <= total <= b.max), None)


class InstrumentRegistry(_Model):
    kind: Literal["instruments"]
    version: str
    verification: str
    notes: str = ""
    instruments: list[Instrument]
    extension_point: str = ""

    @model_validator(mode="after")
    def licensed(self):
        for instrument in self.instruments:
            if not instrument.licence_confirmed:
                raise ValueError(f"{instrument.key}: instruments load only after licence confirmation")
        return self

    def get(self, key: str) -> Instrument:
        for instrument in self.instruments:
            if instrument.key == key:
                return instrument
        raise KeyError(key)


class DomainGroup(_Model):
    key: str
    label: str


class Domain(_Model):
    key: str
    label: str
    group: str
    directions: list[str]
    phq_item: str | None
    gad7_items: list[str]
    criteria: list[str]
    queries: list[str] = Field(min_length=1)


class DomainRegistry(_Model):
    kind: Literal["domains"]
    version: str
    verification: str
    rating_scale: dict[str, str]
    groups: list[DomainGroup]
    domains: list[Domain]

    @model_validator(mode="after")
    def check(self):
        keys = [d.key for d in self.domains]
        groups = {g.key for g in self.groups}
        if len(keys) != len(set(keys)) or any(d.group not in groups for d in self.domains):
            raise ValueError("Domains must be unique and belong to a declared group")
        return self

    def get(self, key: str) -> Domain:
        for domain in self.domains:
            if domain.key == key:
                return domain
        raise KeyError(key)

    def by_group(self, group: str) -> list[Domain]:
        return [d for d in self.domains if d.group == group]


class Criterion(_Model):
    id: str
    kind: Literal["symptom", "impairment", "episode"]
    core: bool
    summary: str = Field(min_length=1, max_length=300)
    domains: list[str]
    model_assessed: bool = True


class OnlyRule(_Model):
    criterion: str
    min_met: int


class Requirement(_Model):
    id: str
    label: str
    criteria: list[str]
    min_met: int = Field(ge=1)
    min_met_if_only: OnlyRule | None = None
    must_include_any_of: list[str] = Field(default_factory=list)


class Consideration(_Model):
    key: str
    summary: str


class Differential(_Model):
    condition: str
    why: str


class Subtype(_Model):
    key: str
    label: str
    summary: str


class Condition(_Model):
    key: str
    label: str
    source: str
    verification: str
    criteria: list[Criterion]
    requirements: list[Requirement]
    duration_days_min: int
    duration_summary: str
    impairment_required: bool
    rule_outs: list[Consideration]
    differentials: list[Differential]
    retrieval_queries: list[str]
    subtypes: list[Subtype] = Field(default_factory=list)
    specifiers: list[Consideration] = Field(default_factory=list)

    @model_validator(mode="after")
    def check(self):
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.key}: duplicate criterion ids")
        for req in self.requirements:
            referenced = set(req.criteria) | set(req.must_include_any_of)
            if req.min_met_if_only:
                referenced.add(req.min_met_if_only.criterion)
            if not referenced <= set(ids):
                raise ValueError(f"{self.key}: requirement {req.id} references unknown criteria")
            if req.min_met > len(req.criteria):
                raise ValueError(f"{self.key}: requirement {req.id} cannot be satisfied")
        if "pending clinical consultant verification" not in self.verification:
            raise ValueError(f"{self.key}: must be marked pending consultant verification")
        if not self.source.startswith("DSM-5-TR"):
            raise ValueError(f"{self.key}: cite the DSM-5-TR section")
        return self

    def criterion(self, criterion_id: str) -> Criterion:
        return next(c for c in self.criteria if c.id == criterion_id)

    @property
    def assessed_criteria(self) -> list[Criterion]:
        return [c for c in self.criteria if c.model_assessed]

    @property
    def criteria_required(self) -> int:
        return sum(r.min_met for r in self.requirements)


class ConditionRegistry(_Model):
    kind: Literal["conditions"]
    version: str
    verification: str
    notes: str = ""
    enabled_conditions: list[str]
    excluded_by_proposal: list[str] = Field(default_factory=list)
    conditions: list[Condition]

    @model_validator(mode="after")
    def check(self):
        keys = {c.key for c in self.conditions}
        if not set(self.enabled_conditions) <= keys or keys & set(self.excluded_by_proposal):
            raise ValueError("Enabled conditions must be defined and never excluded by the proposal")
        return self

    def get(self, key: str) -> Condition:
        for condition in self.conditions:
            if condition.key == key:
                return condition
        raise KeyError(key)

    @property
    def enabled(self) -> list[Condition]:
        return [self.get(k) for k in self.enabled_conditions]


class RegistryVersion(_Model):
    kind: str
    version: str
    sha256: str

    @property
    def tag(self) -> str:
        return f"{self.kind}@{self.version}+{self.sha256[:8]}"


class Registry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    instruments: InstrumentRegistry
    domains: DomainRegistry
    conditions: ConditionRegistry
    versions: dict[str, RegistryVersion]

    def tag(self, kind: str) -> str:
        return self.versions[kind].tag

    @property
    def combined_tag(self) -> str:
        return ";".join(self.versions[k].tag for k in sorted(self.versions))


def canonical_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _cross_check(registry: Registry) -> None:
    domain_keys = {d.key for d in registry.domains.domains}
    criterion_ids = {c.id for cond in registry.conditions.conditions for c in cond.criteria}
    for domain in registry.domains.domains:
        unknown = set(domain.criteria) - criterion_ids
        if unknown:
            raise ValueError(f"Domain {domain.key} maps to unknown criteria {sorted(unknown)}")
    for cond in registry.conditions.conditions:
        for criterion in cond.criteria:
            if set(criterion.domains) - domain_keys:
                raise ValueError(f"Criterion {criterion.id} references unknown domains")
    for instrument in registry.instruments.instruments:
        for item in instrument.items:
            if item.domain and item.domain not in domain_keys:
                raise ValueError(f"{instrument.key} item {item.id} maps to unknown domain")


def load_registry(directory: Path | None = None) -> Registry:
    directory = Path(directory or REGISTRY_DIR)
    raw, versions = {}, {}
    for kind in ("instruments", "domains", "conditions"):
        path = directory / f"{kind}.json"
        raw[kind] = json.loads(path.read_text(encoding="utf-8"))
        versions[kind] = RegistryVersion(kind=kind, version=raw[kind]["version"], sha256=canonical_sha256(raw[kind]))
    registry = Registry(instruments=InstrumentRegistry.model_validate(raw["instruments"]),
                        domains=DomainRegistry.model_validate(raw["domains"]),
                        conditions=ConditionRegistry.model_validate(raw["conditions"]), versions=versions)
    _cross_check(registry)
    return registry


@lru_cache(maxsize=1)
def registry() -> Registry:
    return load_registry()

"""Deterministic trend and change engine. Pure functions; no I/O.

AI-generated values pending review count with full weight. Review status is carried on each
point for display only. Rejected and stale values leave the active series (they become gaps).
Missing values are gaps: never zero, never interpolated, smoothed or carried forward.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

INACTIVE_STATUSES = frozenset({"rejected", "stale"})
REVIEWED_STATUSES = frozenset({"accepted", "edited"})

RULE_INSTRUMENT = "instrument.reliable_change"
RULE_DOMAIN_JUMP = "domain.rating_jump"
RULE_DOMAIN_EMERGENCE = "domain.emergence"
RULE_PREDICTION_LABEL = "prediction.label_change"
RULE_PREDICTION_PROBABILITY = "prediction.probability_shift"

DOMAIN_JUMP_THRESHOLD = 2
DOMAIN_EMERGENCE_RUN = 2
PROBABILITY_SHIFT = 0.2


@dataclass(frozen=True)
class Point:
    """One slot (session or dated administration) of one series."""
    slot: str
    value: float | None = None
    label: str | None = None          # prediction label / subtype key, or domain status
    probability: float | None = None
    status: str | None = None         # domain status_value (rated, explicit_denial, ...)
    provenance: str | None = None
    review_status: str | None = None
    record_id: str | None = None
    gap_reason: str | None = None
    meta: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def assessed(self) -> bool:
        return self.gap_reason is None and (self.value is not None or self.label is not None)


@dataclass(frozen=True)
class Change:
    rule_id: str
    from_slot: str
    to_slot: str
    delta: float | None
    from_label: str | None
    to_label: str | None
    provenance_of_inputs: str


def is_active(point: Point) -> bool:
    return point.review_status not in INACTIVE_STATUSES


def is_reviewed(point: Point) -> bool:
    return point.provenance == "MANUAL" or point.review_status in REVIEWED_STATUSES


def activate(points: list[Point]) -> list[Point]:
    """Turn rejected/stale values into gaps. Pending AI values stay unchanged."""
    out = []
    for p in points:
        if p.record_id is not None and not is_active(p):
            out.append(replace(p, value=None, label=None, probability=None, gap_reason=p.review_status))
        elif p.record_id is None and p.gap_reason is None and p.value is None and p.label is None:
            out.append(replace(p, gap_reason="not_assessed"))
        else:
            out.append(p)
    return out


def input_provenance(*points: Point) -> str:
    reviewed = [is_reviewed(p) for p in points]
    if all(reviewed):
        return "reviewed"
    if not any(reviewed):
        return "ai"
    return "mixed"


def summary(points: list[Point]) -> dict:
    """Deltas vs previous assessed value and vs baseline; coverage counts."""
    points = activate(points)
    assessed = [p for p in points if p.assessed and p.value is not None]
    deltas, previous = [], None
    for p in points:
        if p.assessed and p.value is not None:
            deltas.append({"slot": p.slot, "delta_previous": None if previous is None else p.value - previous.value,
                           "previous_slot": None if previous is None else previous.slot})
            previous = p
        else:
            deltas.append({"slot": p.slot, "delta_previous": None, "previous_slot": None})
    baseline = assessed[0] if assessed else None
    latest = assessed[-1] if assessed else None
    return {
        "sessions_assessed": len(assessed),
        "sessions_total": len(points),
        "baseline_slot": baseline.slot if baseline else None,
        "latest_slot": latest.slot if latest else None,
        "delta_baseline": None if not baseline or latest is baseline else latest.value - baseline.value,
        "deltas": deltas,
    }


def instrument_changes(points: list[Point], threshold: float | None) -> list[Change]:
    """|delta| >= reliable-change threshold between consecutive assessed values."""
    if threshold is None:
        return []
    assessed = [p for p in activate(points) if p.assessed and p.value is not None]
    changes = []
    for a, b in zip(assessed, assessed[1:]):
        delta = b.value - a.value
        if abs(delta) >= threshold:
            changes.append(Change(RULE_INSTRUMENT, a.slot, b.slot, delta, None, None, input_provenance(a, b)))
    return changes


def domain_changes(points: list[Point], jump: int = DOMAIN_JUMP_THRESHOLD, run: int = DOMAIN_EMERGENCE_RUN) -> list[Change]:
    """Rating jump >= ``jump`` between adjacent rated slots; rated after >= ``run`` insufficient slots."""
    points = activate(points)
    changes = []
    for a, b in zip(points, points[1:]):
        if a.status == "rated" and b.status == "rated" and a.value is not None and b.value is not None and not a.gap_reason and not b.gap_reason:
            if abs(b.value - a.value) >= jump:
                changes.append(Change(RULE_DOMAIN_JUMP, a.slot, b.slot, b.value - a.value, None, None, input_provenance(a, b)))
    streak: list[Point] = []
    for p in points:
        if p.gap_reason is None and p.status == "insufficient_evidence":
            streak.append(p)
            continue
        if p.gap_reason is None and p.status == "rated" and p.value is not None and len(streak) >= run:
            changes.append(Change(RULE_DOMAIN_EMERGENCE, streak[0].slot, p.slot, None, "insufficient_evidence", "rated",
                                  input_provenance(*streak, p)))
        streak = []
    return changes


def prediction_changes(points: list[Point], shift: float = PROBABILITY_SHIFT) -> list[Change]:
    """Label/subtype change, or probability change >= ``shift``, between consecutive as_of slots."""
    assessed = [p for p in activate(points) if p.assessed and p.label is not None]
    changes = []
    for a, b in zip(assessed, assessed[1:]):
        prov = input_provenance(a, b)
        if a.label != b.label:
            changes.append(Change(RULE_PREDICTION_LABEL, a.slot, b.slot, None, a.label, b.label, prov))
        if a.probability is not None and b.probability is not None and abs(b.probability - a.probability) >= shift:
            changes.append(Change(RULE_PREDICTION_PROBABILITY, a.slot, b.slot, b.probability - a.probability, a.label, b.label, prov))
    return changes


def trajectory(values: list[float | None], threshold: float | None) -> str | None:
    """>= 3 values: endpoint change <= -T decreasing, >= +T increasing, else range >= T fluctuating, else stable."""
    observed = [v for v in values if v is not None]
    if threshold is None or len(observed) < 3:
        return None
    change = observed[-1] - observed[0]
    if change <= -threshold:
        return "decreasing"
    if change >= threshold:
        return "increasing"
    if max(observed) - min(observed) >= threshold:
        return "fluctuating"
    return "stable"


def series_trajectory(points: list[Point], threshold: float | None) -> str | None:
    return trajectory([p.value for p in activate(points) if p.assessed], threshold)


# ---------------------------------------------------------------- registry v2 (DSM criterion items)

RULE_CRITERION_STATUS = "criterion.status_change"
RULE_CONDITION_LABEL = "condition.label_change"
_PRESENT = {"present"}
_ABSENT = {"absent", "explicit_denial"}


def criterion_changes(points: list[Point]) -> list[Change]:
    """A criterion item moving between present and absent/denied across consecutive *assessed* sessions.

    ``label`` carries the item status; insufficient evidence, conflicting and subthreshold values are not assessed for
    this rule, and missing sessions stay gaps (never carried forward).
    """
    assessed = [p for p in activate(points) if p.gap_reason is None and p.label in _PRESENT | _ABSENT]
    changes = []
    for a, b in zip(assessed, assessed[1:]):
        if (a.label in _PRESENT) != (b.label in _PRESENT):
            changes.append(Change(RULE_CRITERION_STATUS, a.slot, b.slot, None, a.label, b.label, input_provenance(a, b)))
    return changes


def condition_changes(points: list[Point]) -> list[Change]:
    """The code-computed DSM label changes between consecutive as_of sessions."""
    assessed = [p for p in activate(points) if p.assessed and p.label is not None]
    return [Change(RULE_CONDITION_LABEL, a.slot, b.slot, None, a.label, b.label, input_provenance(a, b))
            for a, b in zip(assessed, assessed[1:]) if a.label != b.label]

"""Fixed, stratified LongCounsel-8 subsets for the benchmark. Run once, commit the manifests, never regenerate.

    python -m services.progress.evaluation.subsets select [--seed 42] [--val-n 12] [--test-n 37]

Stratum = trajectory type DERIVED from ground-truth PHQ-8 totals with the trends.trajectory rule (T = 5), because the
release has no trajectory-type field. Proportional allocation (largest remainder), seed-42 shuffle inside each stratum.
Labels are used only for stratification and reporting; they never reach prompts.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from services.progress.trends import trajectory

from .longcounsel import PREPARED, PRIMARY_EDITION, ROOT, all_labels, canonical, prepared_manifest

SUBSET_DIR = ROOT / "data/research/progress-subsets"
TRAJECTORY_T = 5
LARGE_CHANGE = 5
RULE = ("derived_type = trends.trajectory(ground-truth PHQ-8 totals over 5 sessions, T=5): endpoint change <= -5 decreasing, "
        ">= +5 increasing, else range >= 5 fluctuating, else stable. Proportional allocation by largest remainder (ties by "
        "stratum name), stratum members sorted by run_id then shuffled with random.Random(seed).")


def totals(labels: dict) -> list[float]:
    return [s["total"] for s in sorted(labels["ground_truth"], key=lambda s: s["session_index"])]


def derived_type(labels: dict) -> str:
    return trajectory(totals(labels), TRAJECTORY_T) or "unknown"


def large_changes(labels: dict) -> int:
    t = totals(labels)
    return sum(1 for a, b in zip(t, t[1:]) if abs(b - a) >= LARGE_CHANGE)


def allocate(strata: dict[str, list[str]], n: int) -> dict[str, int]:
    total = sum(len(v) for v in strata.values())
    if n > total:
        raise ValueError(f"Requested {n} trajectories but only {total} exist")
    quotas = {k: n * len(v) / total for k, v in strata.items()}
    base = {k: min(len(strata[k]), math.floor(q)) for k, q in quotas.items()}
    remainder = n - sum(base.values())
    for k in sorted(strata, key=lambda k: (-(quotas[k] - math.floor(quotas[k])), k)):
        if remainder == 0:
            break
        if base[k] < len(strata[k]):
            base[k] += 1
            remainder -= 1
    return base


def stratified(labels: dict[str, dict], n: int, seed: int) -> tuple[list[str], dict]:
    strata: dict[str, list[str]] = {}
    for run_id, lab in sorted(labels.items()):
        strata.setdefault(derived_type(lab), []).append(run_id)
    counts = allocate(strata, n)
    rng = random.Random(seed)
    chosen = []
    for key in sorted(strata):
        members = sorted(strata[key])
        rng.shuffle(members)
        chosen += members[: counts[key]]
    return sorted(chosen), {"population": {k: len(v) for k, v in sorted(strata.items())}, "selected": {k: counts[k] for k in sorted(strata)}}


def build_manifest(name: str, split: str, run_ids: list[str], labels: dict[str, dict], strata: dict, *, edition: str, seed: int,
                   prepared: Path, note: str) -> dict:
    prep = prepared_manifest(edition, prepared)
    selected = {r: labels[r] for r in run_ids}
    return {"name": name, "edition": edition, "split": split, "seed": seed, "n": len(run_ids), "run_ids": run_ids,
            "purpose": note, "strata": strata,
            "trajectory_types": {r: derived_type(selected[r]) for r in run_ids},
            "large_change_transitions": sum(large_changes(selected[r]) for r in run_ids),
            "rule": RULE, "rule_sha256": canonical(RULE),
            "split_file_sha256": next(v["sha256"] for k, v in prep["source_files"].items() if k.endswith("train_val_test_split.json")),
            "labels_sha256": prep["labels_sha256"], "created_at": datetime.now(timezone.utc).isoformat()}


def select(edition: str = PRIMARY_EDITION, seed: int = 42, val_n: int = 12, test_n: int = 37, out: Path = SUBSET_DIR,
           prepared: Path = PREPARED, force: bool = False) -> list[dict]:
    out = Path(out)
    targets = {"val-arms-12.json" if val_n == 12 else f"val-arms-{val_n}.json": ("val", val_n, "development arm comparison"),
               "test-final-37.json" if test_n == 37 else f"test-final-{test_n}.json": ("test", test_n, "final half-test run (run_final, user only)")}
    existing = [name for name in targets if (out / name).exists()]
    if existing and not force:
        raise FileExistsError(f"Subset manifests already exist ({', '.join(existing)}); they are fixed once committed. Refusing to overwrite.")
    out.mkdir(parents=True, exist_ok=True)
    manifests = []
    for name, (split, n, purpose) in targets.items():
        labels = all_labels(edition, prepared, split)
        run_ids, strata = stratified(labels, n, seed)
        manifest = build_manifest(name.removesuffix(".json"), split, run_ids, labels, strata, edition=edition, seed=seed, prepared=prepared, note=purpose)
        (out / name).write_text(json.dumps(manifest, indent=2) + "\n")
        manifests.append(manifest)
    return manifests


def complement(of: Path, out: Path = SUBSET_DIR, prepared: Path = PREPARED, force: bool = False) -> dict:
    """The remaining validation trajectories (optional calibration enlargement for the frozen config)."""
    base = load_subset(of)
    if base["split"] != "val":
        raise ValueError("Complements are built for validation subsets only")
    labels = all_labels(base["edition"], prepared, "val")
    rest = sorted(set(labels) - set(base["run_ids"]))
    name = f"val-rest-{len(rest)}"
    target = Path(out) / f"{name}.json"
    if target.exists() and not force:
        raise FileExistsError(f"{target} already exists; refusing to overwrite")
    strata = {"population": {}, "selected": {}}
    for r in rest:
        t = derived_type(labels[r])
        strata["selected"][t] = strata["selected"].get(t, 0) + 1
    strata["population"] = dict(strata["selected"])
    manifest = build_manifest(name, "val", rest, labels, strata, edition=base["edition"], seed=base["seed"], prepared=prepared,
                              note=f"complement of {base['name']}: optional calibration enlargement for the frozen configuration")
    target.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def load_subset(path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    for key in ("name", "edition", "split", "run_ids", "rule_sha256"):
        if key not in data:
            raise ValueError(f"{path}: not a subset manifest (missing {key})")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("select")
    p.add_argument("--edition", default=PRIMARY_EDITION)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--val-n", type=int, default=12)
    p.add_argument("--test-n", type=int, default=37)
    p.add_argument("--out", type=Path, default=SUBSET_DIR)
    c = sub.add_parser("complement")
    c.add_argument("--of", type=Path, required=True)
    c.add_argument("--out", type=Path, default=SUBSET_DIR)
    args = parser.parse_args()
    if args.command == "complement":
        m = complement(args.of, args.out)
        print(f"{m['name']}: {m['n']} validation trajectories; by type {m['strata']['selected']}; large-change transitions {m['large_change_transitions']}")
        return
    for m in select(args.edition, args.seed, args.val_n, args.test_n, args.out):
        print(f"{m['name']}: {m['n']} {m['split']} trajectories (seed {m['seed']})")
        print(f"  population by derived trajectory type: {m['strata']['population']}")
        print(f"  selected by derived trajectory type:   {m['strata']['selected']}")
        print(f"  large-change transitions (|ΔGT PHQ-8| >= {LARGE_CHANGE}): {m['large_change_transitions']}")
    print(f"Manifests written to {args.out}. Commit them before any benchmark run:")
    print("  git add data/research/progress-subsets && git commit -m 'Fix LongCounsel-8 benchmark subsets'")


if __name__ == "__main__":
    main()

"""Run the frozen progress configuration once on the held-out half-test subset (USER ONLY; never by Claude).

    python -m services.progress.evaluation.run_final --subset data/research/progress-subsets/test-final-37.json

Checks that the frozen configuration and the code it was frozen with are unchanged, refuses to overwrite earlier final
results, records the start, then runs the benchmark (resumable with the same command) and writes the report.
Do not iterate or tune on these results.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .benchmark import FROZEN, RUNS, STAGE_A_FILES, STAGE_B_FILES, file_hashes, load_env, run_subset, write_json
from .subsets import load_subset

FINAL_RUN_ID = "final-test"


def validate(subset_path: Path, frozen_path: Path = FROZEN) -> dict:
    if not Path(frozen_path).exists():
        raise SystemExit("No frozen configuration. Run `benchmark freeze` on the chosen validation run first.")
    frozen = json.loads(Path(frozen_path).read_text())
    if file_hashes(STAGE_A_FILES) != frozen["code_stage_a"] or file_hashes(STAGE_B_FILES) != frozen["code_stage_b"]:
        raise SystemExit("Pipeline code changed after the configuration was frozen; the final run is not valid.")
    subset = load_subset(subset_path)
    if subset["split"] != "test":
        raise SystemExit("run_final is for the held-out test subset only")
    return frozen


def main(argv=None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--subset", type=Path, required=True)
    args = parser.parse_args(argv)
    frozen = validate(args.subset)
    folder = RUNS / FINAL_RUN_ID
    output = folder / "report" / "final-results.json"
    if output.exists():
        parser.error("Final results already exist; do not iterate or tune on final-set results")
    started = folder / "evaluation-started.json"
    if not started.exists():
        write_json(started, {"subset_sha256": hashlib.sha256(args.subset.read_bytes()).hexdigest(), "frozen": frozen,
                             "started_at": datetime.now(timezone.utc).isoformat()})
    code = run_subset(args.subset, "frozen", FINAL_RUN_ID, allow_test=True)
    if code != 0:
        print("Final run not finished; rerun this same command to resume.")
        return code
    from .report import build
    report = build(FINAL_RUN_ID)
    write_json(output, report)
    print(f"Final results: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

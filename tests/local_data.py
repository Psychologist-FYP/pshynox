"""Skip markers for tests that read local research files which are deliberately not in Git.

On a fresh clone these files are absent, so the tests skip with the reason shown; on a machine that has them they run.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / "docs/Pshynox_Retrieval_Starter"
REVIEW = ROOT / "data/research/review-experiments"


def _missing(*paths: Path) -> bool:
    return not all(p.exists() for p in paths)


needs_starter_pack = pytest.mark.skipif(
    _missing(STARTER / "data/annomi/AnnoMI-simple.csv", *(STARTER / "data/primock57" / f"day1_consultation0{i}_{kind}"
             for i in (1, 2) for kind in ("transcript.csv", "doctor.wav", "patient.wav", "doctor.TextGrid", "patient.TextGrid", "note.json"))),
    reason="needs the local retrieval starter pack (docs/Pshynox_Retrieval_Starter/data/, not in Git; see START_HERE.md)")

needs_review_experiments = pytest.mark.skipif(
    _missing(REVIEW / "frozen-method-config.json", REVIEW / "independent-final/packet.json"),
    reason="needs local retrieval review-experiment outputs (data/research/review-experiments/, not in Git)")

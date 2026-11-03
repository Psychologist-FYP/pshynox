"""Convert existing research annotations to Git-shareable conversation files.

Stop the application first. Legacy JSON files remain as ignored local backups.
Already converted datasets are validated and left unchanged.
"""
import argparse
from pathlib import Path

from services.retrieval.workspace import ResearchStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="Research data directory (defaults to RESEARCH_DATA_DIR or data/research)")
    args = parser.parse_args()
    store = ResearchStore(args.root)
    paths = sorted((store.root / "datasets").glob("*.json"))
    if not paths:
        parser.error("No prepared research datasets found")
    for path in paths:
        state = store.share_annotations(path.stem)
        judgments = sum(len(q["judgments"]) for q in state["queries"])
        print(f"{path.stem}: {len(state['queries'])} queries, {judgments} saved judgments; conversation files ready for Git")


if __name__ == "__main__":
    main()

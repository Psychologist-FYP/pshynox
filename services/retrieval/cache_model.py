"""Explicit one-time E5 download. Runtime search never enables downloads itself."""
import argparse
import os
from pathlib import Path
import json

from services.retrieval.config import RetrievalSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", required=True, help="Prefer an immutable model commit hash")
    parser.add_argument("--cache-dir", default=RetrievalSettings().e5_cache_dir)
    args = parser.parse_args()
    os.environ.setdefault("HF_HOME", str(Path(args.cache_dir).resolve() / "hf-home"))
    from huggingface_hub import HfApi
    from sentence_transformers import SentenceTransformer
    revision = HfApi().model_info(RetrievalSettings().e5_model, revision=args.revision).sha
    model = SentenceTransformer(RetrievalSettings().e5_model, revision=revision,
                                cache_folder=args.cache_dir, device="cpu", local_files_only=False)
    model.max_seq_length = 512
    manifest = Path(args.cache_dir) / "model-revision.json"
    manifest.write_text(json.dumps({"model": RetrievalSettings().e5_model, "revision": revision}, indent=2) + "\n")
    print(f"Cached {RetrievalSettings().e5_model} revision {revision} in {args.cache_dir}")


if __name__ == "__main__":
    main()

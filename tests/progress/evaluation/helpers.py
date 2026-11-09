"""Pack the fictional mini fixture into a release-like raw folder with real hashes."""
import hashlib
import json
import shutil
import tarfile
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "longcounsel_mini"
EDITION = "lc8-mini"


def build_raw(tmp: Path) -> Path:
    raw = tmp / "raw"
    (raw / EDITION).mkdir(parents=True)
    shutil.copy(FIXTURE / "README.md", raw / "README.md")
    shutil.copy(FIXTURE / "LICENSE", raw / "LICENSE")
    shutil.copy(FIXTURE / "tree" / "train_val_test_split.json", raw / EDITION / "train_val_test_split.json")
    archive = raw / EDITION / "data.tar"
    with tarfile.open(archive, "w") as tar:
        for path in sorted((FIXTURE / "tree" / EDITION).rglob("*")):
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(FIXTURE / "tree")))
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()  # noqa: E731
    manifest = {"schema_version": 3, "datasets": {EDITION: {"archive": f"{EDITION}/data.tar", "archive_root": EDITION, "archive_sha256": sha(archive),
                "split_file": f"{EDITION}/train_val_test_split.json", "split_sha256": sha(raw / EDITION / "train_val_test_split.json")}}}
    (raw / "manifest.json").write_text(json.dumps(manifest))
    return raw


def prepared(tmp: Path) -> Path:
    from services.progress.evaluation.longcounsel import prepare
    out = tmp / "prepared"
    prepare(EDITION, build_raw(tmp), out)
    return out

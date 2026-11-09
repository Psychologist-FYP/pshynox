"""LongCounsel-8 preparation: verify the release, stream the archive (never extracted into raw/), keep only
visible transcript fields, and write research copies with labels kept separately.

    python -m services.progress.evaluation.longcounsel prepare [--edition longcounsel-8-gpt-5.4-mini-low]

Labels never enter prompts or the clinical database. Synthetic timestamps: within a session, row k (0-based, file
order: counselor turn then client turn) gets start_ms = k*10000 and end_ms = start_ms + 9000.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RAW = ROOT / "data/research/datasets/longcounsel-8/raw"
PREPARED = ROOT / "data/research/datasets/longcounsel-8/prepared"
PRIMARY_EDITION = "longcounsel-8-gpt-5.4-mini-low"
ALLOWED_FIELDS = ("session_id", "turn_id", "speaker", "text", "message_id")
SPEAKERS = {"Counselor": "CLINICIAN", "Client": "CLIENT"}
PHQ8_ITEMS = ["anhedonia", "depressed_mood", "sleep", "fatigue", "appetite", "self_worth", "concentration", "psychomotor"]  # PHQ items 1..8
QUESTIONNAIRE_TERMS = re.compile(r"\b(phq|gad|questionnaire|score[sd]?|severity|scale|item\s+\d)\b", re.I)
SPLIT_ALIASES = {"validation": "val", "val": "val", "train": "train", "test": "test"}


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def canonical(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _members(archive: Path):
    """Yield (name, bytes) for regular files, streaming .tar.zst through zstd (or tarfile's native support)."""
    if archive.suffix == ".zst":
        try:
            with tarfile.open(archive, "r:zst") as tar:  # Python 3.14+
                for m in tar:
                    if m.isfile():
                        yield m.name, tar.extractfile(m).read()
                return
        except (tarfile.CompressionError, tarfile.ReadError, ValueError):
            pass
        if shutil.which("zstd") is None:
            raise RuntimeError("Reading .tar.zst needs Python 3.14+ or the zstd command on PATH")
        proc = subprocess.Popen(["zstd", "-dc", str(archive)], stdout=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                for m in tar:
                    if m.isfile():
                        yield m.name, tar.extractfile(m).read()
        finally:
            proc.stdout.close()
            if proc.wait() != 0:
                raise RuntimeError("zstd failed while reading the archive")
    else:
        with tarfile.open(archive, "r:*") as tar:
            for m in tar:
                if m.isfile():
                    yield m.name, tar.extractfile(m).read()


def clean_transcript(raw: bytes, run_id: str, report: dict) -> list[dict]:
    """Allowlist the visible fields; anything else (state, trend, scores) is dropped and counted."""
    sessions: dict[int, list[dict]] = {}
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        for key in row:
            if key not in ALLOWED_FIELDS:
                report["dropped_fields"][key] = report["dropped_fields"].get(key, 0) + 1
        if row.get("speaker") not in SPEAKERS:
            raise ValueError(f"{run_id}: unknown speaker {row.get('speaker')!r}")
        if QUESTIONNAIRE_TERMS.search(row["text"]):
            report["questionnaire_term_rows"] += 1
            for term in QUESTIONNAIRE_TERMS.findall(row["text"]):
                report["questionnaire_terms"][term.lower()] = report["questionnaire_terms"].get(term.lower(), 0) + 1
        sessions.setdefault(int(row["session_id"]), []).append({k: row[k] for k in ALLOWED_FIELDS})
    out = []
    for index in sorted(sessions):
        segments = []
        for k, row in enumerate(sessions[index]):
            segments.append({"id": row["message_id"], "start_ms": k * 10_000, "end_ms": k * 10_000 + 9_000,
                             "speaker_role": SPEAKERS[row["speaker"]], "text": row["text"], "revision": 1,
                             "exchange_index": int(row["turn_id"])})
        out.append({"session_index": index, "segments": segments})
    return out


def items_vector(scores: dict) -> dict[str, float]:
    return {str(i + 1): float(scores[name]) for i, name in enumerate(PHQ8_ITEMS)}


def clean_labels(gt_raw: bytes, sr_raw: bytes) -> dict:
    gt, sr = json.loads(gt_raw), json.loads(sr_raw)
    return {"scale": gt.get("scale"),
            "ground_truth": [{"session_index": int(s["session_id"]), "items": items_vector(s["ground_truth_score"]["symptom_scores"]),
                              "total": float(s["ground_truth_score"]["overall_score"]), "band": s["ground_truth_score"].get("severity_band")}
                             for s in gt["sessions"]],
            "self_report": [{"session_index": int(s.get("session_index", s["session_id"])), "items": items_vector(s["self_report_score"]["symptom_scores"]),
                             "total": float(s["self_report_score"]["overall_score"])} for s in sr["sessions"]]}


def prepare(edition: str = PRIMARY_EDITION, raw: Path = RAW, out: Path = PREPARED) -> dict:
    raw, out = Path(raw), Path(out)
    for required in ("README.md", "LICENSE", "manifest.json"):
        if not (raw / required).is_file():
            raise FileNotFoundError(f"Missing {required} in {raw}; place the LongCounsel-8 release there (do not download it from code)")
    licence = (raw / "LICENSE").read_text(encoding="utf-8", errors="replace")
    if "CC BY-NC 4.0" not in licence and "Attribution-NonCommercial 4.0" not in licence:
        raise ValueError("Unexpected licence text; check the release before use")
    manifest = json.loads((raw / "manifest.json").read_text())
    entry = manifest["datasets"].get(edition)
    if entry is None:
        raise KeyError(f"Unknown edition {edition}")
    archive, split_file = raw / entry["archive"], raw / entry["split_file"]
    files = {}
    for path, expected in ((archive, entry["archive_sha256"]), (split_file, entry["split_sha256"])):
        actual = sha256(path)
        if actual != expected:
            raise ValueError(f"Checksum mismatch for {path.name}: expected {expected}, got {actual}")
        files[str(path.relative_to(raw))] = {"bytes": path.stat().st_size, "sha256": actual}
    splits = {r["run_id"]: {"split": r["split"], "profile_id": r.get("profile_id")} for r in json.loads(split_file.read_text())["runs"]}
    target = out / edition
    if target.exists():
        shutil.rmtree(target)
    (target / "transcripts").mkdir(parents=True)
    (target / "labels").mkdir(parents=True)
    report = {"dropped_fields": {}, "questionnaire_term_rows": 0, "questionnaire_terms": {}}
    pending: dict[str, dict[str, bytes]] = {}
    root_name = entry.get("archive_root", edition)
    for name, data in _members(archive):
        parts = Path(name).parts
        if len(parts) != 4 or parts[0] != root_name or parts[2] != "common" or parts[3] not in {"transcript.jsonl", "ground_truth.json", "self_report.json"}:
            continue
        run_id = parts[1]
        if run_id not in splits:
            raise ValueError(f"{run_id} is not in the split file")
        pending.setdefault(run_id, {})[parts[3]] = data
        bundle = pending[run_id]
        if len(bundle) == 3:
            sessions = clean_transcript(bundle["transcript.jsonl"], run_id, report)
            info = splits[run_id]
            (target / "transcripts" / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, "split": info["split"], "profile_id": info["profile_id"],
                                                                              "sessions": sessions}, ensure_ascii=False))
            (target / "labels" / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, "split": info["split"],
                                                                         **clean_labels(bundle["ground_truth.json"], bundle["self_report.json"])}))
            del pending[run_id]
    if pending:
        raise ValueError(f"Incomplete trajectories in archive: {sorted(pending)[:5]}")
    written = sorted(p.stem for p in (target / "transcripts").glob("*.json"))
    if set(written) != set(splits):
        raise ValueError(f"Archive and split file disagree ({len(written)} vs {len(splits)} trajectories)")
    summary = {"edition": edition, "prepared_at": datetime.now(timezone.utc).isoformat(), "source_files": files,
               "trajectories": len(written), "splits": {s: sum(1 for v in splits.values() if v["split"] == s) for s in ("train", "val", "test")},
               "licence": "CC BY-NC 4.0 (see raw/LICENSE); research use only; not for decisions about a person's care",
               "transcript_fields_kept": list(ALLOWED_FIELDS), "leakage_guard": report,
               "timestamps": "synthetic: row k of a session -> start_ms=k*10000, end_ms=start_ms+9000",
               "labels_sha256": canonical([json.loads((target / "labels" / f"{r}.json").read_text()) for r in written]),
               "transcripts_sha256": canonical([json.loads((target / "transcripts" / f"{r}.json").read_text()) for r in written])}
    (target / "manifest.json").write_text(json.dumps(summary, indent=2))
    return summary


def load_transcript(run_id: str, edition: str = PRIMARY_EDITION, prepared: Path = PREPARED) -> dict:
    return json.loads((Path(prepared) / edition / "transcripts" / f"{run_id}.json").read_text())


def load_labels(run_id: str, edition: str = PRIMARY_EDITION, prepared: Path = PREPARED) -> dict:
    return json.loads((Path(prepared) / edition / "labels" / f"{run_id}.json").read_text())


def all_labels(edition: str = PRIMARY_EDITION, prepared: Path = PREPARED, split: str | None = None) -> dict[str, dict]:
    folder = Path(prepared) / edition / "labels"
    if not folder.is_dir():
        raise FileNotFoundError(f"{folder} missing: run `python -m services.progress.evaluation.longcounsel prepare` first")
    out = {}
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text())
        if split is None or data["split"] == SPLIT_ALIASES.get(split, split):
            out[data["run_id"]] = data
    return out


def prepared_manifest(edition: str = PRIMARY_EDITION, prepared: Path = PREPARED) -> dict:
    return json.loads((Path(prepared) / edition / "manifest.json").read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--edition", default=PRIMARY_EDITION)
    p.add_argument("--raw", type=Path, default=RAW)
    p.add_argument("--out", type=Path, default=PREPARED)
    args = parser.parse_args()
    summary = prepare(args.edition, args.raw, args.out)
    print(json.dumps({k: summary[k] for k in ("edition", "trajectories", "splits", "leakage_guard", "timestamps")}, indent=2))


if __name__ == "__main__":
    main()

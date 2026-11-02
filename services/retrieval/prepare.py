"""Verify the supplied starter pack and prepare isolated research datasets.

Run: .venv/bin/python -m services.retrieval.prepare
Existing datasets/annotations are never overwritten.
"""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import random

from services.retrieval.import_data import research_payload

ROOT = Path(__file__).resolve().parents[2]
STARTER = ROOT / "docs/Pshynox_Retrieval_Starter"
SEED = 20261102


def research_root():
    return Path(os.getenv("RESEARCH_DATA_DIR", str(ROOT / "data/research"))).resolve()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def verify_pack(starter):
    manifest = json.loads((starter / "manifest.json").read_text())
    checks = []
    for entry in manifest["files"]:
        path = starter / entry["path"]
        valid = (path.is_file() and path.stat().st_size == entry["bytes"]
                 and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"])
        checks.append({"path": entry["path"], "valid": valid})
    if not all(c["valid"] for c in checks):
        raise ValueError("Missing/corrupt starter files: " + ", ".join(c["path"] for c in checks if not c["valid"]))
    return {"manifest_sha256": hashlib.sha256((starter / "manifest.json").read_bytes()).hexdigest(), "files": checks}


def select_pilot(conversations, seed, size):
    """Group known identities and shared source videos before assigning splits."""
    parents = {c["conversation_id"]: c["conversation_id"] for c in conversations}
    def find(cid):
        while parents[cid] != cid:
            cid = parents[cid]
        return cid
    owners = {}
    for c in conversations:
        keys = ["participant:" + p for p in c.get("participant_ids", [])]
        if c.get("source_video"):
            keys.append("video:" + c["source_video"])
        for key in keys:
            if key in owners:
                parents[find(c["conversation_id"])] = find(owners[key])
            owners[key] = c["conversation_id"]
    groups = {}
    for c in conversations:
        groups.setdefault(find(c["conversation_id"]), []).append(c["conversation_id"])
    ordered = sorted(sorted(g) for g in groups.values())
    random.Random(seed).shuffle(ordered)
    selected, split_map = [], {}
    for group in ordered:
        # Whole groups stay together even if the pilot grows past its target.
        split = "dev" if len(selected) < max(1, round(size * .7)) else "test"
        for cid in group:
            split_map[cid] = split
        if len(selected) < size:
            selected.extend(group)
    return {"seed": seed, "requested_conversations": size, "pilot_ids": selected,
            "groups": ordered, "splits": split_map,
            "identity_limit": "Participant identities unavailable in supplied CSVs. Shared video sources grouped; speaker independence is not established.",
            "target_queries": 4 * len(selected)}


def draft_queries(conversations, ids):
    queries = []
    for c in conversations:
        cid = c["conversation_id"]
        if cid not in ids:
            continue
        turns = c["segments"]
        clients = [s for s in turns if s["speaker_role"] == "CLIENT"] or turns
        substantial = next((s for s in clients if len(s["text"].split()) >= 10), clients[0])
        short = min(clients, key=lambda s: len(s["text"].split()))
        snippet = " ".join(substantial["text"].split()[:12])
        suggestions = [
            (f'"{" ".join(substantial["text"].split()[:5])}"', "literal", f"Find the statement beginning {snippet!r}; check other relevant turns too."),
            (f"What is the client explaining in this statement: {snippet}?", "paraphrase", "Draft prompt: rewrite in your own words as a paraphrase of this source before approving; do not copy its wording."),
            (f'What does the client mean by "{short["text"][:120]}"?', "context", f"Interpret the short reply at {short['id']} using surrounding conversation; review all turns for evidence."),
            ("What does the client say about taking a scuba-diving certification course?", "no-answer-candidate", "Draft absent-topic candidate. Read the FULL conversation to verify absence, or replace the query. Absence is not pre-labelled."),
        ]
        for number, (text, kind, intent) in enumerate(suggestions, 1):
            queries.append({"query_id": f"draft-{cid}-{number}", "conversation_id": cid,
                            "text": text, "intent": intent, "kind": kind, "draft": True,
                            "no_answer_confirmed": False, "judgments": {}})
    return queries


def prepare(starter=STARTER, output=None, seed=SEED, pilot_size=10):
    output = Path(output or research_root())
    verification = verify_pack(starter)
    annomi_path = starter / "data/annomi/AnnoMI-simple.csv"
    annomi = research_payload("annomi", annomi_path)
    with annomi_path.open(encoding="utf-8-sig", newline="") as stream:
        videos = {"annomi-" + r["transcript_id"]: r.get("video_url", "") for r in csv.DictReader(stream)}
    for c in annomi["conversations"]:
        c["source_video"] = videos[c["conversation_id"]]
    primock = {"description": "Two supplied PriMock consultations; awaiting human annotation.",
               "conversations": [], "queries": [], "judgments": [], "source": []}
    for path in sorted((starter / "data/primock57").glob("*_transcript.csv")):
        imported = research_payload("primock", path)
        primock["conversations"].extend(imported["conversations"])
        primock["source"].append(imported["source"])
    for name, payload, size in (("annomi", annomi, pilot_size), ("primock", primock, len(primock["conversations"]))):
        payload["selection"] = select_pilot(payload["conversations"], seed, size)
        for c in payload["conversations"]:
            c["split"] = payload["selection"]["splits"][c["conversation_id"]]
        payload.update(dataset_id=name, schema_version=1, verification=verification,
                       kind="dataset", annotation_status="awaiting human annotation")
        payload["version"] = digest(payload)
        path = output / "datasets" / f"{name}.json"
        if path.exists():
            if json.loads(path.read_text())["version"] != payload["version"]:
                raise ValueError(f"{path} already exists with a different version. Use a new output directory to preserve annotations.")
        else:
            write_json(path, payload)
        state = output / "annotations" / f"{name}.json"
        if not state.exists():
            write_json(state, {"revision": 0, "dataset_version": payload["version"], "pilot_extra": [],
                               "explored": [], "queries": draft_queries(payload["conversations"], payload["selection"]["pilot_ids"])})
        print(f"{name}: {len(payload['conversations'])} conversations; pilot {payload['selection']['pilot_ids']}; no completed labels")
    write_json(output / "verification.json", verification)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--pilot-size", type=int, default=10)
    args = parser.parse_args()
    if args.pilot_size < 1:
        parser.error("pilot size must be positive")
    print(f"Research workspace: {prepare(output=args.output, seed=args.seed, pilot_size=args.pilot_size)}")


if __name__ == "__main__":
    main()

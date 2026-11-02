"""Import transcript-only research inputs; never import reference notes/labels.

This writes an unjudged benchmark template, not application patient records.
"""
import argparse
import csv
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from services.api.schemas import TranscriptSegment


def timestamp_ms(value: str) -> int:
    try:
        parts = [Decimal(p) for p in value.strip().split(":")]
        if not 1 <= len(parts) <= 3 or any(not p.is_finite() or p < 0 for p in parts):
            raise ValueError()
        if any(p >= 60 for p in parts[1:]):
            raise ValueError()
        seconds = Decimal(0)
        for part in parts:
            seconds = seconds * 60 + part
        return int(seconds * 1000)
    except (InvalidOperation, ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid timestamp: {value!r}") from exc


def _field(row, *names):
    for name in names:
        if name in row and row[name] is not None and row[name].strip():
            return row[name]
    raise ValueError(f"CSV is missing a required transcript column: {' / '.join(names)}")


def _headers(reader, groups):
    for names in groups:
        if not any(name in (reader.fieldnames or []) for name in names):
            raise ValueError(f"CSV is missing a required transcript column: {' / '.join(names)}")


def import_annomi(path: Path):
    conversations = {}
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        _headers(reader, [(name,) for name in ("transcript_id", "utterance_id", "interlocutor", "timestamp", "utterance_text")])
        for row in reader:
            cid = "annomi-" + _field(row, "transcript_id")
            sid = f"{cid}-utterance-{_field(row, 'utterance_id')}"
            if sid in seen:
                raise ValueError(f"Duplicate segment ID: {sid}")
            seen.add(sid)
            role = {"therapist": "CLINICIAN", "client": "CLIENT"}.get(_field(row, "interlocutor").lower(), "UNKNOWN")
            start = timestamp_ms(_field(row, "timestamp"))
            conversations.setdefault(cid, []).append(TranscriptSegment(
                id=sid, session_id=cid, start_ms=start, end_ms=start,
                text=_field(row, "utterance_text"), speaker_role=role, revision=1))
    # AnnoMI supplies starts, not authoritative end timestamps. Retain point
    # timestamps rather than inventing end times from the next speaker.
    if not conversations:
        raise ValueError("No transcript turns supplied")
    return conversations


def import_primock(path: Path):
    cid = path.stem.removesuffix("_transcript")
    segments = []
    seen = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        _headers(reader, [("text", "transcript", "utterance_text"), ("speaker", "speaker_role"),
                          ("source_file", "source_filename", "source_textgrid"), ("segment_id", "interval_id", "interval_index"),
                          ("start_ms", "start_seconds", "start", "start_s", "start_time"),
                          ("end_ms", "end_seconds", "end", "end_s", "end_time")])
        for row in reader:
            text = next((row[name] for name in ("text", "transcript", "utterance_text") if name in row), None)
            if text is None:
                raise ValueError("Missing transcript text")
            if not text.strip():
                continue
            speaker = _field(row, "speaker", "speaker_role").lower()
            source = _field(row, "source_file", "source_filename", "source_textgrid")
            if row.get("conversation_id") and row["conversation_id"] != cid:
                raise ValueError(f"Conversation ID must match input filename: {cid}")
            if "segment_id" in row:
                sid = _field(row, "segment_id")
            else:
                interval = _field(row, "interval_id", "interval_index")
                identity = hashlib.sha256(f"{source}:{interval}".encode()).hexdigest()[:20]
                sid = f"{cid}-{identity}"
            if sid in seen:
                raise ValueError(f"Duplicate segment ID: {sid}")
            seen.add(sid)
            start = int(_field(row, "start_ms")) if "start_ms" in row else timestamp_ms(_field(row, "start_seconds", "start", "start_s", "start_time"))
            end = int(_field(row, "end_ms")) if "end_ms" in row else timestamp_ms(_field(row, "end_seconds", "end", "end_s", "end_time"))
            segments.append(TranscriptSegment(id=sid, session_id=cid, start_ms=start, end_ms=end,
                                              speaker_role={"doctor": "CLINICIAN", "patient": "CLIENT", "clinician": "CLINICIAN", "client": "CLIENT"}.get(speaker, "UNKNOWN"),
                                              text=text, revision=1))
    if not segments:
        raise ValueError("No transcript turns supplied")
    return {cid: segments}


def research_payload(format_name: str, path: Path, split="dev"):
    conversations = import_annomi(path) if format_name == "annomi" else import_primock(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    sources = {}
    if format_name == "primock":
        cid = next(iter(conversations))
        for row in rows:
            source = _field(row, "source_file", "source_filename", "source_textgrid")
            if row.get("segment_id"):
                sid = row["segment_id"]
            else:
                interval = _field(row, "interval_id", "interval_index")
                sid = cid + "-" + hashlib.sha256(f"{source}:{interval}".encode()).hexdigest()[:20]
            sources[sid] = source
    return {
        "description": f"Unjudged {format_name} transcript import. Awaiting human annotation.",
        "source": {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                   "attribution": "Retain publisher README, citations and usage terms.",
                   "end_timestamp_policy": "AnnoMI point timestamps; PriMock supplied intervals"},
        "conversations": [{"conversation_id": cid, "split": split,
                           "source_files": {s.id: sources[s.id] for s in turns if s.id in sources},
                           "segments": [s.model_dump(mode="json") for s in sorted(turns, key=lambda s: (s.start_ms, s.id))]}
                          for cid, turns in conversations.items()],
        "queries": [], "judgments": [],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", required=True, choices=["annomi", "primock"])
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", choices=["dev", "test"], default="dev")
    args = parser.parse_args()
    payload = research_payload(args.format, args.input, args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {sum(len(c['segments']) for c in payload['conversations'])} turns in {len(payload['conversations'])} conversations to {args.output}. No relevance labels generated.")


if __name__ == "__main__":
    main()

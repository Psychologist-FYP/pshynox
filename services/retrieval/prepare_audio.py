"""Create aligned PriMock WAV clips and reference transcripts without changing originals."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import wave

from services.retrieval.import_data import import_primock
from services.retrieval.prepare import STARTER, research_root, write_json
# Research clips stay at most five minutes (the length they were prepared and evaluated with), independent of the
# clinical recording limit in services.speech.audio.
MAX_SECONDS = 300


def boundaries(turns, duration_ms, maximum_ms):
    edges = [0]
    while duration_ms - edges[-1] > maximum_ms:
        target = edges[-1] + maximum_ms
        candidates = sorted({s.start_ms for s in turns} | {s.end_ms for s in turns} | {target}, reverse=True)
        safe = [point for point in candidates if max(edges[-1] + 1, target - 30000) <= point <= target
                and not any(s.start_ms < point < s.end_ms for s in turns)]
        edges.append(safe[0] if safe else target)
    edges.append(duration_ms)
    return edges


def prepare_consultation(csv_path, output, split="dev", maximum_seconds=MAX_SECONDS):
    if not 1 <= maximum_seconds <= MAX_SECONDS:
        raise ValueError(f"Clip duration must be 1–{MAX_SECONDS} seconds")
    if split not in {"dev", "test"}:
        raise ValueError("Invalid split")
    cid, turns = next(iter(import_primock(csv_path).items()))
    tracks, rate = {}, None
    for role in ("doctor", "patient"):
        source = csv_path.parent / f"{cid}_{role}.wav"
        with wave.open(str(source), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise ValueError("Expected original mono 16-bit PCM tracks")
            if rate is not None and wav.getframerate() != rate:
                raise ValueError("Tracks must use the same sample rate")
            rate = wav.getframerate()
            tracks[role] = {"source": source, "frames": wav.getnframes(), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    duration_ms = math.ceil(max(t["frames"] for t in tracks.values()) * 1000 / rate)
    if any(s.end_ms > duration_ms for s in turns):
        raise ValueError("Transcript extends beyond the source recordings")
    edges = boundaries(turns, duration_ms, maximum_seconds * 1000)
    destination = output / cid
    if destination.exists():
        raise ValueError(f"Output exists: {destination}. Choose a new output folder to preserve prior preparations.")
    destination.mkdir(parents=True)
    manifest = {"conversation_id": cid, "split": split, "maximum_seconds": maximum_seconds,
                "boundaries_ms": edges, "originals_preserved": True,
                "source_tracks": {role: {"file": str(t["source"]), "sha256": t["sha256"], "frames": t["frames"]} for role, t in tracks.items()},
                "asr_alignment": "not measured; raw ASR segment IDs must be manually aligned before paired evaluation",
                "clips": []}
    conversations = []
    for index, (start, end) in enumerate(zip(edges, edges[1:]), 1):
        clip_id = f"{cid}-clip-{index:03d}"
        first, last = start * rate // 1000, end * rate // 1000
        wanted = last - first
        audio = {}
        for role, track in tracks.items():
            name = f"{clip_id}_{role}.wav"
            with wave.open(str(track["source"]), "rb") as original:
                original.setpos(min(first, original.getnframes()))
                raw = original.readframes(wanted)
            padding = wanted * 2 - len(raw)
            with wave.open(str(destination / name), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(rate)
                target.writeframes(raw + b"\0" * padding)
            audio[role] = {"file": name, "source_offset_ms": start, "duration_ms": end - start,
                           "padding_frames": padding // 2, "source_role": "CLINICIAN" if role == "doctor" else "CLIENT"}
        included = [s for s in turns if start <= s.start_ms and s.end_ms <= end]
        excluded = [s.id for s in turns if s.start_ms < end and s.end_ms > start and s not in included]
        segments = [{**s.model_dump(mode="json"), "session_id": clip_id,
                     "start_ms": s.start_ms - start, "end_ms": s.end_ms - start} for s in included]
        reference = {"conversation_id": clip_id, "parent_conversation_id": cid, "split": split,
                     "source_offset_ms": start, "segments": segments,
                     "original_source_ids": {s.id: s.id for s in included}}
        write_json(destination / f"{clip_id}_reference.json", reference)
        manifest["clips"].append({"clip_id": clip_id, "start_ms": start, "end_ms": end,
                                  "tracks": audio, "reference": f"{clip_id}_reference.json",
                                  "excluded_boundary_source_ids": excluded})
        conversations.append(reference)
    manifest["excluded_source_ids"] = sorted({s.id for s in turns} - {s["id"] for c in conversations for s in c["segments"]})
    write_json(destination / "manifest.json", manifest)
    return manifest, conversations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=research_root() / "audio-clips")
    parser.add_argument("--dataset", type=Path, default=research_root() / "datasets/primock.json")
    parser.add_argument("--max-seconds", type=int, default=MAX_SECONDS)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text())
    splits = {c["conversation_id"]: c["split"] for c in dataset["conversations"]}
    conversations = []
    for csv_path in sorted((STARTER / "data/primock57").glob("*_transcript.csv")):
        cid = csv_path.stem.removesuffix("_transcript")
        manifest, clips = prepare_consultation(csv_path, args.output, splits[cid], args.max_seconds)
        conversations.extend(clips)
        print(f"{cid}: {len(clips)} paired clips; {len(manifest['excluded_source_ids'])} boundary-excluded turns; split {splits[cid]}")
    write_json(args.output / "reference-dataset.json", {"description": "Unjudged PriMock clip references; ASR effects not measured",
               "source_dataset_version": dataset["version"], "conversations": conversations, "queries": [], "judgments": []})


if __name__ == "__main__":
    main()

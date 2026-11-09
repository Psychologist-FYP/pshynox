// node --experimental-strip-types --test unit/   (npm run test:unit)
import { test } from "node:test";
import assert from "node:assert/strict";
import { decode, encode, MAX_FRAME, PART_BYTES, Reassembler, sha256Hex, splitParts } from "../src/features/meetings/protocol.ts";

const id = "123e4567-e89b-42d3-a456-426614174000";

test("encode/decode round-trips every message type", () => {
  const messages = [
    { t: "hello", role: "CLIENT", caps: { record: true, idb: true }, app: "pshynox" },
    { t: "chat", id: "c1", text: "Fictional hello" },
    { t: "media", camera: false, mic: true },
    { t: "recording_start", meeting_ms: 1200 }, { t: "recording_stop", meeting_ms: 61200 },
    { t: "clock_ping", seq: 1, t0: 123.4 }, { t: "clock_pong", seq: 1, t0: 123.4, meeting_ms: 5000 },
    { t: "chunk_meta", chunk_id: id, offset_ms: 60000, offset_uncertainty_ms: 12, rtt_ms: 24, duration_ms: 60000, rate: 16000, bytes: 1920044, parts: Math.ceil(1920044 / PART_BYTES), sha256: "a".repeat(64) },
    { t: "chunk_part", chunk_id: id, index: 0, data: "AAAA" },
    { t: "chunk_ack", chunk_id: id, status: "saved" }, { t: "verify", code_confirmed: true }, { t: "bye", reason: "end" },
  ];
  for (const m of messages) assert.deepEqual(decode(encode(m)), m);
});

test("decode rejects malformed, oversize, other-version and unknown frames", () => {
  for (const frame of ["", "not json", "{}", JSON.stringify({ v: 2, t: "chat", id: "x", text: "hi" }), JSON.stringify({ v: 1, t: "offer", sdp: "x" }),
    JSON.stringify({ v: 1, t: "chat", id: "x", text: "   " }), JSON.stringify({ v: 1, t: "chat", id: "x", text: "y".repeat(2001) }),
    JSON.stringify({ v: 1, t: "chunk_meta", chunk_id: id, offset_ms: 0, offset_uncertainty_ms: 0, rtt_ms: 0, duration_ms: 60000, rate: 16000, bytes: 1000, parts: 5, sha256: "a".repeat(64) }),
    JSON.stringify({ v: 1, t: "chunk_part", chunk_id: "not-a-uuid", index: 0, data: "AAAA" }), "x".repeat(MAX_FRAME + 1), 42, null]) {
    assert.equal(decode(frame), null, String(frame).slice(0, 60));
  }
  assert.throws(() => encode({ t: "chat", id: "x", text: "" }));
});

test("a full-size part frame stays under the frame limit", () => {
  const [part] = splitParts(id, new Uint8Array(PART_BYTES).fill(255));
  assert.ok(new TextEncoder().encode(encode(part)).length <= MAX_FRAME);
});

function wav(bytes) { const b = new Uint8Array(bytes); for (let i = 0; i < b.length; i++) b[i] = (i * 31) % 256; return b; }
async function metaFor(bytes) { return { t: "chunk_meta", chunk_id: id, offset_ms: 0, offset_uncertainty_ms: 0, rtt_ms: 0, duration_ms: 1000, rate: 16000, bytes: bytes.length, parts: Math.ceil(bytes.length / PART_BYTES), sha256: await sha256Hex(bytes) }; }

test("reassembly tolerates reordering and duplicates and verifies the hash", async () => {
  const bytes = wav(50000);
  const parts = splitParts(id, bytes);
  const r = new Reassembler(await metaFor(bytes));
  const order = [...parts].reverse();
  let result;
  for (const [i, p] of [...order.slice(0, 2), order[0], ...order.slice(2)].entries()) result = await r.add(p);
  assert.equal(result.status, "complete");
  assert.deepEqual(result.bytes, bytes);
});

test("missing parts stay pending; a hash mismatch is rejected", async () => {
  const bytes = wav(30000);
  const parts = splitParts(id, bytes);
  const pending = new Reassembler(await metaFor(bytes));
  for (const p of parts.slice(0, -1)) assert.equal((await pending.add(p)).status, "pending");
  const tampered = wav(30000); tampered[100] ^= 1;
  const bad = new Reassembler({ ...(await metaFor(bytes)) });
  let last;
  for (const p of splitParts(id, tampered)) last = await bad.add(p);
  assert.deepEqual(last, { status: "rejected", reason: "hash mismatch" });
  assert.equal((await pending.add({ ...parts[0], index: 99 })).status, "rejected");
});

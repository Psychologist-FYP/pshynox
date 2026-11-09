import { test } from "node:test";
import assert from "node:assert/strict";
import { ChunkReceiver, ClockSync, PatientSender, coverage } from "../src/features/meetings/patientLink.ts";
import { decode, encode } from "../src/features/meetings/protocol.ts";

function wav(seconds, rate = 16000) {
  const samples = Math.round(seconds * rate);
  const buf = new ArrayBuffer(44 + samples * 2);
  const v = new DataView(buf);
  const text = (at, s) => { for (let i = 0; i < s.length; i++) v.setUint8(at + i, s.charCodeAt(i)); };
  text(0, "RIFF"); v.setUint32(4, 36 + samples * 2, true); text(8, "WAVE"); text(12, "fmt "); v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  text(36, "data"); v.setUint32(40, samples * 2, true);
  for (let i = 0; i < samples; i++) v.setInt16(44 + i * 2, (i * 97) % 30000, true);
  return new Blob([buf], { type: "audio/wav" });
}

class MemoryOutbox {
  rows = new Map(); deletes = [];
  async put(meta, bytes) { this.rows.set(meta.chunk_id, { meta, bytes }); }
  async list() { return [...this.rows.values()].sort((a, b) => a.meta.offset_ms - b.meta.offset_ms); }
  async delete(id) { this.deletes.push(id); this.rows.delete(id); }
}

/** Patient <-> clinician over a fake data channel. Every frame goes through encode/decode, like the real channel. */
function link({ dropPart = null, duplicateParts = false, reverseParts = false, upload } = {}) {
  const channel = { readyState: "open", bufferedAmount: 0, bufferedAmountLowThreshold: 0, addEventListener() {} };
  const uploads = [];
  let receiver;
  const sender = { current: null };
  const toPatient = [];
  const toClinicianParts = [];
  const sendFromClinician = m => { toPatient.push(decode(encode(m))); queueMicrotask(() => { const x = toPatient.shift(); if (x.t === "chunk_ack") sender.current.onAck(x); }); return true; };
  receiver = new ChunkReceiver(sendFromClinician, upload ?? (async (meta, bytes) => { uploads.push({ meta, bytes }); }));
  const deliver = async m => {
    if (m.t === "chunk_meta") receiver.onMeta(m);
    else if (m.t === "chunk_part") await receiver.onPart(m);
  };
  const sendFromPatient = m => {
    const wire = decode(encode(m));
    if (wire.t === "chunk_part") {
      if (dropPart !== null && wire.index === dropPart) return true;          // lost in transit (never acknowledged)
      toClinicianParts.push(wire);
      if (duplicateParts) toClinicianParts.push(wire);
      return true;
    }
    void deliver(wire);
    return true;
  };
  const flushParts = async () => { const parts = reverseParts ? toClinicianParts.splice(0).reverse() : toClinicianParts.splice(0); for (const p of parts) await deliver(p); };
  const outbox = new MemoryOutbox();
  const clock = new ClockSync(() => 1000);
  sender.current = new PatientSender(outbox, sendFromPatient, () => channel, clock, 200);
  return { sender: sender.current, receiver, outbox, uploads, channel, flushParts };
}

test("coverage of a window by the union of intervals", () => {
  assert.equal(coverage([0, 100], []), 0);
  assert.equal(coverage([0, 100], [[0, 50], [25, 75]]), 0.75);
  assert.equal(coverage([0, 100], [[-50, 200]]), 1);
  assert.equal(coverage([0, 100], [[90, 100], [0, 10]]), 0.2);
});

test("clock sync keeps the lowest-RTT sample", () => {
  let now = 0;
  const clock = new ClockSync(() => now);
  clock.pong({ t: "clock_pong", seq: 0, t0: -100, meeting_ms: 5000 });   // rtt 100 -> meeting at now ≈ 5050
  clock.pong({ t: "clock_pong", seq: 1, t0: -20, meeting_ms: 5040 });    // rtt 20 -> meeting at now ≈ 5050, better
  assert.equal(clock.rtt, 20);
  assert.equal(clock.uncertainty, 10);
  now = 1000;
  assert.equal(clock.meetingNow(), 6050);
});

test("chunks reassemble despite reordered and duplicated parts; ack before delete; hash verified", async () => {
  const l = link({ reverseParts: true, duplicateParts: true });
  const meta = await l.sender.enqueue(wav(1), 60000);
  assert.equal(l.outbox.rows.size, 1);
  await new Promise(r => setTimeout(r, 20));
  assert.equal(l.outbox.deletes.length, 0);                 // not deleted before the clinician acknowledges
  await l.flushParts();
  await new Promise(r => setTimeout(r, 20));
  assert.equal(l.uploads.length, 1);
  assert.equal(l.uploads[0].meta.chunk_id, meta.chunk_id);
  assert.equal(l.uploads[0].meta.offset_ms, 60000);
  assert.equal(l.uploads[0].meta.duration_ms, 1000);
  assert.deepEqual(l.outbox.deletes, [meta.chunk_id]);
  assert.deepEqual(l.receiver.received, [[60000, 61000]]);
});

test("a lost part means no ack; the chunk stays queued and is resent later", async () => {
  const l = link({ dropPart: 1 });
  await l.sender.enqueue(wav(1), 0);
  await l.flushParts();
  await new Promise(r => setTimeout(r, 300));                // past the 200 ms test ack timeout
  assert.equal(l.uploads.length, 0);
  assert.equal(l.outbox.rows.size, 1);
  assert.equal(l.outbox.deletes.length, 0);
});

test("a failed clinician upload is rejected and the chunk is kept for retry; duplicates are acknowledged once uploaded", async () => {
  let fail = true;
  const uploaded = [];
  const l = link({ upload: async meta => { if (fail) throw new Error("API down"); uploaded.push(meta.chunk_id); } });
  const meta = await l.sender.enqueue(wav(0.5), 0);
  await l.flushParts();
  await new Promise(r => setTimeout(r, 20));
  assert.equal(l.sender.rejected, 1);
  assert.equal(l.outbox.rows.size, 1);
  fail = false;
  void l.sender.pump();
  await new Promise(r => setTimeout(r, 10));
  await l.flushParts();
  await new Promise(r => setTimeout(r, 20));
  assert.deepEqual(uploaded, [meta.chunk_id]);
  assert.equal(l.outbox.rows.size, 0);
  // A retransmitted meta for an already saved chunk is acknowledged without another upload.
  l.receiver.onMeta(meta);
  assert.deepEqual(uploaded, [meta.chunk_id]);
});

test("a tampered chunk is rejected by hash and never uploaded", async () => {
  const l = link();
  l.channel.readyState = "closed";                         // queue only; nothing is sent yet
  const meta = await l.sender.enqueue(wav(0.5), 0);
  await new Promise(r => setTimeout(r, 10));
  l.outbox.rows.get(meta.chunk_id).bytes[500] ^= 0xff;     // corruption after hashing
  l.channel.readyState = "open";
  void l.sender.pump();
  await new Promise(r => setTimeout(r, 10));
  await l.flushParts();
  await new Promise(r => setTimeout(r, 20));
  assert.equal(l.uploads.length, 0);
  assert.ok(l.sender.rejected >= 1);
});

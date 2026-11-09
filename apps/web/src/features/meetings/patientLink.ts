// Patient-side clean recording over the encrypted data channel (docs/INTERNET_MEETINGS_PLAN.md §6–7).
//
// Patient: ClockSync estimates the clinician's meeting clock; PatientSender stores each recorded chunk in the
// encrypted outbox, sends chunk_meta + chunk_part frames with backpressure, and deletes a chunk only after chunk_ack.
// Clinician: ChunkReceiver reassembles, verifies the SHA-256, uploads to the local API, and only then acknowledges.
import { PART_BYTES, Reassembler, sha256Hex, splitParts, type ChunkAck, type ChunkMeta, type ChunkPart, type ClockPong, type Message } from "./protocol";

export type Send = (message: Message) => boolean;
export type Interval = [number, number];

/** Fraction of [start, end) covered by the union of intervals. Pure; used to decide fallback uploads. */
export function coverage([start, end]: Interval, intervals: Interval[]): number {
  if (end <= start) return 1;
  const clipped = intervals.map(([a, b]) => [Math.max(a, start), Math.min(b, end)] as Interval).filter(([a, b]) => b > a).sort((x, y) => x[0] - y[0]);
  let covered = 0, cursor = start;
  for (const [a, b] of clipped) {
    if (b <= cursor) continue;
    covered += b - Math.max(a, cursor);
    cursor = b;
  }
  return covered / (end - start);
}

/** NTP-style estimate of the clinician's meeting clock: the lowest-RTT sample of a few pings wins. */
export class ClockSync {
  private samples: { rtt: number; offset: number }[] = [];
  private seq = 0;
  offset: number | null = null;        // meeting_ms - performance.now()
  rtt = 0;
  private now: () => number;

  constructor(now: () => number = () => performance.now()) { this.now = now; }

  ping(send: Send) { send({ t: "clock_ping", seq: this.seq++, t0: this.now() }); }

  pong(message: ClockPong) {
    const t3 = this.now();
    const rtt = Math.max(0, t3 - message.t0);
    this.samples.push({ rtt, offset: message.meeting_ms + rtt / 2 - t3 });
    this.samples = this.samples.slice(-16);
    const best = this.samples.reduce((a, b) => (b.rtt < a.rtt ? b : a));
    this.offset = best.offset;
    this.rtt = Math.round(best.rtt);
  }

  get ready() { return this.offset !== null; }
  meetingNow() { return this.offset === null ? null : Math.max(0, Math.round(this.now() + this.offset)); }
  get uncertainty() { return Math.ceil(this.rtt / 2); }
}

function wavDuration(bytes: Uint8Array): { rate: number; durationMs: number } {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const rate = view.getUint32(24, true);
  const dataBytes = view.getUint32(40, true);
  return { rate, durationMs: Math.round((dataBytes / 2) * 1000 / rate) };
}

type Outbox = { put(meta: ChunkMeta, bytes: Uint8Array): Promise<void>; list(): Promise<{ meta: ChunkMeta; bytes: Uint8Array }[]>; delete(id: string): Promise<void> };

export class PatientSender {
  private waiters = new Map<string, (ack: ChunkAck) => void>();
  private pumping: Promise<void> | null = null;
  sent = 0;
  acked = 0;
  rejected = 0;
  private outbox: Outbox;
  private send: Send;
  private channel: () => RTCDataChannel | null;
  private clock: ClockSync;
  private ackTimeoutMs: number;

  constructor(outbox: Outbox, send: Send, channel: () => RTCDataChannel | null, clock: ClockSync, ackTimeoutMs = 30000) {
    this.outbox = outbox; this.send = send; this.channel = channel; this.clock = clock; this.ackTimeoutMs = ackTimeoutMs;
  }

  /** Store a recorded WAV chunk (encrypted) with its meeting-relative offset, then try to deliver. */
  async enqueue(blob: Blob, offsetMs: number): Promise<ChunkMeta> {
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const { rate, durationMs } = wavDuration(bytes);
    const meta: ChunkMeta = { t: "chunk_meta", chunk_id: crypto.randomUUID(), offset_ms: Math.max(0, Math.round(offsetMs)),
      offset_uncertainty_ms: Math.min(60000, this.clock.uncertainty), rtt_ms: Math.min(120000, this.clock.rtt), duration_ms: Math.max(1, durationMs),
      rate, bytes: bytes.length, parts: Math.ceil(bytes.length / PART_BYTES), sha256: await sha256Hex(bytes) };
    await this.outbox.put(meta, bytes);
    void this.pump();
    return meta;
  }

  onAck(ack: ChunkAck) { this.waiters.get(ack.chunk_id)?.(ack); }

  /** Deliver every pending chunk, one at a time. A chunk leaves the outbox only after chunk_ack "saved". */
  pump(): Promise<void> {
    if (this.pumping) return this.pumping;
    this.pumping = (async () => {
      try {
        for (const { meta, bytes } of await this.outbox.list()) {
          const ack = await this.deliver(meta, bytes);
          if (!ack) return;                          // channel down or timed out: retry on the next pump
          if (ack.status === "saved") { await this.outbox.delete(meta.chunk_id); this.acked++; }
          else { this.rejected++; return; }          // e.g. the clinician's upload failed; retried later
        }
      } finally { this.pumping = null; }
    })();
    return this.pumping;
  }

  private async deliver(meta: ChunkMeta, bytes: Uint8Array): Promise<ChunkAck | null> {
    const dc = this.channel();
    if (!dc || dc.readyState !== "open") return null;
    const ack = new Promise<ChunkAck | null>(resolve => {
      const timer = setTimeout(() => { this.waiters.delete(meta.chunk_id); resolve(null); }, this.ackTimeoutMs);
      this.waiters.set(meta.chunk_id, a => { clearTimeout(timer); this.waiters.delete(meta.chunk_id); resolve(a); });
    });
    if (!this.send(meta)) return null;
    dc.bufferedAmountLowThreshold = 256 * 1024;
    for (const part of splitParts(meta.chunk_id, bytes)) {
      while (dc.readyState === "open" && dc.bufferedAmount > 1024 * 1024) {
        await new Promise(resolve => dc.addEventListener("bufferedamountlow", resolve, { once: true }));
      }
      if (!this.send(part)) return null;
    }
    this.sent++;
    return ack;
  }
}

export type Upload = (meta: ChunkMeta, bytes: Uint8Array) => Promise<void>;

/** Clinician side: reassemble, verify, upload, then acknowledge. Duplicate chunk ids are acknowledged again. */
export class ChunkReceiver {
  private pending = new Map<string, Reassembler>();
  private done = new Map<string, ChunkMeta>();
  received: Interval[] = [];
  private send: Send;
  private upload: Upload;

  constructor(send: Send, upload: Upload) { this.send = send; this.upload = upload; }

  onMeta(meta: ChunkMeta) {
    if (this.done.has(meta.chunk_id)) { this.send({ t: "chunk_ack", chunk_id: meta.chunk_id, status: "saved" }); return; }
    this.pending.set(meta.chunk_id, new Reassembler(meta));
  }

  async onPart(part: ChunkPart) {
    const r = this.pending.get(part.chunk_id);
    if (!r) return;
    const result = await r.add(part);
    if (result.status === "pending") return;
    this.pending.delete(part.chunk_id);
    if (result.status === "rejected") { this.send({ t: "chunk_ack", chunk_id: part.chunk_id, status: "rejected", reason: result.reason }); return; }
    try {
      await this.upload(r.meta, result.bytes);
    } catch {
      this.send({ t: "chunk_ack", chunk_id: part.chunk_id, status: "rejected", reason: "upload failed; will retry" });
      return;
    }
    this.done.set(r.meta.chunk_id, r.meta);
    this.received.push([r.meta.offset_ms, r.meta.offset_ms + r.meta.duration_ms]);
    this.send({ t: "chunk_ack", chunk_id: r.meta.chunk_id, status: "saved" });
  }

  /** Drop reassemblies that made no progress for a minute (the patient resends after its ack timeout). */
  sweep(now = Date.now()) {
    for (const [id, r] of this.pending) if (now - r.updated > 60000) this.pending.delete(id);
  }
}

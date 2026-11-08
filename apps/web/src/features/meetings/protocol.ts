// Data channel protocol "pshynox-v1" (docs/INTERNET_MEETINGS_PLAN.md §6). Every frame is UTF-8 JSON of at most
// MAX_FRAME bytes: {"v":1,"t":"<type>", ...}. Chat, media state, recording control, clock sync and audio chunks
// travel here, encrypted by WebRTC (SCTP over DTLS), never over the signaling server.
// Pure functions only: no DOM, so node --test can run the unit tests.

export const CHANNEL = "pshynox-v1";
export const VERSION = 1;
export const MAX_FRAME = 16 * 1024;
export const PART_BYTES = 11 * 1024;           // raw bytes per chunk_part: base64 (~15 KiB) + JSON stays under MAX_FRAME
export const MAX_CHAT = 2000;

export type Hello = { t: "hello"; role: "CLINICIAN" | "CLIENT"; caps: { record: boolean; idb: boolean }; app: string };
export type Chat = { t: "chat"; id: string; text: string };
export type Media = { t: "media"; camera: boolean; mic: boolean };
export type RecordingStart = { t: "recording_start"; meeting_ms: number };
export type RecordingStop = { t: "recording_stop"; meeting_ms: number };
export type ClockPing = { t: "clock_ping"; seq: number; t0: number };
export type ClockPong = { t: "clock_pong"; seq: number; t0: number; meeting_ms: number };
export type ChunkMeta = { t: "chunk_meta"; chunk_id: string; offset_ms: number; offset_uncertainty_ms: number; rtt_ms: number; duration_ms: number;
  rate: number; bytes: number; parts: number; sha256: string };
export type ChunkPart = { t: "chunk_part"; chunk_id: string; index: number; data: string };
export type ChunkAck = { t: "chunk_ack"; chunk_id: string; status: "saved" | "rejected"; reason?: string };
export type Verify = { t: "verify"; code_confirmed: boolean };
export type Bye = { t: "bye"; reason: string };
export type Message = Hello | Chat | Media | RecordingStart | RecordingStop | ClockPing | ClockPong | ChunkMeta | ChunkPart | ChunkAck | Verify | Bye;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HEX64 = /^[0-9a-f]{64}$/;
const B64 = /^[A-Za-z0-9+/]*={0,2}$/;
const num = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER) => typeof v === "number" && Number.isFinite(v) && v >= min && v <= max;
const int = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isInteger(v) && num(v, min, max);
const bool = (v: unknown) => typeof v === "boolean";

/** Field checks per type. A frame that fails is dropped by the receiver (and counted), never half-applied. */
const VALID: Record<Message["t"], (m: any) => boolean> = {
  hello: m => (m.role === "CLINICIAN" || m.role === "CLIENT") && typeof m.caps === "object" && m.caps !== null && bool(m.caps.record) && bool(m.caps.idb) && typeof m.app === "string" && m.app.length <= 64,
  chat: m => typeof m.id === "string" && m.id.length <= 64 && typeof m.text === "string" && m.text.trim().length > 0 && m.text.length <= MAX_CHAT,
  media: m => bool(m.camera) && bool(m.mic),
  recording_start: m => num(m.meeting_ms, 0, 4 * 3600 * 1000),
  recording_stop: m => num(m.meeting_ms, 0, 4 * 3600 * 1000),
  clock_ping: m => int(m.seq, 0, 1e6) && num(m.t0),
  clock_pong: m => int(m.seq, 0, 1e6) && num(m.t0) && num(m.meeting_ms, 0, 4 * 3600 * 1000),
  chunk_meta: m => typeof m.chunk_id === "string" && UUID.test(m.chunk_id) && int(m.offset_ms, 0, 4 * 3600 * 1000) && int(m.offset_uncertainty_ms, 0, 60000)
    && int(m.rtt_ms, 0, 120000) && int(m.duration_ms, 1, 61000) && int(m.rate, 8000, 48000) && int(m.bytes, 45, 8 * 1024 * 1024)
    && int(m.parts, 1, 1000) && m.parts === Math.ceil(m.bytes / PART_BYTES) && typeof m.sha256 === "string" && HEX64.test(m.sha256),
  chunk_part: m => typeof m.chunk_id === "string" && UUID.test(m.chunk_id) && int(m.index, 0, 999) && typeof m.data === "string" && m.data.length <= Math.ceil(PART_BYTES / 3) * 4 && B64.test(m.data),
  chunk_ack: m => typeof m.chunk_id === "string" && UUID.test(m.chunk_id) && (m.status === "saved" || m.status === "rejected") && (m.reason === undefined || (typeof m.reason === "string" && m.reason.length <= 200)),
  verify: m => bool(m.code_confirmed),
  bye: m => typeof m.reason === "string" && m.reason.length <= 200,
};

export function encode(message: Message): string {
  const text = JSON.stringify({ v: VERSION, ...message });
  if (new TextEncoder().encode(text).length > MAX_FRAME) throw new Error(`Data channel frame too large (${message.t})`);
  if (!VALID[message.t]?.(message)) throw new Error(`Invalid ${message.t} message`);
  return text;
}

/** Parse one frame. Returns null for anything oversize, malformed, of another version or of an unknown type. */
export function decode(frame: unknown): Message | null {
  if (typeof frame !== "string" || frame.length > MAX_FRAME) return null;
  let value: any;
  try { value = JSON.parse(frame); } catch { return null; }
  if (!value || typeof value !== "object" || value.v !== VERSION || typeof value.t !== "string") return null;
  const check = VALID[value.t as Message["t"]];
  if (!check || !check(value)) return null;
  const { v: _v, ...message } = value;
  return message as Message;
}

// ---------------------------------------------------------------- chunk transfer helpers

export function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

export function fromBase64(text: string): Uint8Array {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

export async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", bytes as BufferSource));
  return Array.from(digest, b => b.toString(16).padStart(2, "0")).join("");
}

export function splitParts(chunkId: string, bytes: Uint8Array): ChunkPart[] {
  const parts: ChunkPart[] = [];
  for (let index = 0, at = 0; at < bytes.length; index++, at += PART_BYTES) {
    parts.push({ t: "chunk_part", chunk_id: chunkId, index, data: toBase64(bytes.subarray(at, at + PART_BYTES)) });
  }
  return parts;
}

export type Assembled = { status: "pending" } | { status: "complete"; bytes: Uint8Array } | { status: "rejected"; reason: string };

/** Collects one chunk's parts in any order; duplicates are ignored. Completion is verified against the SHA-256. */
export class Reassembler {
  readonly parts = new Map<number, Uint8Array>();
  updated = Date.now();
  readonly meta: ChunkMeta;
  constructor(meta: ChunkMeta) { this.meta = meta; }

  async add(part: ChunkPart): Promise<Assembled> {
    if (part.chunk_id !== this.meta.chunk_id) return { status: "rejected", reason: "wrong chunk" };
    if (part.index >= this.meta.parts) return { status: "rejected", reason: "part index out of range" };
    if (!this.parts.has(part.index)) this.parts.set(part.index, fromBase64(part.data));
    this.updated = Date.now();
    if (this.parts.size < this.meta.parts) return { status: "pending" };
    const bytes = new Uint8Array(this.meta.bytes);
    let at = 0;
    for (let i = 0; i < this.meta.parts; i++) {
      const piece = this.parts.get(i)!;
      if (at + piece.length > bytes.length) return { status: "rejected", reason: "size mismatch" };
      bytes.set(piece, at);
      at += piece.length;
    }
    if (at !== this.meta.bytes) return { status: "rejected", reason: "size mismatch" };
    if (await sha256Hex(bytes) !== this.meta.sha256) return { status: "rejected", reason: "hash mismatch" };
    return { status: "complete", bytes };
  }
}

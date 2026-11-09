// Patient-device outbox: recorded chunks wait here until the clinician's computer acknowledges them.
// Each chunk is encrypted with AES-GCM-256 under a per-meeting, non-extractable key that exists only in this page's
// memory (best effort at rest: it protects against reading IndexedDB after the page is gone, not against code
// running in this page). A reload loses the key; leftover ciphertext is then undecryptable and is deleted, and the
// clinician's network-received fallback covers that time.
import type { ChunkMeta } from "./protocol";

export type PendingChunk = { meta: ChunkMeta; bytes: Uint8Array };
type Row = { id: string; meetingId: string; meta: ChunkMeta; iv: Uint8Array; data: ArrayBuffer };

const DB = "pshynox-patient-outbox";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore("chunks", { keyPath: "id" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(new Error("This browser cannot store audio while it is being sent. Check storage permissions and free space."));
  });
}

function tx<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  return open().then(db => new Promise<T>((resolve, reject) => {
    const t = db.transaction("chunks", mode);
    const req = run(t.objectStore("chunks"));
    t.oncomplete = () => { db.close(); resolve(req.result); };
    t.onerror = t.onabort = () => { db.close(); reject(new Error("Could not save audio on this device. Keep this tab open and free up storage.")); };
  }));
}

export class PatientOutbox {
  private key: Promise<CryptoKey>;
  lostOnReload = 0;
  readonly meetingId: string;

  constructor(meetingId: string) {
    this.meetingId = meetingId;
    this.key = crypto.subtle.generateKey({ name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }

  async put(meta: ChunkMeta, bytes: Uint8Array): Promise<void> {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await this.key, bytes as BufferSource);
    await tx("readwrite", s => s.put({ id: meta.chunk_id, meetingId: this.meetingId, meta, iv, data } satisfies Row));
  }

  /** Pending chunks of this meeting in offset order. Rows this key cannot decrypt (from before a reload) are deleted. */
  async list(): Promise<PendingChunk[]> {
    const rows = (await tx("readonly", s => s.getAll())) as Row[];
    const out: PendingChunk[] = [];
    for (const row of rows.filter(r => r.meetingId === this.meetingId)) {
      try {
        const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: row.iv as BufferSource }, await this.key, row.data);
        out.push({ meta: row.meta, bytes: new Uint8Array(plain) });
      } catch {
        this.lostOnReload++;
        await this.delete(row.id);
      }
    }
    return out.sort((a, b) => a.meta.offset_ms - b.meta.offset_ms);
  }

  async delete(id: string): Promise<void> { await tx("readwrite", s => s.delete(id)); }
}

export type Speaker = "CLINICIAN" | "CLIENT";
export type AudioChunk = { id: string; meetingId: string; role: Speaker; offset: number; blob: Blob };

function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open("pshynox-meeting-outbox", 1);
    req.onupgradeneeded = () => req.result.createObjectStore("chunks", { keyPath: "id" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(new Error("Browser audio storage is unavailable. Check storage permissions and free space."));
  });
}
export async function outbox(action: "put" | "delete" | "list", value: AudioChunk | string): Promise<AudioChunk[]> {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("chunks", action === "list" ? "readonly" : "readwrite");
    const store = tx.objectStore("chunks");
    const req = action === "put" ? store.put(value) : action === "delete" ? store.delete(value as string) : store.getAll();
    tx.oncomplete = () => { db.close(); resolve(action === "list" ? (req.result as AudioChunk[]).filter(c => c.meetingId === value).sort((a, b) => a.offset - b.offset) : []); };
    tx.onerror = () => { db.close(); reject(new Error("Could not save audio to browser storage. Keep this tab open and free up disk space.")); };
    tx.onabort = tx.onerror;
  });
}
export function wavBlob(pcm: Int16Array, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const text = (at: number, value: string) => { for (let i = 0; i < value.length; i++) view.setUint8(at + i, value.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + pcm.length * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) view.setInt16(44 + i * 2, pcm[i], true);
  return new Blob([buffer], { type: "audio/wav" });
}

export async function captureAudio(context: AudioContext, streams: [Speaker, MediaStream][], offset: number,
  onChunk: (role: Speaker, offset: number, blob: Blob) => Promise<void>, onError: (message: string) => void) {
  await context.audioWorklet.addModule("/meeting-pcm.js");
  const epoch = Math.round(context.currentTime * context.sampleRate);
  const pending = new Set<Promise<void>>();
  const captures = streams.map(([role, stream]) => {
    const source = context.createMediaStreamSource(new MediaStream(stream.getAudioTracks()));
    const node = new AudioWorkletNode(context, "meeting-pcm", { processorOptions: { epoch }, outputChannelCount: [1] });
    const silent = context.createGain(); silent.gain.value = 0;
    let done: () => void = () => {};
    node.onprocessorerror = () => { onError("Audio capture failed. End the call and check the saved chunks before continuing."); done(); };
    node.port.onmessage = ({ data }) => {
      if (data.done) { done(); return; }
      const task = onChunk(role, offset + Math.round(data.offset * 1000 / data.rate), wavBlob(data.pcm, data.rate));
      pending.add(task);
      void task.catch(e => onError(e instanceof Error ? e.message : "Audio could not be saved.")).finally(() => pending.delete(task));
    };
    source.connect(node).connect(silent).connect(context.destination);
    return { source, node, silent, stop: () => new Promise<void>((resolve) => { done = resolve; node.port.postMessage("stop"); }) };
  });
  return async () => {
    await Promise.all(captures.map(c => c.stop()));
    for (const c of captures) { c.source.disconnect(); c.node.disconnect(); c.silent.disconnect(); c.node.port.close(); }
    await Promise.allSettled([...pending]);
  };
}

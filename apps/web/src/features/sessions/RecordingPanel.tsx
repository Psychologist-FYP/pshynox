import { useEffect, useRef, useState } from "react";
import { api, formatTimestamp, type Recording, type Session, type SpeechJob } from "../../lib/api";

export function RecordingPanel({ session, onTranscribed }: { session: Session; onTranscribed: () => void }) {
  const [consent, setConsent] = useState(session.consent.recording_consent === true);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [device, setDevice] = useState("");
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [level, setLevel] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [files, setFiles] = useState<Recording[]>([]);
  const [jobs, setJobs] = useState<SpeechJob[]>([]);
  const processing = jobs.some(j => j.status === "QUEUED" || j.status === "RUNNING");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const context = useRef<AudioContext | null>(null);
  const mounted = useRef(true);
  const refresh = useRef(onTranscribed);
  refresh.current = onTranscribed;
  useEffect(() => {
    mounted.current = true;
    Promise.all([api.recordings(session.id), api.speechJobs(session.id)]).then(([r, j]) => { if (mounted.current) { setFiles(r); setJobs(j); } }).catch(e => setError(e.message));
    return () => { mounted.current = false; if (recorder.current?.state === "recording") recorder.current.stop(); stream.current?.getTracks().forEach(t => t.stop()); void context.current?.close(); };
  }, [session.id]);
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => {
      api.speechJobs(session.id).then(next => {
        if (!mounted.current) return;
        setJobs(next);
        if (next.some(j => j.status === "SUCCEEDED" && jobs.find(old => old.id === j.id)?.status !== "SUCCEEDED")) refresh.current();
      }).catch(e => setError(e.message));
    }, 1500);
    return () => clearInterval(timer);
  }, [session.id, processing, jobs]);
  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => setSeconds(s => { if (s >= 299) recorder.current?.stop(); return s + 1; }), 1000);
    return () => clearInterval(timer);
  }, [recording]);
  async function chooseDevices() {
    setError("");
    try {
      const permission = await navigator.mediaDevices.getUserMedia({ audio: true });
      permission.getTracks().forEach(t => t.stop());
      setDevices((await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === "audioinput"));
    } catch { setError("Microphone access was denied or no input was found. Check permissions or import a WAV."); }
  }
  async function upload(blob: Blob) {
    setBusy(true); setError("");
    try {
      if (!blob.size) throw new Error("The recording is empty.");
      const r = await api.upload(session.id, blob);
      if (mounted.current) setFiles(current => [...current, r]);
    } catch (e) { if (mounted.current) setError(e instanceof Error ? e.message : "Upload failed. Please try again."); }
    finally { if (mounted.current) setBusy(false); }
  }
  async function start() {
    setError("");
    try {
      await api.recordingConsent(session.id, consent);
      const input = await navigator.mediaDevices.getUserMedia({ audio: { deviceId: device ? { exact: device } : undefined, channelCount: 1 } });
      stream.current = input;
      const mime = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"].find(t => MediaRecorder.isTypeSupported(t));
      if (!mime) throw new Error("This browser has no supported audio recorder. Import a WAV instead.");
      const media = new MediaRecorder(input, { mimeType: mime });
      recorder.current = media;
      const chunks: Blob[] = [];
      media.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
      media.onerror = () => { setError("Recording failed. Check the microphone and try again."); media.stop(); };
      input.getAudioTracks().forEach(t => { t.onended = () => { setError("The microphone disconnected. Check the saved partial recording."); if (media.state === "recording") media.stop(); }; });
      media.onstop = () => {
        input.getTracks().forEach(t => t.stop());
        void context.current?.close(); context.current = null;
        if (mounted.current) { setRecording(false); setLevel(0); void upload(new Blob(chunks, { type: mime })); }
      };
      const ac = new AudioContext(); context.current = ac;
      const analyser = ac.createAnalyser(); analyser.fftSize = 256;
      ac.createMediaStreamSource(input).connect(analyser);
      const data = new Uint8Array(analyser.fftSize);
      function meter() {
        if (ac.state === "closed" || !mounted.current) return;
        analyser.getByteTimeDomainData(data);
        setLevel(Math.min(100, Math.max(...data.map(v => Math.abs(v - 128))) * 3));
        requestAnimationFrame(meter);
      }
      media.start(1000); setRecording(true); setSeconds(0); meter();
    } catch (e) {
      stream.current?.getTracks().forEach(t => t.stop());
      void context.current?.close(); context.current = null;
      setError(e instanceof Error ? e.message : "Could not start recording.");
    }
  }
  return <section className="panel recording-panel"><div className="panel-heading"><div><p className="eyebrow">Local audio · up to five minutes</p><h3>Record or import a conversation</h3></div></div>
    <label className="confirm-line"><input type="checkbox" checked={consent} disabled={busy || recording || processing} onChange={async e => { const value = e.target.checked; try { await api.recordingConsent(session.id, value); setConsent(value); } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not save consent."); } }} /> I have consent to record and process this fictional conversation locally.</label>
    <div className="recording-actions"><button className="button button-secondary small-button" disabled={!consent || recording || busy} onClick={chooseDevices}>Find microphones</button><select aria-label="Microphone" disabled={recording} value={device} onChange={e => setDevice(e.target.value)}><option value="">Default microphone</option>{devices.map(d => <option key={d.deviceId} value={d.deviceId}>{d.label || "Microphone"}</option>)}</select>
      <button className="button button-primary small-button" disabled={!consent || busy || processing} onClick={() => recording ? recorder.current?.stop() : void start()}>{recording ? "Stop and save" : "Start recording"}</button>
      <label>Import WAV <input aria-label="Import WAV" type="file" accept=".wav,audio/wav" disabled={!consent || busy || recording || processing} onChange={e => { const file = e.target.files?.[0]; if (file) void upload(new Blob([file], { type: "audio/wav" })); e.target.value = ""; }} /></label>
    </div>
    {recording && <p role="status">Recording {formatTimestamp(seconds * 1000)} <meter aria-label="Input level" min={0} max={100} value={level} /> Check that both voices register.</p>}
    {busy && <p role="status">Saving and validating recording…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {files.map((file, index) => {
      const job = jobs.find(j => j.recording_id === file.id);
      const pending = job?.status === "QUEUED" || job?.status === "RUNNING";
      return <div className="saved-recording" key={file.id}>
        <span>{file.meeting_id ? `${file.speaker_role === "CLIENT" ? "Patient" : "Clinician"} · meeting ${formatTimestamp(file.offset_ms ?? 0)}` : `Recording ${index + 1}`} · {formatTimestamp(file.duration_ms)} · {file.channels} channel(s)</span>
        <audio id={`audio-${file.id}`} data-offset-ms={file.offset_ms ?? 0} controls preload="none" src={`/api/v1/sessions/${session.id}/recordings/${file.id}/audio`} />
        <button className="button button-secondary small-button" disabled={!consent || busy || recording || pending || job?.status === "SUCCEEDED"} onClick={async () => {
          setError(""); setBusy(true);
          try {
            const next = await api.transcribe(session.id, file.id, crypto.randomUUID());
            setJobs(current => [...current.filter(j => j.id !== next.id), next]);
          } catch (e) { setError(e instanceof Error ? e.message : "Could not queue transcription."); }
          finally { setBusy(false); }
        }}>{job?.status === "SUCCEEDED" ? "Transcribed" : pending ? job.status === "QUEUED" ? "Queued" : "Transcribing…" : job?.status === "FAILED" ? "Retry transcription" : "Transcribe locally"}</button>
        {file.channels === 2 && <span className="small-note">Stereo will be mixed for transcription. Original audio is preserved; assign speaker roles manually.</span>}
        {job && <p role="status">Recording {index + 1}: {job.status.toLowerCase()}{job.error_message ? ` — ${job.error_message}` : ""}</p>}
      </div>;
    })}
    <p className="small-note">A single microphone cannot reliably identify speakers. Check both voices are audible and review the transcript before populating a draft.</p>
  </section>;
}

import { useEffect, useRef, useState } from "react";
import { Brand } from "../../components/layout/AppShell";
import { api, formatTimestamp, type TranscriptSegment } from "../../lib/api";
import { captureAudio, outbox, type AudioChunk } from "./audio";
import { meetingRequest, tokenFor, type MeetingInfo } from "./api";
import "./meeting.css";

type Signal = { type: string; data?: any; offset_ms?: number; role?: string };
type Message = { role: string; text: string };

function VideoTile({ stream, local, camera, label }: { stream: MediaStream | null; local?: boolean; camera: boolean; label: string }) {
  const ref = useRef<HTMLVideoElement>(null);
  const [blocked, setBlocked] = useState(false);
  useEffect(() => {
    if (ref.current) { ref.current.srcObject = stream; void ref.current.play().catch(() => setBlocked(true)); }
  }, [stream]);
  return <div className={`call-tile ${local ? "self-tile" : ""}`}>
    <video ref={ref} autoPlay playsInline muted={local} className={camera && stream?.getVideoTracks().length ? "" : "camera-hidden"} />
    {(!camera || !stream?.getVideoTracks().length) && <div className="call-avatar"><span>{label[0]}</span><p>{stream ? "Camera off. Still listening." : "Waiting for the other chair to fill."}</p></div>}
    <span className="tile-name">{label}{local ? " (you)" : ""}</span>
    {blocked && <button className="button button-secondary playback-unlock" onClick={() => void ref.current?.play().then(() => setBlocked(false))}>Enable call audio</button>}
  </div>;
}

export function MeetingRoom({ id }: { id: string }) {
  const [token] = useState(() => tokenFor(id));
  const [info, setInfo] = useState<MeetingInfo | null>(null);
  const [error, setError] = useState("");
  const [saveError, setSaveError] = useState("");
  const [status, setStatus] = useState("Checking your invitation…");
  const [consent, setConsent] = useState(false);
  const [joined, setJoined] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ending, setEnding] = useState(false);
  const [finished, setFinished] = useState(false);
  const [local, setLocal] = useState<MediaStream | null>(null);
  const [remote, setRemote] = useState<MediaStream | null>(null);
  const [mic, setMic] = useState(true);
  const [camera, setCamera] = useState(true);
  const [cameraBusy, setCameraBusy] = useState(false);
  const [peerCamera, setPeerCamera] = useState(true);
  const [peerMic, setPeerMic] = useState(true);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [pendingCount, setPendingCount] = useState(0);
  const [messages, setMessages] = useState<Message[]>([]);
  const [chat, setChat] = useState("");
  const [tab, setTab] = useState("chat");
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([]);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [audioDevice, setAudioDevice] = useState("");
  const [videoDevice, setVideoDevice] = useState("");
  const [invite] = useState(() => sessionStorage.getItem(`meeting-invite-${id}`) || "");
  const [copied, setCopied] = useState(false);
  const infoRef = useRef(info); infoRef.current = info;
  const localRef = useRef<MediaStream | null>(null);
  const remoteRef = useRef<MediaStream | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const peer = useRef<RTCPeerConnection | null>(null);
  const audioContext = useRef<AudioContext | null>(null);
  const stopCapture = useRef<(() => Promise<void>) | null>(null);
  const captureStarting = useRef<Promise<void> | null>(null);
  const memoryChunks = useRef(new Map<string, AudioChunk>());
  const pumping = useRef<Promise<boolean> | null>(null);
  const roomClock = useRef({ offset: 0, received: performance.now() });
  const active = useRef(false);
  const leaving = useRef(false);
  const recordingRef = useRef(false);
  const mounted = useRef(true);
  const captureGeneration = useRef(0);
  const isHost = info?.role === "CLINICIAN";
  const secure = window.isSecureContext && !!navigator.mediaDevices;

  function send(type: string, data?: unknown) {
    if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({ type, data }));
  }
  function setRecordingState(value: boolean) { recordingRef.current = value; setRecording(value); send("recording", value); }

  async function drain(): Promise<boolean> {
    if (pumping.current) return pumping.current;
    const task = (async () => {
      try {
        const disk = await outbox("list", id);
        const chunks = new Map(disk.map(c => [c.id, c]));
        memoryChunks.current.forEach((v, k) => chunks.set(k, v));
        setPendingCount(chunks.size);
        for (const chunk of chunks.values()) {
          await meetingRequest(`meetings/${id}/chunks/${chunk.id}?speaker_role=${chunk.role}&offset_ms=${chunk.offset}`, token,
            { method: "POST", headers: { "Content-Type": "audio/wav" }, body: chunk.blob, signal: AbortSignal.timeout(20000) });
          await outbox("delete", chunk.id);
          memoryChunks.current.delete(chunk.id);
          setPendingCount(n => Math.max(0, n - 1));
        }
        setSaveError("");
        return true;
      } catch (e) { setSaveError((e as Error).message); return false; }
    })();
    pumping.current = task;
    try { return await task; } finally { pumping.current = null; }
  }

  async function haltCapture() {
    captureGeneration.current++;
    if (captureStarting.current) await captureStarting.current;
    const stop = stopCapture.current;
    stopCapture.current = null;
    if (stop) await stop();
    setRecordingState(false);
  }

  function startCapture() {
    if (!active.current || leaving.current || !isHost || stopCapture.current || captureStarting.current || !remoteRef.current?.getAudioTracks().length || !localRef.current || !audioContext.current) return;
    const generation = captureGeneration.current;
    const task = (async () => {
      try {
        const offset = roomClock.current.offset + Math.round(performance.now() - roomClock.current.received);
        const stop = await captureAudio(audioContext.current!, [["CLINICIAN", localRef.current!], ["CLIENT", remoteRef.current!]], offset,
          async (role, at, blob) => {
            const chunk: AudioChunk = { id: crypto.randomUUID(), meetingId: id, role, offset: at, blob };
            memoryChunks.current.set(chunk.id, chunk);
            await outbox("put", chunk);
            memoryChunks.current.delete(chunk.id);
            const backlog = (await outbox("list", id)).length;
            setPendingCount(backlog);
            if (backlog >= 20) { setSaveError("Uploads are falling behind. Recording has paused; retry uploads before continuing."); void haltCapture(); }
            void drain();
          }, message => { setSaveError(message); void haltCapture(); });
        if (!active.current || leaving.current || generation !== captureGeneration.current) { await stop(); return; }
        stopCapture.current = stop;
        setRecordingState(true);
      } catch (e) { setSaveError(`Recording could not start: ${(e as Error).message}`); }
    })();
    captureStarting.current = task;
    void task.finally(() => { captureStarting.current = null; });
  }

  function makePeer() {
    peer.current?.close();
    const pc = new RTCPeerConnection({ iceServers: infoRef.current?.ice_servers || [] });
    peer.current = pc;
    localRef.current!.getTracks().forEach(track => pc.addTrack(track, localRef.current!));
    // The offer must reserve video even when the clinician joins audio only.
    // Otherwise the patient's answer cannot include a camera track.
    if (isHost && !localRef.current!.getVideoTracks().length) {
      pc.addTransceiver("video", { direction: "sendrecv", streams: [localRef.current!] });
    }
    const received = new MediaStream();
    pc.onicecandidate = e => { if (e.candidate) send("ice", e.candidate.toJSON()); };
    pc.ontrack = e => {
      if (peer.current !== pc) return;
      // Track events can be streamless, and audio/video may arrive separately.
      received.addTrack(e.track);
      remoteRef.current = received;
      setRemote(new MediaStream(received.getTracks()));
      if (pc.connectionState === "connected") startCapture();
    };
    pc.onconnectionstatechange = () => {
      if (peer.current !== pc) return;
      if (pc.connectionState === "connected") { setStatus("Connected"); startCapture(); }
      else if (["disconnected", "failed"].includes(pc.connectionState)) {
        setStatus("Connection interrupted — recording paused"); void haltCapture();
        if (pc.connectionState === "failed") setError("The media connection failed. Leave and rejoin; different networks may require a TURN relay.");
      }
    };
    return pc;
  }

  async function preview() {
    setBusy(true); setError("");
    localRef.current?.getTracks().forEach(t => t.stop());
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { deviceId: audioDevice ? { exact: audioDevice } : undefined, echoCancellation: true, noiseSuppression: true, channelCount: 1 },
        video: camera ? { deviceId: videoDevice ? { exact: videoDevice } : undefined, width: { ideal: 1280 }, height: { ideal: 720 } } : false,
      });
      localRef.current = stream; setLocal(stream);
      stream.getAudioTracks().forEach(t => { t.enabled = mic; t.onended = () => { setError("The microphone disconnected. Leave and rejoin with a working device."); void haltCapture(); }; });
      setDevices(await navigator.mediaDevices.enumerateDevices());
    } catch (e) { localRef.current = null; setLocal(null); setError(`Could not open the selected devices. Check browser permissions, or turn the camera off and try audio only. ${(e as Error).message}`); }
    finally { setBusy(false); }
  }

  async function join() {
    if (!localRef.current || !consent || !info) return;
    setBusy(true); setError(""); leaving.current = false;
    try {
      if (isHost) {
        // Storage and AudioContext permissions are checked during the user's gesture.
        await outbox("list", id);
        audioContext.current = new AudioContext({ sampleRate: 16000 });
        await audioContext.current.resume();
        audioContext.current.onstatechange = () => {
          if (audioContext.current?.state === "suspended" && recordingRef.current) {
            setError("The browser suspended audio capture. Keep this tab and device awake; leave and rejoin to resume recording.");
            void haltCapture();
          }
        };
      }
      const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/meetings/${id}/signal`);
      socket.current = ws;
      const candidates: RTCIceCandidateInit[] = [];
      let chain = Promise.resolve();
      ws.onopen = () => ws.send(JSON.stringify({ token, recording_consent: true }));
      ws.onmessage = event => {
        chain = chain.then(async () => {
          const message: Signal = JSON.parse(event.data);
          if (message.type === "joined") { active.current = true; setJoined(true); setBusy(false); setStatus("Waiting for the other participant…"); }
          if (message.type === "ready") {
            await haltCapture();
            roomClock.current = { offset: message.offset_ms || 0, received: performance.now() };
            const pc = makePeer();
            setStatus("Connecting your call…");
            send("media", { camera: localRef.current!.getVideoTracks().some(t => t.enabled), mic: localRef.current!.getAudioTracks().some(t => t.enabled) });
            if (isHost) { const offer = await pc.createOffer(); await pc.setLocalDescription(offer); send("offer", pc.localDescription); }
          }
          if (message.type === "offer" && !isHost && peer.current) {
            await peer.current.setRemoteDescription(message.data);
            // Keep the patient's sending direction available for a camera
            // first enabled after joining, without renegotiating the call.
            peer.current.getTransceivers().filter(t => t.receiver.track.kind === "video")
              .forEach(t => { t.direction = "sendrecv"; });
            for (const c of candidates.splice(0)) await peer.current.addIceCandidate(c);
            await peer.current.setLocalDescription(await peer.current.createAnswer()); send("answer", peer.current.localDescription);
          }
          if (message.type === "answer" && isHost && peer.current) {
            await peer.current.setRemoteDescription(message.data);
            for (const c of candidates.splice(0)) await peer.current.addIceCandidate(c);
          }
          if (message.type === "ice") {
            if (peer.current?.remoteDescription) await peer.current.addIceCandidate(message.data);
            else candidates.push(message.data);
          }
          if (message.type === "media") { setPeerCamera(message.data.camera === true); setPeerMic(message.data.mic === true); }
          if (message.type === "recording" && !isHost) setRecording(message.data === true);
          if (message.type === "chat") setMessages(list => [...list.slice(-199), { role: message.role || "Participant", text: message.data }]);
          if (message.type === "peer-left") {
            await haltCapture(); peer.current?.close(); peer.current = null; remoteRef.current = null; setRemote(null);
            setStatus("The other participant left. Waiting for them to rejoin…");
          }
          if (message.type === "ended") { await leave(false); setFinished(true); setStatus("Meeting ended"); }
        }).catch(e => setError(`Connection error: ${(e as Error).message}`));
      };
      ws.onerror = () => setError("Could not reach the meeting server. Check the network and invitation address.");
      ws.onclose = event => {
        if (socket.current !== ws) return;
        setBusy(false);
        if (!leaving.current) {
          const reason = event.code === 4409 ? "This participant is already connected in another tab. Use the patient invitation for the second participant, not the clinician’s address."
            : event.code === 4403 ? "This invitation is closed, invalid, or missing recording consent. Ask for a new invitation."
            : "Meeting connection closed. Saved audio is retained. Check the network and rejoin.";
          setError(reason); void leave(false);
        }
      };
    } catch (e) { setError((e as Error).message); setBusy(false); void audioContext.current?.close(); audioContext.current = null; }
  }

  async function leave(endForAll: boolean) {
    if (leaving.current) return;
    leaving.current = true; active.current = false; setEnding(true);
    await haltCapture();
    send("leave"); socket.current?.close(); socket.current = null;
    peer.current?.close(); peer.current = null;
    localRef.current?.getTracks().forEach(t => t.stop()); localRef.current = null; setLocal(null);
    remoteRef.current = null; setRemote(null);
    await audioContext.current?.close(); audioContext.current = null;
    setJoined(false); setBusy(false);
    if (isHost) {
      // A prior pump may have taken its snapshot before the final partial chunks.
      await drain();
      const saved = await drain();
      if (endForAll && saved) {
        try { await meetingRequest(`meetings/${id}/end`, token, { method: "POST" }); setFinished(true); setStatus("Meeting ended — audio saved"); }
        catch (e) { setError((e as Error).message); }
      } else if (endForAll) setStatus("Call stopped. Retry audio uploads, then finish the meeting.");
    }
    setEnding(false); leaving.current = false;
  }

  useEffect(() => {
    mounted.current = true;
    if (!token) { setError("This invitation is missing its access token. Ask the clinician for the complete link."); return; }
    let cancelled = false;
    const refresh = async () => {
      try {
        const value = await meetingRequest<MeetingInfo>(`meetings/${id}`, token);
        if (cancelled) return;
        setInfo(value);
        if (value.status === "ENDED") { setFinished(true); setStatus("Meeting ended"); }
        else if (!active.current) setStatus("A quick check before you join");
        if (value.role === "CLINICIAN" && value.session_id) {
          const rows = await api.transcript(value.session_id);
          if (!cancelled) setTranscript(rows);
          void drain();
        }
      } catch (e) { if (!cancelled) setError((e as Error).message); }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4000);
    const clock = window.setInterval(() => { if (active.current) setSeconds(Math.max(0, Math.floor((roomClock.current.offset + performance.now() - roomClock.current.received) / 1000))); }, 1000);
    const unload = (event: BeforeUnloadEvent) => { if (active.current || memoryChunks.current.size || recordingRef.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", unload);
    return () => {
      cancelled = true; mounted.current = false; clearInterval(timer); clearInterval(clock); window.removeEventListener("beforeunload", unload);
      active.current = false; leaving.current = true;
      socket.current?.close(); peer.current?.close();
      localRef.current?.getTracks().forEach(t => t.stop());
      void (async () => { await haltCapture(); await audioContext.current?.close(); })();
    };
  }, [id, token]);

  const jobs = info?.jobs || [];
  const completed = jobs.filter(j => j.status === "SUCCEEDED").length;
  const failed = jobs.filter(j => j.status === "FAILED").length;
  function toggleMic() { const next = !mic; setMic(next); localRef.current?.getAudioTracks().forEach(t => { t.enabled = next; }); send("media", { mic: next, camera }); }
  async function toggleCamera() {
    if (cameraBusy) return;
    const next = !camera;
    const stream = localRef.current;
    if (!stream) { setCamera(next); return; }
    const existing = stream.getVideoTracks().find(t => t.readyState === "live");
    if (!next || existing) {
      stream.getVideoTracks().forEach(t => { t.enabled = next; });
      setCamera(next);
      send("media", { mic: stream.getAudioTracks().some(t => t.enabled), camera: next });
      return;
    }
    setCameraBusy(true);
    let captured: MediaStream | null = null;
    try {
      captured = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { deviceId: videoDevice ? { exact: videoDevice } : undefined, width: { ideal: 1280 }, height: { ideal: 720 } },
      });
      if (!mounted.current || leaving.current || localRef.current !== stream) {
        captured.getTracks().forEach(t => t.stop());
        return;
      }
      const track = captured.getVideoTracks()[0];
      const pc = peer.current;
      if (pc) {
        const transceiver = pc.getTransceivers().find(t => t.receiver.track.kind === "video");
        if (transceiver) await transceiver.sender.replaceTrack(track);
        else pc.addTrack(track, stream); // Patient may still be waiting for the offer.
      }
      if (!mounted.current || leaving.current || localRef.current !== stream) {
        captured.getTracks().forEach(t => t.stop());
        return;
      }
      stream.getVideoTracks().forEach(t => { stream.removeTrack(t); t.stop(); });
      stream.addTrack(track);
      setLocal(new MediaStream(stream.getTracks()));
      setCamera(true);
      send("media", { mic: stream.getAudioTracks().some(t => t.enabled), camera: true });
    } catch (e) {
      captured?.getTracks().forEach(t => t.stop());
      setError(`Could not turn on the camera. Check browser permissions and try again. ${(e as Error).message}`);
    } finally { setCameraBusy(false); }
  }

  return <div className="meeting-app"><header className="meeting-header"><Brand /><div><p className="eyebrow">The office, with a longer cable</p><h1>Online session</h1></div><span className="meeting-clock">{joined ? formatTimestamp(seconds * 1000) : "Two chairs. One conversation."}</span></header>
    <main className="meeting-main">
      <div className="meeting-status" role="status"><strong>{status}</strong><span>{recording ? "Audio recording · separate speaker tracks" : "Audio recording is not active"}</span></div>
      {error && <div className="meeting-alert" role="alert">{error}</div>}
      {saveError && <div className="meeting-alert" role="alert"><strong>Audio needs attention.</strong> {saveError} <button className="text-button" onClick={() => void drain()}>Retry uploads</button><p>Keep this tab open. Saved browser chunks remain queued until the server confirms receipt.</p></div>}
      {!secure && <div className="meeting-alert">Microphone and camera need a secure connection. On the hosting laptop, use localhost. On another Wi-Fi device, open the trusted HTTPS invitation address. See docs/ONLINE_MEETINGS.md.</div>}
      {finished ? <section className="meeting-lobby panel"><p className="eyebrow">The chairs can take a break</p><h2>That’s the conversation wrapped up.</h2><p>{isHost ? "Saved audio continues transcribing in the background. Review the speaker-labelled transcript before populating the note." : "You have left the meeting. Your microphone and camera are off."}</p>{isHost && info?.session_id && <a className="button button-primary" href={`/workspace/sessions/${info.session_id}`}>Return to session & transcript</a>}</section> : <>
        {!joined && <section className="meeting-lobby panel"><div><p className="eyebrow">{isHost ? "Clinician preflight" : "Your invitation"}</p><h2>Good to see you.<br />Let’s check the microphone.</h2><p>This call records both voices separately on the clinician’s computer and transcribes them locally. Video and chat are not saved.</p><p>Use headphones to keep the other person’s voice out of your microphone. Stay on this tab and keep the device awake during recording.</p><label className="confirm-line"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} disabled={busy || ending} /> I consent to audio recording and local transcription of this fictional session.</label>
          <div className="device-settings"><label>Microphone<select value={audioDevice} disabled={busy} onChange={e => { setAudioDevice(e.target.value); localRef.current?.getTracks().forEach(t => t.stop()); localRef.current = null; setLocal(null); }}><option value="">Default microphone</option>{devices.filter(d => d.kind === "audioinput").map(d => <option key={d.deviceId} value={d.deviceId}>{d.label || "Microphone"}</option>)}</select></label><label>Camera<select value={videoDevice} disabled={busy} onChange={e => { setVideoDevice(e.target.value); localRef.current?.getTracks().forEach(t => t.stop()); localRef.current = null; setLocal(null); }}><option value="">Default camera</option>{devices.filter(d => d.kind === "videoinput").map(d => <option key={d.deviceId} value={d.deviceId}>{d.label || "Camera"}</option>)}</select></label></div>
          <div className="meeting-lobby-actions"><button className="button button-secondary" disabled={!secure || busy || !info || ending} onClick={() => void preview()}>{local ? "Refresh preview" : "Check camera & microphone"}</button><button className="button button-primary" disabled={!local || !consent || busy || !info || ending} onClick={() => void join()}>Join meeting</button></div>
          {isHost && <button className="text-button" disabled={ending || busy} onClick={() => void leave(true)}>End meeting and close invitation</button>}
        </div><div><VideoTile stream={local} local camera={camera} label={isHost ? "Clinician" : "Patient"} /><button className="text-button" disabled={busy || cameraBusy || ending} onClick={() => void toggleCamera()}>{cameraBusy ? "Opening camera…" : camera ? "Turn camera off / audio only" : "Turn camera on"}</button></div></section>}
        {joined && <div className="meeting-layout"><section className="call-stage"><VideoTile stream={remote} camera={peerCamera} label={isHost ? "Patient" : "Clinician"} /><VideoTile stream={local} local camera={camera} label={isHost ? "Clinician" : "Patient"} /><p className="call-caption">{remote ? peerMic ? "Both participants connected" : "The other participant’s microphone is muted" : "Share the invitation. The other chair is ready."}</p>
          <div className="call-controls" aria-label="Meeting controls"><button aria-pressed={!mic} onClick={toggleMic}>{mic ? "Mute microphone" : "Unmute microphone"}</button><button aria-pressed={!camera} disabled={cameraBusy || ending} onClick={() => void toggleCamera()}>{cameraBusy ? "Opening camera…" : camera ? "Turn camera off" : "Turn camera on"}</button><button onClick={() => setTab("chat")}>Chat ({messages.length})</button>{isHost && !recording && remote && <button onClick={startCapture}>Resume recording</button>}<button className="leave-call" disabled={ending} onClick={() => void leave(!!isHost)}>{ending ? "Saving final audio…" : isHost ? "End meeting" : "Leave meeting"}</button></div>
        </section><aside className="meeting-side"><div className="meeting-tabs"><button aria-pressed={tab === "chat"} onClick={() => setTab("chat")}>Chat</button>{isHost && <button aria-pressed={tab === "transcript"} onClick={() => setTab("transcript")}>Live transcript</button>}</div>{tab === "chat" ? <><div className="meeting-chat" aria-live="polite">{!messages.length && <p className="empty-chat">A quiet chat. We’ll try not to take it personally.<small>Messages are visible to both participants and disappear when this page closes.</small></p>}{messages.map((m, i) => <div className={`chat-message ${m.role === info?.role ? "own-message" : ""}`} key={i}><strong>{m.role === "CLIENT" ? "Patient" : "Clinician"}</strong><p>{m.text}</p></div>)}</div><form className="chat-form" onSubmit={e => { e.preventDefault(); if (!chat.trim() || !remote) return; send("chat", chat.trim()); setMessages(list => [...list.slice(-199), { role: info!.role, text: chat.trim() }]); setChat(""); }}><input aria-label="Chat message" placeholder="Something to add?" maxLength={2000} value={chat} onChange={e => setChat(e.target.value)} /><button disabled={!chat.trim() || !remote}>Send</button></form></> : <div className="meeting-chat" aria-live="polite"><p className="small-note">Draft ASR text appears after each chunk finishes processing. Review for errors.</p>{transcript.slice(-30).map(s => <article className="live-segment" key={s.id}><strong>{s.speaker_role === "CLIENT" ? "Patient" : s.speaker_role === "CLINICIAN" ? "Clinician" : "Unknown"} · {formatTimestamp(s.start_ms)}</strong><p>{s.text}</p></article>)}{!transcript.length && <p>The transcript is listening. First results arrive after the first minute plus processing time.</p>}</div>}</aside></div>}
      </>}
      {isHost && <section className="meeting-bottom"><div className="meeting-invite panel"><p className="eyebrow">Invite the other chair</p><label htmlFor="meeting-invite">Patient invitation — share only with your patient</label><div><input id="meeting-invite" readOnly value={invite} placeholder="Invitation is available in the tab that created this meeting." /><button className="button button-secondary" disabled={!invite} onClick={async () => { try { await navigator.clipboard.writeText(invite); setCopied(true); } catch { setError("Select the invitation text and copy it manually."); } }}>{copied ? "Copied" : "Copy link"}</button></div>{invite.includes("localhost") || invite.includes("127.0.0.1") ? <p className="form-error">This address works only on this laptop. Set MEETING_PUBLIC_ORIGIN to your trusted HTTPS Wi-Fi address and create a new invitation for another device.</p> : <p className="small-note">Invitation expires after four hours or when you end the meeting.</p>}</div><div className="meeting-progress panel"><p className="eyebrow">Behind the conversation</p><strong>{completed} / {jobs.length} chunks transcribed</strong><p>{pendingCount} awaiting upload · {jobs.length - completed - failed} processing or queued · {failed} failed</p><p className="small-note">One continuous audio timeline, two speaker tracks, roughly one-minute WAV files. Processing can continue after the call.</p>{failed > 0 && <p className="form-error">{jobs.find(j => j.status === "FAILED")?.error_message} Retry failed transcription from the session page.</p>}</div></section>}
    </main><footer className="meeting-footer">Audio is saved on the clinician’s computer. Speaker labels follow microphone tracks; review cross-talk and transcription errors.</footer>
  </div>;
}

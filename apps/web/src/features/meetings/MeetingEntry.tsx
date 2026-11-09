import { useEffect, useState } from "react";
import { meetingRequest } from "./api";

type Usage = { month: string; meetings: number; relayed_meetings: number; relayed_bytes: number; estimated_cost_usd: number; turn_provider: string | null; audio_only_default: boolean; mode: string };

export function MeetingEntry({ sessionId }: { sessionId: string }) {
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const saved = sessionStorage.getItem(`session-meeting-${sessionId}`);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [audioOnly, setAudioOnly] = useState<boolean | null>(null);
  useEffect(() => { void meetingRequest<Usage>("meeting-usage").then(u => { setUsage(u); setAudioOnly(a => a ?? u.audio_only_default); }).catch(() => undefined); }, []);
  async function create() {
    setBusy(true); setError("");
    try {
      const room = await meetingRequest<{ id: string; host_token: string; guest_token: string; public_origin: string }>(`sessions/${sessionId}/meetings`, "", { method: "POST", body: JSON.stringify({ recording_consent: consent, ...(audioOnly === null ? {} : { audio_only: audioOnly }) }) });
      sessionStorage.setItem(`meeting-token-${room.id}`, room.host_token);
      sessionStorage.setItem(`meeting-invite-${room.id}`, `${room.public_origin || location.origin}/meeting/${room.id}#token=${room.guest_token}`);
      sessionStorage.setItem(`session-meeting-${sessionId}`, room.id);
      location.assign(`/meeting/${room.id}`);
    } catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <section className="panel meeting-entry"><div><p className="eyebrow">Same conversation. Different chairs.</p><h3>Meet online</h3><p>Invite your patient to a private two-person call. Each voice is saved separately and transcribed locally while you talk.</p></div>
    <div className="meeting-entry-actions"><label className="confirm-line"><input type="checkbox" checked={consent} disabled={busy} onChange={e => setConsent(e.target.checked)} /> I consent to recording and local transcription of this fictional session. The patient will be asked separately.</label>
    <label className="confirm-line"><input type="checkbox" checked={audioOnly ?? false} disabled={busy} onChange={e => setAudioOnly(e.target.checked)} /> Audio only (no video; keeps relayed internet traffic small)</label>
    <button className="button button-primary" disabled={!consent || busy} onClick={() => void create()}>{busy ? "Opening the office…" : "Start online meeting"}</button>
    {saved && <a className="text-link" href={`/meeting/${saved}`}>Resume existing meeting</a>}
    <button className="text-button" disabled={busy} onClick={async () => { if (!confirm("Close any existing invitation for this session? Do not do this while audio is still uploading.")) return; try { await meetingRequest(`sessions/${sessionId}/meetings/close`, "", { method: "POST" }); sessionStorage.removeItem(`session-meeting-${sessionId}`); setError("Previous meeting closed. You can create a new invitation."); } catch (e) { setError((e as Error).message); } }}>Close previous invitation</button>
    {error && <p role="alert" className="form-error">{error}</p>}
    {usage && <p className="small-note">{usage.mode === "internet" ? "Internet meetings" : "Same-Wi-Fi meetings"} · TURN relay: {usage.turn_provider ?? "not configured"} · This month: {usage.meetings} meetings, {usage.relayed_meetings} relayed, {(usage.relayed_bytes / 1e6).toFixed(1)} MB relayed (≈ ${usage.estimated_cost_usd.toFixed(2)})</p>}</div></section>;
}

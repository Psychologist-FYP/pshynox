import { useEffect, useState } from "react";
import { WorkspaceShell } from "../../components/layout/AppShell";
import { Icon } from "../../components/ui/Icon";
import { api, formatDate, formatTimestamp, type EvidenceHit, type Health, type Session, type TranscriptSegment } from "../../lib/api";

export function SessionDetailPage({ health, session, error }: { health: Health | null; session: Session | null; error: string | null }) {
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([]);
  const [evidence, setEvidence] = useState<EvidenceHit[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("sleep difficulty");
  const [searching, setSearching] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(error);

  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    Promise.all([api.transcript(session.id, controller.signal), api.evidence(session.id, query, controller.signal)])
      .then(([segments, hits]) => { setTranscript(segments); setEvidence(hits); setLoading(false); })
      .catch((reason: Error) => { if (reason.name !== "AbortError") { setDetailError(reason.message); setLoading(false); } });
    return () => controller.abort();
  }, [session, query]);

  if (!session) return <WorkspaceShell health={health} active="Sessions"><main className="section-wrap"><div className="state-panel state-error"><strong>Session unavailable</strong><span>{detailError ?? "This session could not be found."}</span><a href="/workspace">Return to workspace</a></div></main></WorkspaceShell>;
  return <WorkspaceShell health={health} active="Sessions"><main className="detail-page section-wrap">
    <a className="back-link" href="/workspace"><Icon name="chevron" /> Back to workspace</a>
    <div className="detail-heading"><div><p className="eyebrow">Fictional session · {session.id}</p><h2>{session.session_type === "INITIAL_ASSESSMENT" ? "Initial assessment" : "Follow-up session"}</h2><p>{session.pseudonym_id} · Created {formatDate(session.created_at)}</p></div><span className="workflow-badge large">{session.workflow_status.replaceAll("_", " ")}</span></div>
    <div className="detail-grid"><section className="transcript-panel panel"><div className="panel-heading"><div><p className="eyebrow">Source transcript</p><h3>Conversation editor</h3></div><span className="small-note">{transcript.length} segments · saved revisions</span></div>{loading && <div className="state-inline"><span className="loader" /> Loading transcript…</div>}{detailError && <div className="state-inline state-error">{detailError}</div>}{!loading && !detailError && (transcript.length ? transcript.map((segment) => <TranscriptRow key={segment.id} segment={segment} sessionId={session.id} onSaved={(saved) => setTranscript((current) => current.map((item) => item.id === saved.id ? saved : item))} />) : <div className="empty-inline">No transcript segments returned.</div>)}</section><aside className="evidence-panel panel"><div className="panel-heading"><div><p className="eyebrow">Evidence panel</p><h3>Source-linked excerpts</h3></div><Icon name="search" /></div><form className="search-form" onSubmit={(event) => { event.preventDefault(); setSearching(true); api.evidence(session.id, query).then(setEvidence).catch((reason: Error) => setDetailError(reason.message)).finally(() => setSearching(false)); }}><label htmlFor="evidence-query">Search this session</label><div className="search-control"><Icon name="search" size={16} /><input id="evidence-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try a source query" /><button type="submit" aria-label="Search evidence"><Icon name="arrow" size={16} /></button></div></form>{searching && <div className="state-inline"><span className="loader" /> Searching…</div>}{!searching && (evidence.length ? evidence.map((hit) => <EvidenceCard key={hit.segment_id} hit={hit} />) : <div className="empty-inline">No matching excerpts. A zero-hit search is not a safety conclusion.</div>)}</aside></div>
    <div className="notice"><Icon name="shield" /><span><strong>Review remains clinician-controlled.</strong> This read-only preview does not generate notes, assess risk, or approve a session.</span></div>
  </main></WorkspaceShell>;
}

function TranscriptRow({ segment, sessionId, onSaved }: { segment: TranscriptSegment; sessionId: string; onSaved: (segment: TranscriptSegment) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(segment.text);
  const [role, setRole] = useState(segment.speaker_role);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  async function save() {
    setSaving(true); setMessage(null);
    try { const saved = await api.updateSegment(sessionId, segment.id, { text, speaker_role: role }); onSaved(saved); setEditing(false); setMessage(`Saved revision ${saved.revision}`); }
    catch (reason) { setMessage(reason instanceof Error ? reason.message : "The edit could not be saved."); }
    finally { setSaving(false); }
  }
  function cancel() { setText(segment.text); setRole(segment.speaker_role); setEditing(false); setMessage(null); }
  return <article className="transcript-row"><div className="transcript-meta"><span>{formatTimestamp(segment.start_ms)}</span>{editing ? <label className="sr-only" htmlFor={`role-${segment.id}`}>Speaker role</label> : null}{editing ? <select id={`role-${segment.id}`} value={role} onChange={(event) => setRole(event.target.value as TranscriptSegment["speaker_role"])}><option>CLIENT</option><option>CLINICIAN</option><option>UNKNOWN</option></select> : <span className={`speaker speaker-${segment.speaker_role.toLowerCase()}`}>{segment.speaker_role}</span>}<span className="revision-label">Revision {segment.revision}</span></div>{editing ? <textarea value={text} onChange={(event) => setText(event.target.value)} aria-label={`Edit transcript segment ${segment.id}`} /> : <p>{segment.text}</p>}{editing ? <div className="edit-actions"><button className="button button-secondary small-button" onClick={cancel} disabled={saving}>Cancel</button><button className="button button-primary small-button" onClick={save} disabled={saving || !text.trim()}>{saving ? "Saving…" : "Save edit"}</button></div> : <button className="text-button" onClick={() => setEditing(true)}>Edit segment</button>}{message && <span className={message.startsWith("Saved") ? "save-message" : "form-error"} role="status">{message}</span>}</article>;
}
function EvidenceCard({ hit }: { hit: EvidenceHit }) { return <article className="evidence-card"><div><span className="evidence-section">{hit.section}</span><span className="evidence-time">{formatTimestamp(hit.start_ms)} – {formatTimestamp(hit.end_ms)}</span></div><p>{hit.excerpt}</p><span className="source-link"><Icon name="file" size={14} /> {hit.segment_id}</span></article>; }

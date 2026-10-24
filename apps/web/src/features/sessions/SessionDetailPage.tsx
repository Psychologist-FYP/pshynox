import { usePatients } from "../patients/PatientDirectory";
import { MeetingEntry } from "../meetings/MeetingEntry";
import "../meetings/meeting.css";
import { RecordingPanel } from "./RecordingPanel";
import { useEffect, useState } from "react";
import { WorkspaceShell } from "../../components/layout/AppShell";
import { Icon } from "../../components/ui/Icon";
import { api, formatDate, formatTimestamp, type AuditEvent, type EvidenceHit, type Health, type NoteDraft, type NoteField, type ReviewState, type Session, type TranscriptSegment } from "../../lib/api";

export function SessionDetailPage({ health, session, error }: { health: Health | null; session: Session | null; error: string | null }) {
  const { patients } = usePatients();
  const patient = patients.find(p => p.id === session?.patient_id);
  const [sources, setSources] = useState<Record<string, string>>({});
  const [transcript, setTranscript] = useState<TranscriptSegment[]>([]);
  const [evidence, setEvidence] = useState<EvidenceHit[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("sleep difficulty");
  const [searching, setSearching] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(error);
  const [selectedSegment, setSelectedSegment] = useState<string | null>(null);
  const [draft, setDraft] = useState<NoteDraft | null>(null);
  const [noteFields, setNoteFields] = useState<NoteField[]>([]);
  const [noteState, setNoteState] = useState("Loading draft…");
  const [review, setReview] = useState<ReviewState | null>(null);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [sourceReviewer, setSourceReviewer] = useState("");
  async function refreshSaved() {
    if (!session) return;
    try {
      const [segments, value, state, events] = await Promise.all([api.transcript(session.id), api.noteDraft(session.id), api.reviewState(session.id), api.auditEvents(session.id)]);
      setTranscript(segments); setDraft(value); setReview(state); setAudit(events);
      setSources(await api.transcriptSources(session.id));
    } catch (e) { setDetailError(e instanceof Error ? e.message : "Refresh failed"); }
  }

  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    api.transcriptSources(session.id).then(setSources).catch(() => {});
    Promise.all([api.transcript(session.id, controller.signal), api.evidence(session.id, query, controller.signal)])
      .then(([segments, hits]) => { setTranscript(segments); setEvidence(hits); setLoading(false); })
      .catch((reason: Error) => { if (reason.name !== "AbortError") { setDetailError(reason.message); setLoading(false); } });
    return () => controller.abort();
  }, [session, query]);
  useEffect(() => {
    if (!session) return;
    Promise.all([api.noteDraft(session.id), api.reviewState(session.id), api.auditEvents(session.id)]).then(([value, state, events]) => { setDraft(value); setNoteFields(value.fields); setNoteState("Saved"); setReview(state); setAudit(events); }).catch((reason: Error) => setNoteState(reason.message));
  }, [session]);

  if (!session) return <WorkspaceShell health={health} active="Sessions"><main className="section-wrap"><div className="state-panel state-error"><strong>Session unavailable</strong><span>{detailError ?? "This session could not be found."}</span><a href="/workspace">Return to workspace</a></div></main></WorkspaceShell>;
  return <WorkspaceShell health={health} active="Session" patientId={session.patient_id}><main className="detail-page section-wrap">
    <a className="back-link" href={session.patient_id ? `/workspace/patients/${session.patient_id}` : "/workspace"}><Icon name="chevron" /> {patient ? `Back to ${patient.name}` : session.patient_id ? "Back to patient" : "All patients"}</a>
    <div className="detail-heading"><div><p className="eyebrow">{patient ? patient.name : "Unassigned session"} · {session.id}</p><h2>{session.session_type === "INITIAL_ASSESSMENT" ? "Initial assessment" : "Follow-up session"}</h2><p>{patient?.preferred_name || patient?.name || session.pseudonym_id} · Created {formatDate(session.created_at)}</p></div><span className="workflow-badge large">{session.workflow_status.replaceAll("_", " ")}</span></div>
    {!session.patient_id && <AssignPatient sessionId={session.id} />}
    <MeetingEntry sessionId={session.id} />
    <RecordingPanel key={session.id} session={session} onTranscribed={() => void refreshSaved()} />
    <div className="detail-grid"><section className="transcript-panel panel"><div className="panel-heading"><div><p className="eyebrow">Source transcript</p><h3>Conversation editor</h3></div><span className="small-note">{transcript.length} segments · saved revisions</span></div>{loading && <div className="state-inline"><span className="loader" /> Loading transcript…</div>}{detailError && <div className="state-inline state-error">{detailError}</div>}{!loading && !detailError && (transcript.length ? transcript.map((segment) => <TranscriptRow key={segment.id} recordingId={sources[segment.id]} segment={segment} sessionId={session.id} selected={selectedSegment === segment.id} onSaved={(saved) => { setTranscript((current) => current.map((item) => item.id === saved.id ? saved : item)); void refreshSaved(); }} />) : <div className="empty-inline">No transcript segments returned.</div>)}</section><aside className="evidence-panel panel"><div className="panel-heading"><div><p className="eyebrow">Source excerpts · example search</p><h3>Source-linked excerpts</h3></div><Icon name="search" /></div><form className="search-form" onSubmit={(event) => { event.preventDefault(); setSearching(true); api.evidence(session.id, query).then(setEvidence).catch((reason: Error) => setDetailError(reason.message)).finally(() => setSearching(false)); }}><label htmlFor="evidence-query">Search this session</label><div className="search-control"><Icon name="search" size={16} /><input id="evidence-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try a source query" /><button type="submit" aria-label="Search evidence"><Icon name="arrow" size={16} /></button></div></form>{searching && <div className="state-inline"><span className="loader" /> Searching…</div>}{!searching && (evidence.length ? evidence.map((hit) => <EvidenceCard key={hit.segment_id} hit={hit} onSelect={() => { setSelectedSegment(hit.segment_id); document.getElementById(`segment-${hit.segment_id}`)?.scrollIntoView({ behavior: "smooth", block: "center" }); }} />) : <div className="empty-inline">No matching excerpts. A zero-hit search is not a safety conclusion.</div>)}</aside></div>
    <div className="draft-tools">
      <button className="button button-secondary small-button" disabled={!draft || draft.workflow_status !== "DRAFT" || noteState === "Unsaved changes"} onClick={async () => { try { const saved = await api.populateDraft(session.id); setDraft(saved); setNoteFields(saved.fields); setNoteState("Saved excerpt draft — review required"); await refreshSaved(); } catch (e) { setNoteState(e instanceof Error ? e.message : "Draft population failed"); } }}>Populate / refresh patient excerpts</button>
      {draft?.sources_stale && <div role="alert"><p>Source text changed. Review the saved draft against the transcript, then refresh excerpts or confirm your manual corrections.</p><input aria-label="Source reviewer" placeholder="Reviewer name" value={sourceReviewer} onChange={e => setSourceReviewer(e.target.value)} /><button disabled={!sourceReviewer.trim() || noteState === "Unsaved changes"} onClick={async () => { try { const saved = await api.confirmSources(session.id, sourceReviewer); setDraft(saved); setNoteFields(saved.fields); await refreshSaved(); } catch (e) { setNoteState(e instanceof Error ? e.message : "Confirmation failed"); } }}>Confirm I checked the changed sources</button></div>}
    </div>
    <NoteComposer draft={draft} fields={noteFields} state={noteState} onChange={(fields) => { setNoteFields(fields); setNoteState("Unsaved changes"); }} onSave={async () => { if (!draft) return; setNoteState("Saving…"); try { const saved = await api.saveNoteDraft(session.id, noteFields); setDraft(saved); setNoteFields(saved.fields); setNoteState(`Saved ${new Date(saved.updated_at).toLocaleTimeString()}`); await refreshSaved(); } catch (reason) { setNoteState(reason instanceof Error ? reason.message : "Could not save draft"); } }} />
    <ReviewPanel sessionId={session.id} review={review} audit={audit} onChanged={(state, events) => { setReview(state); setAudit(events); }} />
    <div className="notice"><Icon name="shield" /><span><strong>Review remains clinician-controlled.</strong> Transcription and excerpt drafts remain unreviewed until you check the sources and complete review.</span></div>
  </main></WorkspaceShell>;
}

function TranscriptRow({ segment, recordingId, sessionId, selected, onSaved }: { recordingId?: string; segment: TranscriptSegment; sessionId: string; selected: boolean; onSaved: (segment: TranscriptSegment) => void }) {
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
  return <article id={`segment-${segment.id}`} className={`transcript-row ${selected ? "source-focused" : ""}`}><div className="transcript-meta"><button className="text-button" onClick={() => { const audio = (recordingId ? document.getElementById(`audio-${recordingId}`) as HTMLAudioElement | null : null); if (audio) { audio.currentTime = Math.max(0, segment.start_ms - Number(audio.dataset.offsetMs || 0)) / 1000; void audio.play().catch(() => {}); } }}>{formatTimestamp(segment.start_ms)}</button>{editing ? <label className="sr-only" htmlFor={`role-${segment.id}`}>Speaker role</label> : null}{editing ? <select id={`role-${segment.id}`} value={role} onChange={(event) => setRole(event.target.value as TranscriptSegment["speaker_role"])}><option>CLIENT</option><option>CLINICIAN</option><option>UNKNOWN</option></select> : <span className={`speaker speaker-${segment.speaker_role.toLowerCase()}`}>{segment.speaker_role}</span>}<span className="revision-label">Revision {segment.revision}</span></div>{editing ? <textarea value={text} onChange={(event) => setText(event.target.value)} aria-label={`Edit transcript segment ${segment.id}`} /> : <p>{segment.text}</p>}{editing ? <div className="edit-actions"><button className="button button-secondary small-button" onClick={cancel} disabled={saving}>Cancel</button><button className="button button-primary small-button" onClick={save} disabled={saving || !text.trim()}>{saving ? "Saving…" : "Save edit"}</button></div> : <button className="text-button" onClick={() => setEditing(true)}>Edit segment</button>}{message && <span className={message.startsWith("Saved") ? "save-message" : "form-error"} role="status">{message}</span>}</article>;
}
function EvidenceCard({ hit, onSelect }: { hit: EvidenceHit; onSelect: () => void }) { return <button className="evidence-card evidence-button" onClick={onSelect}><div><span className="evidence-section">{hit.section}</span><span className="evidence-time">{formatTimestamp(hit.start_ms)} – {formatTimestamp(hit.end_ms)} · score {hit.score.toFixed(2)}</span></div><p>{hit.excerpt}</p><span className="source-link"><Icon name="file" size={14} /> {hit.segment_id} · jump to source</span></button>; }

function NoteComposer({ draft, fields, state, onChange, onSave }: { draft: NoteDraft | null; fields: NoteField[]; state: string; onChange: (fields: NoteField[]) => void; onSave: () => void }) {
  const terminal = draft?.workflow_status !== "DRAFT";
  return <section className="note-composer panel"><div className="panel-heading"><div><p className="eyebrow">Structured draft</p><h3>Clinical note composer</h3></div><span className="small-note" role="status">{state}</span></div><p className="note-disclaimer">Patient excerpts and retrieval candidates require clinician review. Missing information stays not documented.</p><div className="note-grid">{fields.map((field, index) => <label key={field.section} className="note-field"><span>{field.section}<small>{field.provenance === "TEMPLATE" ? "Excerpt / template" : field.provenance === "RETRIEVAL_CANDIDATE" ? "Suggestion" : "Manual"}</small></span><textarea readOnly={terminal} value={field.text} placeholder={field.section === "objective" || field.section === "assessment" || field.section === "plan" ? "Not documented" : "Enter or review documentation"} onChange={(event) => { const next = [...fields]; next[index] = { ...field, text: event.target.value, provenance: "MANUAL", review_status: "PENDING" }; onChange(next); }} /></label>)}</div><div className="note-actions"><span>{terminal ? `Decision: ${draft?.workflow_status}` : draft ? "Draft persists locally in SQLite." : "Loading draft…"}</span><button className="button button-primary small-button" onClick={onSave} disabled={!draft || terminal || state === "Saving…"}>Save draft</button></div></section>;
}

function ReviewPanel({ sessionId, review, audit, onChanged }: { sessionId: string; review: ReviewState | null; audit: AuditEvent[]; onChanged: (state: ReviewState, events: AuditEvent[]) => void }) {
  const [name, setName] = useState(""); const [reason, setReason] = useState(""); const [confirmed, setConfirmed] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  if (!review) return <section className="panel review-panel">Loading review state…</section>;
  const terminal = review.workflow_status !== "DRAFT";
  const run = async (action: () => Promise<ReviewState>) => { setBusy(true); setError(null); try { const state = await action(); onChanged(state, await api.auditEvents(sessionId)); } catch (e) { setError(e instanceof Error ? e.message : "Workflow action failed."); } finally { setBusy(false); } };
  return <section className="panel review-panel"><div className="panel-heading"><div><p className="eyebrow">Clinician review gate</p><h3>Review & decision</h3></div><span className={`review-status ${review.risk_review_status.toLowerCase()}`}>{review.risk_review_status}</span></div><p className="safety-notice">Search results are only evidence candidates. No matching results do not establish that risk is absent. The clinician must review the relevant context manually.</p><div className="review-grid"><div className="review-column"><div className="review-field"><label htmlFor="reviewer-name">Reviewer display name / local identifier</label><input id="reviewer-name" value={name} disabled={terminal || busy} onChange={(e) => setName(e.target.value)} placeholder="Self-reported clinician name" /><small className="field-help">Self-reported in this local MVP; identity is not authenticated.</small></div><label className="confirm-line"><input type="checkbox" checked={confirmed} disabled={terminal || busy} onChange={(e) => setConfirmed(e.target.checked)} /> <span>I confirm I manually reviewed the relevant session context.</span></label><button className="button button-primary small-button review-submit" disabled={terminal || busy || review.risk_review_status === "COMPLETED" || !name.trim() || !confirmed} onClick={() => run(() => api.completeReview(sessionId, { reviewer_name: name, confirmation: confirmed }))}>Complete risk review</button></div><div className="review-column"><div className="review-field"><label htmlFor="reject-reason">Rejection reason</label><textarea id="reject-reason" value={reason} disabled={terminal || busy} onChange={(e) => setReason(e.target.value)} placeholder="Required to reject this draft" /></div><div className="decision-actions"><button className="button button-secondary small-button" disabled={terminal || busy || review.risk_review_status !== "COMPLETED"} onClick={() => run(() => api.approve(sessionId, name))}>Approve note</button><button className="button button-secondary small-button reject-button" disabled={terminal || busy || !reason.trim()} onClick={() => run(() => api.reject(sessionId, name, reason))}>Reject note</button></div></div></div>{review.workflow_status !== "DRAFT" && <p className="decision-result">Decision: <strong>{review.workflow_status}</strong> by {review.decision_actor_name}</p>}{error && <p className="form-error" role="alert">{error}</p>}<div className="audit-list"><h4>Audit history</h4>{audit.length ? audit.map((event) => <div className="audit-row" key={event.id}><strong>{event.event_type}</strong><span>{event.actor_name} · {formatDate(event.occurred_at)}</span></div>) : <span className="small-note">No workflow events yet.</span>}</div></section>;
}


function AssignPatient({ sessionId }: { sessionId: string }) {
  const { patients } = usePatients();
  const [patientId, setPatientId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <section className="panel assign-patient"><div><h3>Link this session to a patient</h3><p>Keep this earlier recording and its notes in the right patient's history.</p></div><select aria-label="Assign session to patient" value={patientId} onChange={e => setPatientId(e.target.value)}><option value="">Select patient</option>{patients.map(p => <option value={p.id} key={p.id}>{p.name}</option>)}</select><button className="button button-secondary" disabled={!patientId || busy} onClick={async () => { setBusy(true); try { await api.assignPatient(sessionId, patientId); window.location.reload(); } catch (e) { setError(e instanceof Error ? e.message : "Could not link session."); setBusy(false); } }}>{busy ? "Linking…" : "Link patient"}</button>{!patients.length && <a href="/workspace/patients/new">Add a patient first</a>}{error && <p className="form-error" role="alert">{error}</p>}</section>;
}

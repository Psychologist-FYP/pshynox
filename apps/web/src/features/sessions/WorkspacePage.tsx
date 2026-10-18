import { Icon } from "../../components/ui/Icon";
import { WorkspaceShell } from "../../components/layout/AppShell";
import { api, formatDate, type Health, type Session } from "../../lib/api";
import { useState, type FormEvent } from "react";

export function WorkspacePage({ health, sessions, loading, error, onCreated }: { health: Health | null; sessions: Session[]; loading: boolean; error: string | null; onCreated: (session: Session) => void }) {
  const [showCreate, setShowCreate] = useState(false);
  return <WorkspaceShell health={health}>
    <main className="dashboard section-wrap">
      <section className="dashboard-intro"><div><p className="eyebrow">Good morning, clinician</p><h2>A focused place for careful documentation.</h2><p>Open a fictional session or create one to test local persistence and transcript correction.</p></div><button className="button button-primary" onClick={() => setShowCreate(true)}><Icon name="file" /> New fictional session</button></section>
      <section className="metric-grid" aria-label="Workspace summary"><Metric label="Active sessions" value={String(sessions.length).padStart(2, "0")} note={health?.fixture_mode ? "Fixture + local data" : "SQLite workspace"} /><Metric label="Review status" value="Draft" note="No approval action available" /><Metric label="API mode" value={health?.fixture_mode ? "Fixture" : "SQLite"} note={health ? `v${health.app_version}` : "Unavailable"} /></section>
      <section className="session-section" id="sessions"><div className="section-title-row"><div><p className="eyebrow">Recent sessions</p><h2>Fictional workspace</h2></div><span className="small-note">Safe demo data only</span></div>{loading && <div className="state-panel"><span className="loader" /> Loading session data…</div>}{error && <div className="state-panel state-error"><strong>Could not load the workspace</strong><span>{error}</span><a href="/workspace">Try again</a></div>}{!loading && !error && sessions.map((item) => <SessionCard key={item.id} session={item} />)}{!loading && !error && !sessions.length && <div className="state-panel"><strong>No sessions yet</strong><span>Create a fictional session to begin the local workflow.</span></div>}</section>
      {showCreate && <CreateSession onClose={() => setShowCreate(false)} onCreated={(created) => { onCreated(created); setShowCreate(false); }} />}
    </main>
  </WorkspaceShell>;
}

function Metric({ label, value, note }: { label: string; value: string; note: string }) { return <div className="metric-card"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>; }
function SessionCard({ session }: { session: Session }) { return <a className="session-card" href={`/workspace/sessions/${session.id}`}><div className="session-icon"><Icon name="file" /></div><div className="session-card-main"><div className="card-kicker"><span>FICTIONAL · {session.id}</span><span className="workflow-badge">{session.workflow_status.replaceAll("_", " ")}</span></div><h3>{session.session_type === "INITIAL_ASSESSMENT" ? "Initial assessment" : "Follow-up session"}</h3><p>{session.pseudonym_id} · Created {formatDate(session.created_at)}</p></div><Icon name="arrow" /></a>; }

function CreateSession({ onClose, onCreated }: { onClose: () => void; onCreated: (session: Session) => void }) {
  const [pseudonym, setPseudonym] = useState("");
  const [sessionType, setSessionType] = useState<Session["session_type"]>("INITIAL_ASSESSMENT");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!pseudonym.trim()) { setError("Enter a fictional pseudonym ID."); return; }
    setSaving(true); setError(null);
    try { onCreated(await api.createSession({ pseudonym_id: pseudonym.trim(), session_type: sessionType, consent: { recording_consent: false, fictional_fixture: true } })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "The session could not be saved."); setSaving(false); }
  }
  return <div className="modal-backdrop" role="presentation"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="create-title"><div className="modal-heading"><div><p className="eyebrow">Local demo record</p><h2 id="create-title">New fictional session</h2></div><button className="icon-button" onClick={onClose} aria-label="Close dialog">×</button></div><p className="modal-copy">This creates synthetic data in the local SQLite workspace. No real identity or audio is requested.</p><form onSubmit={submit}><label htmlFor="pseudonym">Pseudonym ID</label><input id="pseudonym" value={pseudonym} onChange={(event) => setPseudonym(event.target.value)} placeholder="client-demo-002" autoFocus /><label htmlFor="session-type">Session type</label><select id="session-type" value={sessionType} onChange={(event) => setSessionType(event.target.value as Session["session_type"])}><option value="INITIAL_ASSESSMENT">Initial assessment</option><option value="FOLLOW_UP">Follow-up</option></select>{error && <p className="form-error" role="alert">{error}</p>}<div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>Cancel</button><button className="button button-primary" disabled={saving}>{saving ? "Saving…" : "Create session"}</button></div></form></div></div>;
}

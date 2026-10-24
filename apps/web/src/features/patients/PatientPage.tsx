import { useEffect, useState } from "react";
import { WorkspaceShell } from "../../components/layout/AppShell";
import { Icon } from "../../components/ui/Icon";
import { api, formatDate, type Health, type Patient, type Session } from "../../lib/api";
import { initials, patientAge } from "./PatientDirectory";
import { intakeGroups, PatientForm } from "./PatientForm";
import { SessionCard } from "../sessions/WorkspacePage";

export function PatientPage({ health, patientId, editing = false }: { health: Health | null; patientId?: string; editing?: boolean }) {
  const [patient, setPatient] = useState<Patient | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(!!patientId);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [sessionType, setSessionType] = useState<Session["session_type"]>("INITIAL_ASSESSMENT");
  const [tab, setTab] = useState("sessions");
  useEffect(() => {
    if (!patientId) return;
    let active = true;
    Promise.all([api.patient(patientId), api.patientSessions(patientId)]).then(([p, rows]) => {
      if (active) { setPatient(p); setSessions(rows); setSessionType(rows.length ? "FOLLOW_UP" : "INITIAL_ASSESSMENT"); }
    }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [patientId]);
  const isForm = !patientId || editing;
  async function startSession() {
    if (!patient) return;
    setStarting(true); setError("");
    try {
      const session = await api.createSession({ patient_id: patient.id, session_type: sessionType, consent: { recording_consent: false } });
      window.location.assign(`/workspace/sessions/${session.id}`);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not start session."); setStarting(false); }
  }
  return <WorkspaceShell health={health} active={isForm ? patientId ? "Edit patient" : "Add patient" : "Patient record"} patientId={patientId}><main className="section-wrap patient-page">
    <a className="back-link" href={editing && patientId ? `/workspace/patients/${patientId}` : "/workspace"}><Icon name="chevron" /> {editing ? "Back to patient" : "All patients"}</a>
    {loading && <div className="state-panel" role="status">Loading patient record…</div>}
    {error && <div className="state-panel state-error" role="alert">{error}</div>}
    {!loading && isForm && (!patientId || patient) && <><div className="patient-form-heading"><img className="intake-character" src="/images/patient2.png" alt="" /><p className="eyebrow">Patient intake</p><h2>{patientId ? "Update patient details" : "Introductions first. Forms, apparently, also first."}</h2><p>Capture the details you have now. Leave anything not yet discussed blank.</p></div><PatientForm patient={patient ?? undefined} onSaved={saved => window.location.assign(`/workspace/patients/${saved.id}`)} /></>}
    {!loading && !isForm && patient && <>
      <section className="patient-profile-header"><span className="patient-avatar large">{initials(patient.name)}</span><div><p className="eyebrow">Patient record</p><h2>{patient.name}</h2><p>{patientAge(patient)}{patient.sex ? ` · ${patient.sex}` : ""}{patient.language ? ` · ${patient.language}` : ""}</p>{patient.preferred_name && <p>Preferred name: {patient.preferred_name}</p>}</div><a className="button button-secondary" href={`/workspace/patients/${patient.id}/edit`}>Edit details</a></section>
      <div className="patient-overview-strip"><span><strong>{sessions.length}</strong> {sessions.length === 1 ? "session" : "sessions"}</span><span>Added {formatDate(patient.created_at)}</span><span>{patient.phone || "Phone not recorded"}</span></div>
      <div className="patient-tabs" role="tablist" aria-label="Patient record sections"><button role="tab" id="sessions-tab" aria-selected={tab === "sessions"} aria-controls="sessions-panel" onClick={() => setTab("sessions")}>Sessions & documentation</button><button role="tab" id="intake-tab" aria-selected={tab === "intake"} aria-controls="intake-panel" onClick={() => setTab("intake")}>Patient details & intake</button></div>
      {tab === "sessions" ? <section id="sessions-panel" role="tabpanel" aria-labelledby="sessions-tab"><div className="new-session-bar panel"><div><h3>Another conversation. A fresh page.</h3><p>Start a session to record audio, review the transcript and prepare notes.</p></div><div className="new-session-actions"><label htmlFor="new-session-type" className="sr-only">Session type</label><select id="new-session-type" value={sessionType} onChange={e => setSessionType(e.target.value as Session["session_type"])}><option value="INITIAL_ASSESSMENT">Initial assessment</option><option value="FOLLOW_UP">Follow-up session</option></select><button className="button button-primary" disabled={starting} onClick={startSession}><Icon name="file" /> {starting ? "Starting…" : "Start new session"}</button></div></div><div className="section-title-row"><h3>Session history</h3><span className="small-note">Most recent first</span></div>{sessions.map(s => <SessionCard key={s.id} session={s} />)}{!sessions.length && <div className="patient-empty compact"><h3>No sessions yet</h3><p>Start the first session above. Its recordings, transcript and notes will stay in this patient's history.</p></div>}</section> :
      <section id="intake-panel" role="tabpanel" aria-labelledby="intake-tab" className="intake-summary">{intakeGroups.map(group => <section className="panel intake-section" key={group.title}><h3>{group.title}</h3><dl>{group.fields.map(f => <div key={f.key} className={f.wide || f.type === "textarea" ? "wide" : ""}><dt>{f.label}</dt><dd>{patient[f.key] === null || patient[f.key] === "" ? "Not recorded" : String(patient[f.key])}</dd></div>)}</dl></section>)}</section>}
    </>}
  </main></WorkspaceShell>;
}

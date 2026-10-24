import { useEffect, useState } from "react";
import { Icon } from "../../components/ui/Icon";
import { WorkspaceShell } from "../../components/layout/AppShell";
import { api, formatDate, type Health, type Session } from "../../lib/api";
import { initials, patientAge, usePatients } from "../patients/PatientDirectory";

export function WorkspacePage({ health }: { health: Health | null }) {
  const { patients, loading, error, reload } = usePatients();
  const [query, setQuery] = useState("");
  const [unassigned, setUnassigned] = useState<Session[]>([]);
  const [sessionError, setSessionError] = useState("");
  useEffect(() => { api.sessions().then(rows => setUnassigned(rows.filter(s => !s.patient_id))).catch(e => setSessionError(e.message)); }, []);
  const filtered = patients.filter(p => `${p.name} ${p.preferred_name} ${p.phone}`.toLowerCase().includes(query.toLowerCase()));
  return <WorkspaceShell health={health} active="Patients"><main className="dashboard section-wrap">
    <section className="dashboard-intro"><div><p className="eyebrow">Your workspace</p><h2>Care starts with a conversation.</h2><p>Patient details, session history and documentation, all in one place.</p></div><a className="button button-primary" href="/workspace/patients/new"><Icon name="file" /> Add patient</a></section>
    <section className="patient-directory-section"><div className="section-title-row"><div><p className="eyebrow">Patient directory</p><h2>Your patients <span className="count-chip">{patients.length}</span></h2></div><label className="patient-search"><Icon name="search" size={17} /><input aria-label="Search patients" placeholder="Search name or phone…" value={query} onChange={e => setQuery(e.target.value)} /></label></div>
      {loading && <div className="state-panel" role="status">Loading patients…</div>}
      {error && <div className="state-panel state-error" role="alert"><strong>Could not load patients</strong><span>{error}</span><button className="text-button" onClick={() => void reload()}>Try again</button></div>}
      {!loading && !error && !patients.length && <div className="patient-empty"><span className="patient-avatar"><Icon name="file" size={26} /></span><h3>A place for every patient's story</h3><p>Add a patient to collect their intake details and keep every session together.</p><a className="button button-primary" href="/workspace/patients/new">Add your first patient</a></div>}
      {!loading && !error && !!patients.length && !filtered.length && <div className="state-panel">No patients match your search.</div>}
      <div className="patient-card-grid">{filtered.map(p => <a className="patient-card" key={p.id} href={`/workspace/patients/${p.id}`}><span className="patient-avatar">{initials(p.name)}</span><div><h3>{p.name}</h3><p>{patientAge(p)}{p.sex && p.sex !== "Not recorded" ? ` · ${p.sex}` : ""}</p><small>{p.preferred_name ? `Goes by ${p.preferred_name}` : `Added ${formatDate(p.created_at)}`}</small></div><Icon name="arrow" size={18} /></a>)}</div>
    </section>
    {sessionError && <p role="alert" className="form-error">Could not load unassigned sessions: {sessionError}</p>}
    {!!unassigned.length && <details className="legacy-sessions"><summary>Unassigned sessions <span className="count-chip">{unassigned.length}</span></summary><p>These earlier sessions have not been linked to a patient. Open one to assign it.</p>{unassigned.map(s => <SessionCard session={s} key={s.id} />)}</details>}
  </main></WorkspaceShell>;
}

export function SessionCard({ session }: { session: Session }) {
  return <a className="session-card" href={`/workspace/sessions/${session.id}`}><div className="session-icon"><Icon name="file" /></div><div className="session-card-main"><div className="card-kicker"><span>{formatDate(session.created_at)}</span><span className="workflow-badge">{session.workflow_status.replaceAll("_", " ")}</span></div><h3>{session.session_type === "INITIAL_ASSESSMENT" ? "Initial assessment" : "Follow-up session"}</h3><p>{session.id} · Open recording, transcript & notes</p></div><Icon name="arrow" /></a>;
}

import { usePatients, initials } from "../../features/patients/PatientDirectory";
import { useState } from "react";
import { Icon } from "../ui/Icon";
import { StatusPill } from "../ui/StatusPill";
import type { Health } from "../../lib/api";

export function Brand() {
  return <a className="brand" href="/" aria-label="Dr. Katz home"><img className="brand-logo" src="/images/logo.png" alt="Dr. Katz — Professional Therapist" /></a>;
}

export function TopBar({ health, workspace = false }: { health: Health | null; workspace?: boolean }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <header className={`topbar ${workspace ? "topbar-workspace" : ""} ${menuOpen ? "nav-open" : ""}`}>
      <Brand />
      <button className="icon-button nav-toggle" onClick={() => setMenuOpen((open) => !open)} aria-expanded={menuOpen} aria-controls="primary-navigation" aria-label={menuOpen ? "Close navigation" : "Open navigation"}><Icon name={menuOpen ? "close" : "menu"} /></button>
      <nav className="topnav" id="primary-navigation" aria-label="Primary navigation">
        <a href="/#approach" onClick={() => setMenuOpen(false)}>The approach</a>
        <a href="/#workflow" onClick={() => setMenuOpen(false)}>Workflow</a>
        <a href="/#about" onClick={() => setMenuOpen(false)}>About</a>
        <a className="nav-action" href="/workspace" onClick={() => setMenuOpen(false)}>Open workspace <Icon name="arrow" size={15} /></a>
      </nav>
      {!workspace && <StatusPill health={health} compact />}
    </header>
  );
}

export function WorkspaceShell({ health, children, active = "Patients", patientId }: { health: Health | null; children: React.ReactNode; active?: string; patientId?: string | null }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { patients, loading, error } = usePatients();
  const [search, setSearch] = useState("");
  return (
    <div className="workspace-app">
      <aside className={`sidebar ${menuOpen ? "is-open" : ""}`}>
        <div className="sidebar-top"><Brand /><button className="icon-button mobile-only" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><Icon name="chevron" /></button></div>
        <nav className="side-nav patient-top-nav" aria-label="Workspace navigation">
          <a className={active === "Patients" ? "active" : ""} href="/workspace"><Icon name="grid" /> All patients</a>
          <a href="/workspace/patients/new"><Icon name="file" /> Add patient</a>
        </nav>
        <div className="sidebar-patient-heading"><p className="sidebar-label">Patients</p><span>{patients.length}</span></div>
        <label className="sr-only" htmlFor="sidebar-patient-search">Find a patient</label>
        <input className="sidebar-search" id="sidebar-patient-search" placeholder="Find a patient…" value={search} onChange={e => setSearch(e.target.value)} />
        <nav className="patient-nav" aria-label="Patient list">
          {loading && <p className="sidebar-hint">Loading patients…</p>}
          {error && <p className="sidebar-hint" role="alert">Could not load patients.</p>}
          {patients.filter(p => `${p.name} ${p.preferred_name}`.toLowerCase().includes(search.toLowerCase())).map(p => <a key={p.id} aria-current={patientId === p.id ? "page" : undefined} className={patientId === p.id ? "active" : ""} href={`/workspace/patients/${p.id}`}><span className="patient-avatar small">{initials(p.name)}</span><span>{p.name}{p.preferred_name && <small>{p.preferred_name}</small>}</span></a>)}
          {!loading && !patients.length && <p className="sidebar-hint">Add your first patient to get started.</p>}
          {!!patients.length && !patients.some(p => `${p.name} ${p.preferred_name}`.toLowerCase().includes(search.toLowerCase())) && <p className="sidebar-hint">No matching patients.</p>}
        </nav>
        <div className="sidebar-bottom"><div className="office-sign">The listening room<span>The notebook is listening.</span></div><StatusPill health={health} /><p>Everything filed. A small victory for the office.</p></div>
      </aside>
      <div className="workspace-content">
        <header className="workspace-header">
          <button className="icon-button mobile-only" onClick={() => setMenuOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
          <div><p className="breadcrumb">Dr. Katz / {active}</p><h1>{active}</h1></div>
          <StatusPill health={health} />
        </header>
        {children}
        <footer className="workspace-footer">Documentation support only · No diagnosis, risk assessment, or treatment recommendations.</footer>
      </div>
    </div>
  );
}

import { useState } from "react";
import { Icon } from "../ui/Icon";
import { StatusPill } from "../ui/StatusPill";
import type { Health } from "../../lib/api";

export function Brand() {
  return <a className="brand" href="/" aria-label="Dr. Katz home"><span className="brand-mark"><Icon name="wave" size={19} /></span><span>Dr. Katz</span></a>;
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

export function WorkspaceShell({ health, children, active = "Overview" }: { health: Health | null; children: React.ReactNode; active?: string }) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="workspace-app">
      <aside className={`sidebar ${menuOpen ? "is-open" : ""}`}>
        <div className="sidebar-top"><Brand /><button className="icon-button mobile-only" onClick={() => setMenuOpen(false)} aria-label="Close navigation"><Icon name="chevron" /></button></div>
        <p className="sidebar-label">Workspace</p>
        <nav className="side-nav" aria-label="Workspace navigation">
          <a className={active === "Overview" ? "active" : ""} href="/workspace"><Icon name="grid" /> Overview</a>
          <a className={active === "Sessions" ? "active" : ""} href="/workspace#sessions"><Icon name="file" /> Sessions</a>
        </nav>
        <div className="sidebar-bottom"><StatusPill health={health} /><p>Local-first documentation support for fictional workflows.</p></div>
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

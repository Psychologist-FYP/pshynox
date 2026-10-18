import { useState } from "react";
import { Icon } from "../ui/Icon";
import { StatusPill } from "../ui/StatusPill";
import type { Health } from "../../lib/api";

export function Brand() {
  return <a className="brand" href="/" aria-label="Pshynox home"><span className="brand-mark"><Icon name="wave" size={19} /></span><span>Pshynox</span></a>;
}

export function TopBar({ health, workspace = false }: { health: Health | null; workspace?: boolean }) {
  return (
    <header className={`topbar ${workspace ? "topbar-workspace" : ""}`}>
      <Brand />
      <nav className="topnav" aria-label="Primary navigation">
        <a href="/#approach">Approach</a>
        <a href="/#scope">Scope</a>
        <a href="/workspace">Workspace</a>
      </nav>
      <StatusPill health={health} compact />
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
          <div><p className="breadcrumb">Module C / {active}</p><h1>{active}</h1></div>
          <StatusPill health={health} />
        </header>
        {children}
        <footer className="workspace-footer">Documentation support only · No diagnosis, risk assessment, or treatment recommendations.</footer>
      </div>
    </div>
  );
}

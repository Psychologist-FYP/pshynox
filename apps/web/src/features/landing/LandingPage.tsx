import { Icon } from "../../components/ui/Icon";
import { StatusPill } from "../../components/ui/StatusPill";
import { Brand, TopBar } from "../../components/layout/AppShell";
import type { Health } from "../../lib/api";

export function LandingPage({ health, error }: { health: Health | null; error: string | null }) {
  return (
    <div className="landing">
      <TopBar health={health} />
      <main>
        <section className="hero section-wrap">
          <div className="hero-copy">
            <p className="eyebrow">Module C · offline MVP</p>
            <h1>Clinical documentation, <em>with the clinician in control.</em></h1>
            <p className="hero-lede">Organize session transcripts, trace note content to source excerpts, and review structured documentation in one local workspace.</p>
            <div className="hero-actions"><a className="button button-primary" href="/workspace">Open workspace <Icon name="arrow" /></a><a className="text-link" href="#approach">Explore the workflow <Icon name="chevron" /></a></div>
            <div className="hero-status"><StatusPill health={health} /><span>{error ?? (health ? `API v${health.app_version} · ${health.fixture_mode ? "fictional data only" : "local data"}` : "Connecting to local API…")}</span></div>
          </div>
          <PreviewCard />
        </section>
        <section className="trust-strip"><div className="section-wrap trust-inner"><span><Icon name="shield" /> Local-first by default</span><span><Icon name="check" /> Clinician review stays explicit</span><span><Icon name="file" /> Fictional fixture workspace</span></div></section>
        <section className="section-wrap content-section" id="approach"><div className="section-heading"><p className="eyebrow">A calmer workflow</p><h2>Keep the source close to the note.</h2><p>Designed for careful review, not automated conclusions. Every preview keeps the transcript, evidence, and documentation context together.</p></div><div className="value-grid"><Value icon="wave" title="Transcript review" text="Read timestamped segments in a focused, human-reviewable timeline." /><Value icon="search" title="Source-linked evidence" text="Keep excerpts connected to their source segment and time range." /><Value icon="file" title="Structured notes" text="Prepare a clear path toward SOAP-style documentation without filling gaps." /><Value icon="shield" title="Clinician-controlled" text="Review and approval remain explicit workflow steps, never automatic." /></div></section>
        <section className="section-wrap scope-section" id="scope"><div><p className="eyebrow">Honest scope</p><h2>Useful offline, clear about its limits.</h2></div><p>This milestone is a fixture-backed application shell. It does not diagnose, assess clinical risk, recommend treatment, or make claims of clinical validation. The next step is durable local persistence.</p></section>
      </main>
      <footer className="site-footer section-wrap"><Brand /><span>Documentation support only. Built for a focused local demo.</span></footer>
    </div>
  );
}

function PreviewCard() {
  return <div className="preview-frame" aria-label="Illustrative workspace preview"><div className="preview-top"><span className="preview-dot" /><span>Session workspace</span><span className="preview-muted">fixture</span></div><div className="preview-body"><div className="preview-side"><span className="mini-line short" /><span className="mini-line active" /><span className="mini-line" /></div><div className="preview-main"><span className="mini-label">SESSION / DEMO-001</span><strong>Initial assessment</strong><div className="preview-segment"><span className="time">00:00</span><span className="speaker">CLIENT</span><span>Fictional example text about sleep difficulty.</span></div><div className="preview-evidence"><Icon name="search" size={14} /><span>1 source-linked excerpt</span></div></div></div><div className="preview-footer"><span>Clinician review</span><span className="mini-badge">In progress</span></div></div>;
}

function Value({ icon, title, text }: { icon: "wave" | "search" | "file" | "shield"; title: string; text: string }) {
  return <article className="value-card"><span className="value-icon"><Icon name={icon} /></span><h3>{title}</h3><p>{text}</p></article>;
}

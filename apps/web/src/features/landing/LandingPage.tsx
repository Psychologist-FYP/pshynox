import { Icon } from "../../components/ui/Icon";
import { Brand, TopBar } from "../../components/layout/AppShell";
import type { Health } from "../../lib/api";

export function LandingPage({ health, error }: { health: Health | null; error: string | null }) {
  return <div className="landing">
    <TopBar health={health} />
    <main>
      <section className="hero section-wrap">
        <div className="hero-copy">
          <p className="eyebrow">Thoughtful notes. Clear sources.</p>
          <h1>A little more clarity after every <em>conversation.</em></h1>
          <p className="hero-lede">A focused workspace for organizing session transcripts, connecting notes to source excerpts, and keeping clinician review explicit.</p>
          <div className="hero-actions"><a className="button button-primary" href="/workspace">Open workspace <Icon name="arrow" /></a><a className="text-link" href="#workflow">See how it works <Icon name="chevron" /></a></div>
          <div className="hero-status"><span className="quiet-dot" /> {error ?? (health ? "A local workspace for fictional examples" : "Preparing your local workspace…")}</div>
        </div>
        <HeroIllustration />
      </section>
      <section className="trust-strip"><div className="section-wrap trust-inner"><span><Icon name="file" /> The source stays close to the note.</span><span><Icon name="shield" /> Clinicians make the decisions.</span><span><Icon name="wave" /> Built for local-first use.</span></div></section>
      <section className="section-wrap content-section" id="approach">
        <div className="section-heading"><p className="eyebrow">A calmer way to work</p><h2>Good documentation leaves a trail.</h2><p>Dr. Katz keeps the useful context nearby, so careful review feels less like hunting and more like listening again.</p></div>
        <div className="value-grid"><Value icon="file" title="The source stays close" text="Read timestamped transcript segments and keep evidence tied to the words that support it." /><Value icon="check" title="Human judgment stays central" text="Structured notes can be shaped and reviewed, but decisions remain with the clinician." /><Value icon="shield" title="A local-first foundation" text="Fictional examples and saved drafts are designed to run in this focused local workspace." /></div>
      </section>
      <section className="section-wrap workflow-section" id="workflow">
        <div className="section-heading"><p className="eyebrow">The workflow</p><h2>Three small steps. One clearer record.</h2></div>
        <div className="workflow-grid"><Step number="01" icon="file" title="Bring in a session" text="Open a fictional session and start with a clean, organized record." /><Step number="02" icon="search" title="Review transcript and sources" text="Correct words or speakers, then inspect excerpts that point back to their transcript segment." /><Step number="03" icon="shield" title="Shape and review the note" text="Edit structured sections and complete an explicit clinician review before approval." /></div>
      </section>
      <section className="section-wrap showcase-section">
        <div className="showcase-copy"><p className="eyebrow">Example workspace</p><h2>Everything important stays in view.</h2><p>A sample of the real working surface—transcript, source excerpt, note structure, and review status. No pretend diagnosis, no mystery automation.</p><a className="text-link" href="/workspace">Open the workspace <Icon name="arrow" size={15} /></a></div>
        <ExampleWorkspace />
      </section>
      <section className="section-wrap about-section" id="about"><div className="about-mark"><img src="/images/dr-katz-inspired-mascot.png" alt="" /></div><div><p className="eyebrow">Why the character?</p><h2>A gentle reminder to listen twice.</h2><p>Dr. Katz is a playful software persona for a serious promise: careful listening, organized documentation, traceable source material, and human control. The character is not a clinician, and the product does not make clinical decisions.</p></div></section>
      <section className="section-wrap scope-section" id="scope"><div><p className="eyebrow">Honest scope</p><h2>Documentation support, not autonomous judgment.</h2></div><p>This workspace helps organize and review fictional session material. It does not diagnose, predict risk, recommend treatment, or replace professional judgment.</p></section>
    </main>
    <footer className="site-footer section-wrap"><Brand /><div><a href="/workspace">Workspace</a><a href="/#workflow">Workflow</a><a href="/#about">About</a></div><span>Built for a focused local demo.</span></footer>
  </div>;
}

function HeroIllustration() {
  return <div className="hero-art"><div className="speech-bubble">“Let’s keep the important bits where we can see them.”</div><div className="mascot-wrap"><img src="/images/dr-katz-inspired-mascot.png" alt="An original friendly Dr. Katz mascot illustration" /><div className="fallback-face" aria-hidden="true"><span>◡</span></div></div><div className="hero-preview"><div className="preview-top"><span className="preview-dot" /><span>Example workspace</span><span className="preview-muted">fictional</span></div><div className="preview-body"><div className="preview-side"><span className="mini-line short" /><span className="mini-line active" /><span className="mini-line" /></div><div className="preview-main"><span className="mini-label">SESSION / 001</span><strong>Initial assessment</strong><div className="preview-segment"><span className="time">00:00</span><span className="speaker">CLIENT</span><span>Example text about finding better sleep.</span></div><div className="preview-evidence"><Icon name="search" size={14} /> 1 source-linked excerpt</div></div></div><div className="preview-footer"><span>Clinician review</span><span className="mini-badge">In progress</span></div></div></div>;
}

function ExampleWorkspace() {
  return <div className="example-workspace"><div className="example-heading"><span className="mini-label">TRANSCRIPT</span><span className="mini-badge">Example data</span></div><div className="example-row"><span className="time">00:14</span><span className="speaker">CLIENT</span><p>“I have been trying to make room for rest.”</p></div><div className="source-snippet"><Icon name="search" size={16} /><div><strong>Source excerpt</strong><p>“make room for rest” · linked to 00:14</p></div></div><div className="note-snippet"><span className="mini-label">SUBJECTIVE</span><p>Client describes a desire to create more space for rest.</p><span className="workflow-badge">Draft</span></div></div>;
}

function Value({ icon, title, text }: { icon: "check" | "file" | "shield"; title: string; text: string }) { return <article className="value-card"><span className="value-icon"><Icon name={icon} /></span><h3>{title}</h3><p>{text}</p></article>; }
function Step({ number, icon, title, text }: { number: string; icon: "file" | "search" | "shield"; title: string; text: string }) { return <article className="step-card"><span className="step-number">{number}</span><span className="value-icon"><Icon name={icon} /></span><h3>{title}</h3><p>{text}</p></article>; }

import { Icon } from "../../components/ui/Icon";
import { Brand, TopBar } from "../../components/layout/AppShell";
import type { Health } from "../../lib/api";

export function LandingPage({ health, error }: { health: Health | null; error: string | null }) {
  return <div className="landing">
    <TopBar health={health} />
    <main>
      <section className="hero section-wrap">
        <div className="hero-copy">
          <p className="eyebrow">The office is open. Yes, really.</p>
          <h1>Take a seat.<br /><em>The paperwork can wait.</em></h1>
          <p className="hero-lede">Well, briefly. Keep transcripts, source excerpts, and session notes together—so remembering where you put everything doesn’t become a session of its own.</p>
          <div className="hero-actions"><a className="button button-primary" href="/workspace">Open workspace <Icon name="arrow" /></a><a className="text-link" href="#workflow">See how it works <Icon name="chevron" /></a></div>
          <div className="hero-status">{error ?? (health ? "A local workspace for fictional examples" : "Preparing your local workspace…")}</div>
        </div>
        <HeroIllustration />
      </section>
      <section className="trust-strip"><div className="section-wrap trust-inner"><span><Icon name="file" /> Receipts, essentially to the note.</span><span><Icon name="shield" /> Clinicians make the decisions.</span><span><Icon name="wave" /> Built for local-first use.</span></div></section>
      <section className="section-wrap content-section" id="approach">
        <div className="section-heading"><p className="eyebrow">Organization. We’re trying it.</p><h2>“I’ll remember that” has a poor track record.</h2><p>Keep the transcript beside the note and the source a click away. Your memory has enough side projects.</p></div>
        <div className="value-grid"><Value icon="file" title="Receipts, essentially" text="Timestamped transcripts keep the original words close by. “I’m pretty sure they said that” can take the afternoon off." /><Value icon="check" title="You’re still in charge" text="Edit and review the notes. Clinical decisions stay with the clinician. The software has a desk job." /><Value icon="shield" title="A modest home office" text="A local workspace for your examples and drafts. Somehow, the notes have managed without a corner office." /></div>
      </section>
      <section className="section-wrap workflow-section" id="workflow">
        <div className="section-heading"><p className="eyebrow">Around here, it goes like this</p><h2>A routine. How unexpectedly reassuring.</h2></div>
        <div className="workflow-grid"><Step number="01" icon="file" title="Bring in a session" text="Open a fictional session. A clean record, before the conversation develops subplots." /><Step number="02" icon="search" title="Review transcript and sources" text="Check the words, correct the speakers, and follow excerpts back to their source. Transcripts also benefit from a second opinion." /><Step number="03" icon="shield" title="Shape and review the note" text="Edit the note and complete clinician review before approval. Yes, there’s still a little paperwork." /></div>
      </section>
      <section className="section-wrap showcase-section">
        <div className="showcase-copy"><p className="eyebrow">Example workspace</p><h2>The notes have finally agreed to stay together.</h2><p>Transcript, source excerpt, note, review status. All in one place. We considered a pile of loose paper, but it tested poorly.</p><a className="text-link" href="/workspace">Open the workspace <Icon name="arrow" size={15} /></a></div>
        <ExampleWorkspace />
      </section>
      <section className="section-wrap about-section" id="about"><div className="about-mark"><img src="/images/katz.png" alt="" /></div><div><p className="eyebrow">A familiar face. Same chair.</p><h2>He brought a notebook. We took the hint.</h2><p>Inspired by the illustrated world of Dr. Katz, Professional Therapist: a comfortable chair, a patient listener, and the suspicion that the paperwork is multiplying. We handle the organizing. Clinical decisions stay with the clinician.</p></div></section>
      <section className="section-wrap scope-section" id="scope"><div><p className="eyebrow">Honest scope</p><h2>Documentation support, not autonomous judgment.</h2></div><p>This workspace helps organize and review fictional session material. It does not diagnose, predict risk, recommend treatment, or replace professional judgment.</p></section>
    </main>
    <footer className="site-footer section-wrap"><Brand /><div><a href="/workspace">Workspace</a><a href="/#workflow">Workflow</a><a href="/#about">About</a></div><span>The office is local. The paperwork is persistent.</span></footer>
  </div>;
}

function HeroIllustration() {
  return <div className="hero-art">
    <span className="office-caption">THE OFFICE IS OPEN</span>
    <div className="office-window" aria-hidden="true"><span /><span /><span /><span /></div>
    <div className="speech-bubble">Take your time.<br /><em>I brought a notebook.</em></div>
    <div className="office-rug" aria-hidden="true" />
    <div className="mascot-wrap"><img src="/images/katz.png" alt="Dr. Katz seated in his armchair with a notebook" fetchPriority="high" /></div>
    <div className="office-note"><span className="mini-label">A NOTE TO OURSELVES</span><strong>Listen first.<br />Misplace fewer notes.</strong><span><Icon name="check" size={15} /> An ambitious filing strategy.</span></div>
    <span className="office-art-label">THE CHAIR HAS HEARD A LOT.</span>
  </div>;
}

function ExampleWorkspace() {
  return <div className="example-workspace"><div className="example-heading"><span className="mini-label">TRANSCRIPT</span><span className="mini-badge">Example data</span></div><div className="example-row"><span className="time">00:14</span><span className="speaker">CLIENT</span><p>“I have been trying to make room for rest.”</p></div><div className="source-snippet"><Icon name="search" size={16} /><div><strong>Source excerpt</strong><p>“make room for rest” · linked to 00:14</p></div></div><div className="note-snippet"><span className="mini-label">SUBJECTIVE</span><p>Client describes a desire to create more space for rest.</p><span className="workflow-badge">Draft</span></div></div>;
}

function Value({ icon, title, text }: { icon: "check" | "file" | "shield"; title: string; text: string }) { return <article className="value-card"><span className="value-icon"><Icon name={icon} /></span><h3>{title}</h3><p>{text}</p></article>; }
function Step({ number, icon, title, text }: { number: string; icon: "file" | "search" | "shield"; title: string; text: string }) { return <article className="step-card"><span className="step-number">{number}</span><span className="value-icon"><Icon name={icon} /></span><h3>{title}</h3><p>{text}</p></article>; }

import { useCallback, useEffect, useState } from "react";
import { DSM_STATUS, progressApi, type DsmFinding, type DsmRegistryView, type InstrumentScore, type RiskStatement } from "./api";
import { Citations, ReviewActions, ReviewBadge } from "./common";
import { FindingCard, FindingFields } from "./Dsm";
import { JobStatus, useProgressJob } from "./jobs";
import { RiskList, RiskReport } from "./RiskStatements";

/** Session-level DSM findings beside the note editor. Pending reviews never block note approval. */
export function DsmSessionPanel({ sessionId, patientId, decided, onChanged }: { sessionId: string; patientId: string; decided: boolean; onChanged?: () => void }) {
  const [registry, setRegistry] = useState<DsmRegistryView | null>(null);
  const [findings, setFindings] = useState<DsmFinding[]>([]);
  const [scores, setScores] = useState<InstrumentScore[]>([]);
  const [risks, setRisks] = useState<RiskStatement[]>([]);
  const [reviewed, setReviewed] = useState(false);
  const [entering, setEntering] = useState<string | null>(null);
  const [actor, setActor] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const load = useCallback(async () => {
    try {
      const [f, sc, rs] = await Promise.all([progressApi.dsmFindings(sessionId), progressApi.scores(patientId), progressApi.progress(patientId).then(p => p.risk_statements)]);
      setFindings(f); setScores(sc.filter(s => s.session_id === sessionId)); setRisks(rs.filter(r => r.session_id === sessionId)); setLoaded(true);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load findings."); }
  }, [sessionId, patientId]);
  const job = useProgressJob(() => { void load(); onChanged?.(); });
  useEffect(() => { progressApi.dsmRegistry().then(setRegistry).catch(() => {}); void load(); }, [load]);
  useEffect(() => { progressApi.sessionJobs(sessionId).then(jobs => { const running = jobs.find(j => j.status === "running"); if (running) job.attach(running.id); }).catch(() => {}); }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps
  const byKey = new Map(findings.map(f => [f.feature_key, f]));
  const counted = findings.filter(f => f.status !== "insufficient_evidence");
  const pending = [...counted, ...scores, ...risks].filter(v => v.review_status === "pending" || v.review_status === "stale").length;
  const stale = [...findings, ...scores, ...risks].filter(v => v.review_status === "stale").length;
  async function saveManual(key: string, value: Record<string, unknown>) {
    if (!actor.trim()) { setError("Enter your name before saving."); return; }
    setError("");
    try { await progressApi.putDsmFinding(sessionId, key, { ...value, actor: actor.trim() }); setEntering(null); await load(); onChanged?.(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save."); }
  }
  return <section className="panel progress-panel observations-panel" aria-labelledby="dsm-obs-title">
    <div className="panel-heading"><div><p className="eyebrow">Progress monitoring · DSM-5-TR criteria</p><h3 id="dsm-obs-title">DSM findings</h3>
      <p className="small-note">{pending} value{pending === 1 ? "" : "s"} pending review. Pending reviews do not block note approval. <a className="text-link" href={`/workspace/patients/${patientId}/progress`}>Open progress dashboard</a></p></div></div>
    {stale > 0 && <div className="stale-banner" role="alert">Sources changed for {stale} value{stale === 1 ? "" : "s"}. Regenerate the analysis or re-review them against the current transcript.</div>}
    <div className="analyse-bar">
      <label><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={decided} /> I have reviewed this transcript (speaker roles and text)</label>
      <button type="button" className="button button-small" disabled={decided || !reviewed || job.running} onClick={() => void job.start(() => progressApi.startExtraction(sessionId, { transcript_reviewed: true }))}>Analyse this session</button>
      {decided && <span className="small-note">The note is decided; findings are read-only.</span>}
    </div>
    <JobStatus job={job} />
    {error && <p className="form-error" role="alert">{error}</p>}
    <div className="panel-tools"><label className="inline-field">Entering as<input value={actor} onChange={e => setActor(e.target.value)} placeholder="Clinician name" disabled={decided} /></label>
      <label><input type="checkbox" checked={showAll} onChange={e => setShowAll(e.target.checked)} /> Show features without evidence</label></div>
    {registry && registry.feature_groups.map(g => {
      const feats = registry.features.filter(f => f.group === g.key && (showAll || (byKey.get(f.key)?.status ?? "insufficient_evidence") !== "insufficient_evidence" || entering === f.key));
      if (!feats.length) return null;
      return <div key={g.key} className="obs-group"><h4>{g.label}</h4><ul className="card-list">{feats.map(feat => { const f = byKey.get(feat.key); return <li key={feat.key}>
        {f ? <FindingCard f={f} registry={registry} decided={decided} onChanged={async () => { await load(); onChanged?.(); }} />
          : <div className="obs-card"><div className="card-head"><strong>{feat.label}</strong><span className="small-note">Not assessed</span></div></div>}
        {!decided && <button type="button" className="text-button" onClick={() => setEntering(entering === feat.key ? null : feat.key)}>{f?.provenance === "MANUAL" ? "Edit manual entry" : "Enter manually"}</button>}
        {entering === feat.key && <FindingFields initial={f ?? {}} qualifiers={feat.qualifiers} degree={feat.degree} contexts={registry.contexts} intensity submitLabel="Save manual entry" submit={value => void saveManual(feat.key, value)} />}
      </li>; })}</ul></div>;
    })}
    {!loaded && !error && <p className="small-note" role="status">Loading findings…</p>}
    {loaded && !findings.length && <p className="empty-note">No DSM findings for this session yet. Analyse the session, or enter findings manually ({Object.values(DSM_STATUS).slice(0, 3).map(s => s.text.toLowerCase()).join(", ")}…).</p>}
    {scores.length > 0 && <div className="obs-group"><h4>Questionnaire items</h4><ul className="card-list">{scores.map(s => <li key={s.id} className="obs-card">
      <div className="card-head"><strong>{s.instrument_key.toUpperCase()} {s.provenance === "AI_GENERATED" ? "AI estimate" : "administered"}</strong><span>Total {s.total ?? "—"}{s.total_is_partial ? " (partial)" : ""}{s.subtotals.phq8 ? ` · PHQ-8 ${s.subtotals.phq8.total ?? "—"}` : ""}</span><ReviewBadge info={s} compact /></div>
      <table className="progress-table compact"><tbody>{s.items.map(i => <tr key={i.item_id}><th scope="row">Item {i.item_id}</th><td>{i.score ?? (i.status === "insufficient_evidence" ? "insufficient evidence" : "—")}</td><td className="small-note">{i.basis}</td></tr>)}</tbody></table>
      {s.evidence.length > 0 && <details><summary>Cited turns ({s.evidence.length})</summary><Citations items={s.evidence} /></details>}
      <ReviewActions info={s} disabled={decided} onReview={async p => { await progressApi.reviewScore(patientId, s.id, p); await load(); onChanged?.(); }} />
    </li>)}</ul></div>}
    <RiskReport id={`risk-session-${sessionId}`} title="Flagged statements in this session" risks={risks.filter(r => r.review_status !== "rejected")} rejected={risks.filter(r => r.review_status === "rejected")}>
      <RiskList risks={risks.filter(r => r.review_status !== "rejected")} decided={decided} onReview={async (r, p) => { await progressApi.reviewRisk(patientId, r.id, p); await load(); }} />
    </RiskReport>
  </section>;
}

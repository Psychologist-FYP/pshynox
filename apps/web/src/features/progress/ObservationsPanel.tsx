import { useCallback, useEffect, useState } from "react";
import { DOMAIN_STATUS_LABEL, RISK_LABEL, progressApi, type DomainObservation, type InstrumentScore, type RiskStatement } from "./api";
import { Citations, ReviewActions, ReviewBadge } from "./common";
import { DomainEditFields } from "./panels";
import { RiskList, RiskReport } from "./RiskStatements";
import { JobStatus, useProgressJob } from "./jobs";
import { DsmSessionPanel } from "./DsmSessionPanel";
import "./progress.css";

type Registry = Awaited<ReturnType<typeof progressApi.registry>>;

/** Session-level observations beside the note editor. Pending reviews never block note approval. */
export function ObservationsPanel({ sessionId, patientId, decided, onChanged }: { sessionId: string; patientId: string | null; decided: boolean; onChanged?: () => void }) {
  const [registry, setRegistry] = useState<Registry | null>(null);
  const [observations, setObservations] = useState<DomainObservation[]>([]);
  const [scores, setScores] = useState<InstrumentScore[]>([]);
  const [risks, setRisks] = useState<RiskStatement[]>([]);
  const [reviewed, setReviewed] = useState(false);
  const [entering, setEntering] = useState<string | null>(null);
  const [actor, setActor] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    if (!patientId) return;
    try {
      const [obs, sc, rs] = await Promise.all([progressApi.observations(sessionId), progressApi.scores(patientId), progressApi.progress(patientId).then(p => p.risk_statements)]);
      setObservations(obs); setScores(sc.filter(s => s.session_id === sessionId)); setRisks(rs.filter(r => r.session_id === sessionId));
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load observations."); }
  }, [sessionId, patientId]);
  const job = useProgressJob(() => { void load(); onChanged?.(); });
  useEffect(() => { progressApi.registry().then(setRegistry).catch(() => {}); void load(); }, [load]);
  useEffect(() => { progressApi.sessionJobs(sessionId).then(jobs => { const running = jobs.find(j => j.status === "running"); if (running) job.attach(running.id); }).catch(() => {}); }, [sessionId]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!patientId) return <section className="panel progress-panel"><h3>Observations</h3><p className="small-note">Link this session to a patient to record progress observations.</p></section>;
  if (registry?.active === "dsm") return <DsmSessionPanel sessionId={sessionId} patientId={patientId} decided={decided} onChanged={onChanged} />;
  const pending = [...observations, ...scores, ...risks].filter(v => v.review_status === "pending" || v.review_status === "stale").length;
  const stale = [...observations, ...scores, ...risks].filter(v => v.review_status === "stale").length;
  const byDomain = new Map(observations.map(o => [o.domain_key, o]));
  async function saveManual(domainKey: string, value: Record<string, unknown>) {
    setError("");
    try { await progressApi.putObservation(sessionId, domainKey, { ...value, actor }); setEntering(null); await load(); onChanged?.(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save."); }
  }
  return <section className="panel progress-panel observations-panel" aria-labelledby="obs-title">
    <div className="panel-heading"><div><p className="eyebrow">Progress monitoring</p><h3 id="obs-title">Observations</h3>
      <p className="small-note">{pending} value{pending === 1 ? "" : "s"} pending review. Pending reviews do not block note approval. <a className="text-link" href={`/workspace/patients/${patientId}/progress`}>Open progress dashboard</a></p></div></div>
    {stale > 0 && <div className="stale-banner" role="alert">Sources changed for {stale} value{stale === 1 ? "" : "s"}. Regenerate the analysis or re-review them against the current transcript.</div>}
    <div className="analyse-bar">
      <label><input type="checkbox" checked={reviewed} onChange={e => setReviewed(e.target.checked)} disabled={decided} /> I have reviewed this transcript (speaker roles and text)</label>
      <button type="button" className="button button-small" disabled={decided || !reviewed || job.running} onClick={() => void job.start(() => progressApi.startExtraction(sessionId, { transcript_reviewed: true }))}>Analyse this session</button>
      {decided && <span className="small-note">The note is decided; observations are read-only.</span>}
    </div>
    <JobStatus job={job} />
    {error && <p className="form-error" role="alert">{error}</p>}
    <label className="inline-field">Entering as<input value={actor} onChange={e => setActor(e.target.value)} placeholder="Clinician name" disabled={decided} /></label>
    {registry && registry.domains.groups.map(g => <div key={g.key} className="obs-group"><h4>{g.label}</h4><ul className="card-list">
      {registry.domains.domains.filter(d => d.group === g.key).map(d => { const o = byDomain.get(d.key); return <li key={d.key} className="obs-card">
        <div className="card-head"><strong>{d.label}</strong>{o ? <><span>{o.status_value === "rated" ? `Rating ${o.rating}` : DOMAIN_STATUS_LABEL[o.status_value]}{o.direction ? ` · ${o.direction.replaceAll("_", " ")}` : ""}{o.timeframe === "past" ? " · past" : ""}</span><ReviewBadge info={o} compact /></> : <span className="small-note">Not assessed</span>}
          {!decided && <button type="button" className="text-button" onClick={() => setEntering(entering === d.key ? null : d.key)}>{o?.provenance === "MANUAL" ? "Edit" : "Enter manually"}</button>}</div>
        {o?.basis && <p className="small-note">{o.basis}</p>}
        {o && o.evidence.length > 0 && <details><summary>Cited turns ({o.evidence.length})</summary><Citations items={o.evidence} /></details>}
        {o && <ReviewActions info={o} disabled={decided} onReview={async p => { await progressApi.reviewObservation(sessionId, o.id, p); await load(); onChanged?.(); }} editForm={submit => <DomainEditFields initial={o} directions={d.directions} submit={submit} />} />}
        {entering === d.key && <DomainEditFields initial={o ?? {}} directions={d.directions} submitLabel="Save manual entry" submit={value => { if (!actor.trim()) { setError("Enter your name before saving."); return; } void saveManual(d.key, value); }} />}
      </li>; })}</ul></div>)}
    {scores.length > 0 && <div className="obs-group"><h4>Questionnaire items</h4><ul className="card-list">{scores.map(s => <li key={s.id} className="obs-card">
      <div className="card-head"><strong>{s.instrument_key.toUpperCase()} {s.provenance === "AI_GENERATED" ? "AI estimate" : "administered"}</strong><span>Total {s.total ?? "—"}{s.total_is_partial ? " (partial)" : ""}{s.subtotals.phq8 ? ` · PHQ-8 ${s.subtotals.phq8.total ?? "—"}` : ""}</span><ReviewBadge info={s} compact /></div>
      <table className="progress-table compact"><tbody>{s.items.map(i => <tr key={i.item_id}><th scope="row">Item {i.item_id}</th><td>{i.score ?? (i.status === "insufficient_evidence" ? "insufficient evidence" : "—")}</td><td className="small-note">{i.basis}</td></tr>)}</tbody></table>
      {s.evidence.length > 0 && <details><summary>Cited turns ({s.evidence.length})</summary><Citations items={s.evidence} /></details>}
      <ReviewActions info={s} disabled={decided} onReview={async p => { await progressApi.reviewScore(patientId, s.id, p); await load(); onChanged?.(); }} />
    </li>)}</ul></div>}
    <RiskReport id={`risk-session-${sessionId}`} title="Flagged statements in this session" risks={risks.filter(r => r.review_status !== "rejected")} rejected={risks.filter(r => r.review_status === "rejected")}>
      <RiskList risks={risks.filter(r => r.review_status !== "rejected")} decided={decided}
        onReview={async (r, p) => { await progressApi.reviewRisk(patientId, r.id, p); await load(); }} />
    </RiskReport>
  </section>;
}

import { useEffect, useMemo, useState } from "react";
import { WorkspaceShell } from "../../components/layout/AppShell";
import type { Health } from "../../lib/api";
import { REGISTRY_EXPORT, progressApi, type DsmRegistryView } from "./api";
import { VERIFICATION_TEXT, VerificationBadge } from "./Dsm";
import "./progress.css";

/** Every DSM criterion item and rule with its paraphrase, ids, codes and clinical verification status. */
export function RegistryPage({ health }: { health: Health | null }) {
  const [view, setView] = useState<DsmRegistryView | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState("all");
  const [actor, setActor] = useState("");
  const [result, setResult] = useState<Awaited<ReturnType<typeof progressApi.importRegistryReview>> | null>(null);
  const load = () => progressApi.dsmRegistry().then(setView).catch(e => setError(e.message));
  useEffect(() => { void load(); }, []);
  const items = useMemo(() => (view?.items ?? []).filter(i => (status === "all" || i.verification.status === status)
    && `${i.id} ${i.label} ${i.paraphrase}`.toLowerCase().includes(filter.toLowerCase())), [view, filter, status]);
  async function importFile(file: File) {
    if (!actor.trim()) { setError("Enter your name before importing."); return; }
    setError("");
    try { setResult(await progressApi.importRegistryReview({ actor: actor.trim(), csv: await file.text() })); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Import failed."); }
  }
  const counts = (view?.items ?? []).reduce<Record<string, number>>((m, i) => ({ ...m, [i.verification.status]: (m[i.verification.status] ?? 0) + 1 }), {});
  return <WorkspaceShell health={health} active="DSM registry"><main className="section-wrap progress-page registry-page">
    <header className="progress-header panel"><div><p className="eyebrow">Progress monitoring · registry v2</p><h2>DSM-5-TR criteria registry</h2>
      <p>{view ? `${view.version} · ${view.items.length} criterion items · ${view.conditions.length} conditions · active registry: ${view.active}` : "Loading…"}</p>
      <p className="small-note">{view?.note} Each paraphrase is at most 20 words and is shown as “{VERIFICATION_TEXT.pending}” until a clinician verifies it.</p></div>
      <div className="header-stats"><p><strong>{counts.verified ?? 0}</strong> verified · <strong>{counts.changed ?? 0}</strong> change requested · <strong>{counts.pending ?? 0}</strong> pending</p>
        <div className="panel-tools"><a className="button button-small" href={REGISTRY_EXPORT.csv} download>Export CSV for review</a><a className="button button-small button-secondary" href={REGISTRY_EXPORT.html} target="_blank" rel="noreferrer">Printable review sheet</a></div>
        <div className="administration-form" aria-label="Import clinician decisions">
          <p className="small-note">Import the returned CSV (decision: verified, changed or pending). Ids and wording never change on import; a “changed” decision records the suggested wording as a comment.</p>
          <div className="form-row"><label>Imported by<input value={actor} onChange={e => setActor(e.target.value)} /></label>
            <label>Review CSV<input type="file" accept=".csv,text/csv" onChange={e => { const f = e.target.files?.[0]; if (f) void importFile(f); }} /></label></div>
          {result && <p className="small-note" role="status">Recorded {result.recorded} decision{result.recorded === 1 ? "" : "s"}{result.rejected.length ? `; ${result.rejected.length} rejected` : ""}.</p>}
          {result?.rejected.length ? <ul className="small-note">{result.rejected.map(r => <li key={`${r.line}-${r.id}`}>Line {r.line} {r.id}: {r.reason}</li>)}</ul> : null}
        </div></div>
    </header>
    {error && <div className="state-panel state-error" role="alert">{error}</div>}
    {view && <>
      <section className="panel progress-panel" aria-labelledby="reg-conditions"><h3 id="reg-conditions">Conditions and rules</h3>
        <div className="table-scroll"><table className="progress-table"><thead><tr><th scope="col">Condition</th><th scope="col">Role</th><th scope="col">Rule (computed in code)</th><th scope="col">ICD-10-CM</th><th scope="col">ICD-11</th><th scope="col">Verification</th></tr></thead>
          <tbody>{view.conditions.map(c => <tr key={c.key}><th scope="row">{c.label}<span className="small-note"> · {c.chapter}</span></th><td>{c.role}</td><td className="small-note">{c.rule_summary}</td><td>{c.icd10cm}</td><td>{c.icd11}</td><td><VerificationBadge status={c.verification.status} compact /></td></tr>)}</tbody></table></div></section>
      <section className="panel progress-panel" aria-labelledby="reg-items"><div className="panel-heading"><h3 id="reg-items">Criterion items</h3>
        <div className="panel-tools"><label className="inline-field">Filter<input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Id or words" /></label>
          <label className="inline-field">Status<select value={status} onChange={e => setStatus(e.target.value)}><option value="all">All</option><option value="pending">Pending</option><option value="verified">Verified</option><option value="changed">Change requested</option></select></label></div></div>
        <div className="table-scroll"><table className="progress-table"><thead><tr><th scope="col">DSM id</th><th scope="col">Paraphrase (our words)</th><th scope="col">Chapter</th><th scope="col">Kind</th><th scope="col">Verification</th></tr></thead>
          <tbody>{items.map(i => <tr key={i.id}><th scope="row"><span className="dsm-id">{i.id}</span></th><td>{i.paraphrase}{i.notes && <span className="small-note"> · {i.notes}</span>}</td><td className="small-note">{i.chapter}</td><td>{i.kind}</td>
            <td><VerificationBadge status={i.verification.status} compact />{i.verification.verified_by && <span className="small-note"> {i.verification.verified_by} · {i.verification.verified_on}</span>}{i.verification.comment && <span className="small-note"> · {i.verification.comment}</span>}</td></tr>)}</tbody></table></div></section>
      <section className="panel progress-panel" aria-labelledby="reg-features"><h3 id="reg-features">Extraction features (shared findings)</h3>
        <p className="small-note">What the model looks for in each session. One finding can count for several criteria; related symptoms with different meanings stay separate, for the reasons given.</p>
        <div className="table-scroll"><table className="progress-table"><thead><tr><th scope="col">Feature</th><th scope="col">Definition</th><th scope="col">Kept separate from</th></tr></thead>
          <tbody>{view.features.map(f => <tr key={f.key}><th scope="row">{f.label}<span className="small-note"> · {f.key}</span></th><td>{f.definition}</td><td className="small-note">{f.separate_from.map(s => `${s.feature}: ${s.reason}`).join(" ")}</td></tr>)}</tbody></table></div></section>
    </>}
  </main></WorkspaceShell>;
}

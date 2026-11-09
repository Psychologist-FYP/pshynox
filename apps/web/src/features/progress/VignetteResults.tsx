import { useEffect, useState } from "react";
import { request } from "../../lib/api";

type Binary = { n: number; tp: number; fp: number; fn: number; tn: number; sensitivity: number | null; specificity: number | null; ppv: number | null; npv: number | null; f1: number | null;
  sensitivity_ci95: [number, number] | null; specificity_ci95: [number, number] | null };
type ConditionResult = { n_labelled: number; truth_counts: Record<string, number>; label_counts: Record<string, number>; failed: number; at_possible: Binary; at_likely: Binary; subtype: { n: number; exact: number | null } };
type VignetteReport = { label: string; run_id: string; complete: boolean; cases_completed: number; cases_expected: number; cases_changed_since_run: string[];
  conditions: Record<string, ConditionResult>;
  criteria: { n: number; exact_agreement: number | null; agreement_when_both_decided: number | null; n_both_decided: number; model_abstained_when_labelled: number | null; met_kappa: number | null; by_condition: Record<string, number | null> };
  domains: { n_labelled: number; n_both_rated: number; mae: number | null; exact: number | null; within_1: number | null; model_abstained_when_rated: number | null; model_rated_when_label_insufficient: number };
  cases: { case_id: string; truth: Record<string, string>; predictions: Record<string, { prediction: string | null; subtype_key: string | null; status?: string }> }[]; notes: string[] };
type Run = { run_id: string; state: string | null; completed: number | null; total: number | null; has_report: boolean };

const CONDITION: Record<string, string> = { depressive_episode: "Depressive episode", bipolar: "Bipolar features", gad: "Generalized anxiety", social_anxiety: "Social anxiety" };
const pct = (v: number | null | undefined) => typeof v === "number" ? `${Math.round(v * 100)}%` : "—";
const ci = (c: [number, number] | null | undefined) => c ? `${pct(c[0])}–${pct(c[1])}` : "";

/** Vignette-set results (consultant labels vs model output). Hidden until a vignette run exists. */
export function VignetteResults() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState("");
  const [report, setReport] = useState<VignetteReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { request<Run[]>("/api/v1/research/progress/vignette-runs").then(r => { setRuns(r); const first = r.find(x => x.has_report); if (first) setRunId(first.run_id); }).catch(e => setError(e.message)); }, []);
  useEffect(() => { if (!runId) return; setReport(null); request<VignetteReport>(`/api/v1/research/progress/vignette-runs/${runId}/report`).then(setReport).catch(e => setError(e.message)); }, [runId]);
  return <section className="panel progress-panel" aria-labelledby="vignette-title">
    <div className="panel-heading"><div><p className="eyebrow">Clinician-labelled vignettes</p><h3 id="vignette-title">Vignette set: all four conditions</h3>
      <p className="small-note">Agreement with the consultant's blind labels on written synthetic cases. Not clinical accuracy.</p></div>
      {runs.length > 0 && <label className="inline-field">Run<select value={runId} onChange={e => setRunId(e.target.value)}><option value="">Choose a run</option>
        {runs.map(r => <option key={r.run_id} value={r.run_id} disabled={!r.has_report}>{r.run_id} · {r.completed ?? 0}/{r.total ?? "?"} cases{r.has_report ? "" : " · no report"}</option>)}</select></label>}</div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {!runs.length && !error && <p className="empty-note">No vignette runs yet. Author cases (docs/VIGNETTE_AUTHORING_GUIDE.md), then run
      <code> python -m services.progress.evaluation.vignettes run --run-id &lt;id&gt;</code> and <code>report</code>.</p>}
    {report && <>
      {!report.complete && <p className="stale-banner" role="status">Incomplete: {report.cases_completed}/{report.cases_expected} cases{report.cases_changed_since_run.length ? `; edited since the run: ${report.cases_changed_since_run.join(", ")}` : ""}.</p>}
      <table className="progress-table"><caption>Per condition (truth = consultant status “present”)</caption>
        <thead><tr><th scope="col">Condition</th><th>Labelled</th><th>Present</th><th>Sens · ≥ possible</th><th>Spec · ≥ possible</th><th>Sens · likely</th><th>Spec · likely</th><th>Abstained</th><th>Subtype exact</th></tr></thead>
        <tbody>{Object.entries(report.conditions).map(([k, m]) => <tr key={k}><th scope="row">{CONDITION[k] ?? k}</th><td>{m.n_labelled}</td><td>{m.truth_counts.present ?? 0}</td>
          <td>{pct(m.at_possible.sensitivity)} <span className="small-note">{ci(m.at_possible.sensitivity_ci95)}</span></td><td>{pct(m.at_possible.specificity)} <span className="small-note">{ci(m.at_possible.specificity_ci95)}</span></td>
          <td>{pct(m.at_likely.sensitivity)}</td><td>{pct(m.at_likely.specificity)}</td><td>{m.label_counts.insufficient_evidence ?? 0}</td><td>{m.subtype.n ? `${pct(m.subtype.exact)} (n=${m.subtype.n})` : "—"}</td></tr>)}</tbody></table>
      <table className="progress-table"><caption>Criterion and domain agreement</caption><tbody>
        <tr><th scope="row">Criteria labelled</th><td>{report.criteria.n}</td></tr>
        <tr><th scope="row">Exact agreement (met / not met / insufficient)</th><td>{pct(report.criteria.exact_agreement)}</td></tr>
        <tr><th scope="row">Agreement when both decided</th><td>{pct(report.criteria.agreement_when_both_decided)} (n={report.criteria.n_both_decided})</td></tr>
        <tr><th scope="row">Model abstained on a decided criterion</th><td>{pct(report.criteria.model_abstained_when_labelled)}</td></tr>
        <tr><th scope="row">Cohen's κ, met vs not met</th><td>{typeof report.criteria.met_kappa === "number" ? report.criteria.met_kappa.toFixed(2) : "—"}</td></tr>
        <tr><th scope="row">Session domain ratings: MAE / exact / within 1</th><td>{typeof report.domains.mae === "number" ? report.domains.mae.toFixed(2) : "—"} / {pct(report.domains.exact)} / {pct(report.domains.within_1)} (n={report.domains.n_both_rated})</td></tr>
        <tr><th scope="row">Model abstained on a rated domain</th><td>{pct(report.domains.model_abstained_when_rated)}</td></tr>
      </tbody></table>
      <details><summary>Per case ({report.cases.length})</summary><table className="progress-table"><thead><tr><th scope="col">Case</th>{Object.keys(report.conditions).map(k => <th key={k}>{CONDITION[k] ?? k}</th>)}</tr></thead>
        <tbody>{report.cases.map(c => <tr key={c.case_id}><th scope="row">{c.case_id}</th>{Object.keys(report.conditions).map(k => <td key={k}>{c.truth[k] ?? "—"} → {c.predictions[k]?.prediction ?? "failed"}{c.predictions[k]?.subtype_key ? ` (${c.predictions[k].subtype_key})` : ""}</td>)}</tr>)}</tbody></table></details>
      <ul className="small-note">{report.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
    </>}
  </section>;
}

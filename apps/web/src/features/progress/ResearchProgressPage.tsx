import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { request, type Health } from "../../lib/api";
import { WorkspaceShell } from "../../components/layout/AppShell";
import { CHART, axisTick } from "./chartTheme";
import "./progress.css";

type Metric = { value: number | null; ci95: [number, number] | null };
type Session = { session_index: number; ai_total: number; gt_total: number; label: string | null; ai_items: Record<string, number | null>; gt_items: Record<string, number>; rated_items: number;
  findings: { criterion_id: string; status: string; evidence: { segment_id: string; speaker_role?: string; basis: string }[] }[]; rationale: { sentence: string; segment_ids: string[] }[] };
type Report = { label: string; run_id: string; complete: boolean; sessions_completed: number; sessions_expected: number; status: string | null;
  config: { arm: string; evidence: string; history: string; subset: { path: string; split: string } };
  metrics: Record<string, Metric | number>; item: Record<string, number>; stratified_mae: Record<string, { mae: number; n_sessions: number }>;
  depression: { auroc?: number; auprc?: number; roc?: { fpr: number; tpr: number }[]; at_possible: Record<string, number | string>; at_likely: Record<string, number | string>;
    calibration: { method: string; brier: number | null; reliability: { mean_predicted: number; observed_rate: number; n: number }[] }; label_counts: Record<string, number> };
  trajectory: { agreement?: number; n_trajectories?: number }; baselines: Record<string, { current_mae: number; change_mae: number }>;
  runtime: Record<string, unknown>; trajectories: { run_id: string; type: string; sessions: Session[] }[]; notes: string[] };
type Run = { run_id: string; arm: string; split: string; state: string; completed: number; total: number; has_report: boolean };

const GT = CHART.blue, AI = CHART.orange;
const METRIC_LABEL: Record<string, string> = { current_mae: "Current PHQ-8 MAE", change_mae: "Change MAE (adjacent sessions)", item_mae_rated: "Item MAE (rated items)",
  item_exact_match_rated: "Item exact match (rated)", coverage: "Item coverage (rated / all)", large_change_direction_accuracy: "Large-change direction accuracy (|ΔGT| ≥ 5; ties wrong)",
  auroc: "AUROC (PHQ-8 ≥ 10)", auprc: "AUPRC (PHQ-8 ≥ 10)" };
const f = (v: unknown, d = 3) => typeof v === "number" ? v.toFixed(d) : "—";

export function ResearchProgressPage({ health }: { health: Health | null }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [traj, setTraj] = useState("");
  useEffect(() => { request<Run[]>("/api/v1/research/progress/runs").then(r => { setRuns(r); const first = r.find(x => x.has_report); if (first) setRunId(first.run_id); }).catch(e => setError(e.message)); }, []);
  useEffect(() => { if (!runId) return; setReport(null); request<Report>(`/api/v1/research/progress/runs/${runId}/report`).then(r => { setReport(r); setTraj(r.trajectories[0]?.run_id ?? ""); }).catch(e => setError(e.message)); }, [runId]);
  const strat = useMemo(() => Object.entries(report?.stratified_mae ?? {}).map(([type, v]) => ({ type, mae: v.mae, n: v.n_sessions })), [report]);
  const selected = report?.trajectories.find(t => t.run_id === traj);
  return <WorkspaceShell health={health} active="Research evaluation"><main className="section-wrap progress-page">
    <header className="progress-header panel"><div><p className="eyebrow">Research evaluation</p><h2>Progress benchmark results</h2>
      <p className="demo-banner"><strong>Synthetic benchmark (LongCounsel-8): depression track only.</strong> Not clinical validation; bipolar, GAD and social-anxiety hypotheses are unvalidated.</p></div>
      <label className="inline-field">Run<select value={runId} onChange={e => setRunId(e.target.value)}><option value="">Choose a run</option>{runs.map(r => <option key={r.run_id} value={r.run_id} disabled={!r.has_report}>{r.run_id} · {r.arm} · {r.split} · {r.completed}/{r.total}{r.has_report ? "" : " (no report yet)"}</option>)}</select></label></header>
    {error && <div className="state-panel state-error" role="alert">{error}</div>}
    {!runs.length && !error && <div className="state-panel">No benchmark runs yet. Follow docs/PROGRESS_BENCHMARK_RUNBOOK.md, then build a report.</div>}
    {report && <>
      {!report.complete && <p className="stale-banner" role="status">Incomplete run: {report.sessions_completed}/{report.sessions_expected} sessions. Change and trajectory metrics use complete trajectories only.</p>}
      <section className="panel progress-panel" aria-labelledby="metrics-title"><h3 id="metrics-title">Metrics · {report.config.arm} (evidence {report.config.evidence}, history {report.config.history})</h3>
        <table className="progress-table"><thead><tr><th scope="col">Metric</th><th scope="col">Value</th><th scope="col">95% CI (trajectory bootstrap)</th></tr></thead><tbody>
          {Object.entries(METRIC_LABEL).map(([k, label]) => { const m = report.metrics[k] as Metric | undefined; return <tr key={k}><th scope="row">{label}</th><td>{f(m?.value)}</td><td>{m?.ci95 ? `${f(m.ci95[0])} – ${f(m.ci95[1])}` : "—"}</td></tr>; })}
          <tr><th scope="row">Brier score (calibrated; {report.depression.calibration.method})</th><td>{f(report.depression.calibration.brier)}</td><td>—</td></tr>
          <tr><th scope="row">Trajectory descriptor agreement</th><td>{f(report.trajectory.agreement)}</td><td>n = {report.trajectory.n_trajectories ?? 0}</td></tr>
          {Object.entries(report.baselines).map(([k, b]) => <tr key={k}><th scope="row">Baseline: {k.replaceAll("_", " ")} (train self-reports)</th><td>current {f(b.current_mae)} · change {f(b.change_mae)}</td><td>—</td></tr>)}
        </tbody></table>
        <table className="progress-table"><caption>Depression hypothesis vs ground-truth PHQ-8 ≥ 10</caption><thead><tr><th scope="col">Threshold</th><th>Sens</th><th>Spec</th><th>PPV</th><th>NPV</th><th>F1</th></tr></thead>
          <tbody>{[report.depression.at_possible, report.depression.at_likely].map(t => <tr key={String(t.threshold)}><th scope="row">label ≥ {String(t.threshold)}</th><td>{f(t.sensitivity)}</td><td>{f(t.specificity)}</td><td>{f(t.ppv)}</td><td>{f(t.npv)}</td><td>{f(t.f1)}</td></tr>)}</tbody></table>
      </section>
      <div className="instrument-grid">
        <section className="panel progress-panel" aria-labelledby="roc-title"><h3 id="roc-title">ROC (AI PHQ-8 total as score)</h3>
          {report.depression.roc?.length ? <div className="chart-surface"><ResponsiveContainer width="100%" height={220}><LineChart data={report.depression.roc} margin={{ top: 8, right: 16, bottom: 16, left: 0 }}>
            <CartesianGrid stroke={CHART.grid} /><XAxis tick={axisTick} stroke={CHART.axis} type="number" dataKey="fpr" domain={[0, 1]} tickCount={6} label={{ value: "False positive rate", position: "insideBottom", offset: -8, fontSize: 11 }} />
            <YAxis tick={axisTick} stroke={CHART.axis} type="number" domain={[0, 1]} tickCount={6} width={34} /><ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke={CHART.axis} strokeWidth={1} />
            <Tooltip formatter={(v) => typeof v === "number" ? v.toFixed(3) : String(v)} /><Line type="stepAfter" dataKey="tpr" name="True positive rate" stroke={GT} strokeWidth={2} dot={false} isAnimationActive={false} /></LineChart></ResponsiveContainer></div> : <p className="empty-note">Needs both classes.</p>}</section>
        <section className="panel progress-panel" aria-labelledby="rel-title"><h3 id="rel-title">Reliability (calibrated probability)</h3>
          {report.depression.calibration.reliability.length ? <div className="chart-surface"><ResponsiveContainer width="100%" height={220}><LineChart data={report.depression.calibration.reliability} margin={{ top: 8, right: 16, bottom: 16, left: 0 }}>
            <CartesianGrid stroke={CHART.grid} /><XAxis tick={axisTick} stroke={CHART.axis} type="number" dataKey="mean_predicted" domain={[0, 1]} tickCount={6} label={{ value: "Mean predicted", position: "insideBottom", offset: -8, fontSize: 11 }} />
            <YAxis tick={axisTick} stroke={CHART.axis} type="number" domain={[0, 1]} tickCount={6} width={34} /><ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke={CHART.axis} strokeWidth={1} />
            <Tooltip formatter={(v) => typeof v === "number" ? v.toFixed(3) : String(v)} /><Line dataKey="observed_rate" name="Observed rate" stroke={AI} strokeWidth={2} dot={{ r: 4 }} isAnimationActive={false} /></LineChart></ResponsiveContainer></div> : <p className="empty-note">No calibrated probabilities.</p>}</section>
        <section className="panel progress-panel" aria-labelledby="strat-title"><h3 id="strat-title">Current MAE by derived trajectory type</h3>
          <div className="chart-surface"><ResponsiveContainer width="100%" height={220}><BarChart data={strat} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}><CartesianGrid stroke={CHART.grid} vertical={false} />
            <XAxis dataKey="type" tick={axisTick} stroke={CHART.axis} tickLine={false} /><YAxis tick={axisTick} width={34} axisLine={false} tickLine={false} /><Tooltip formatter={(v) => typeof v === "number" ? v.toFixed(2) : String(v)} /><Bar dataKey="mae" name="MAE" fill={GT} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} /></BarChart></ResponsiveContainer></div>
          <p className="small-note">{strat.map(s => `${s.type}: n=${s.n}`).join(" · ")}</p></section>
      </div>
      <section className="panel progress-panel" aria-labelledby="traj-title"><div className="panel-heading"><h3 id="traj-title">Per-trajectory ground truth vs AI estimate</h3>
        <select aria-label="Trajectory" value={traj} onChange={e => setTraj(e.target.value)}>{report.trajectories.map(t => <option key={t.run_id} value={t.run_id}>{t.run_id} · {t.type}</option>)}</select></div>
        {selected && <><div className="chart-legend"><span><svg width="26" height="10"><line x1="1" x2="25" y1="5" y2="5" stroke={GT} strokeWidth="2" /></svg>Ground truth PHQ-8</span><span><svg width="26" height="10"><line x1="1" x2="25" y1="5" y2="5" stroke={AI} strokeWidth="2" strokeDasharray="5 4" /></svg>AI estimate (insufficient items = 0)</span></div>
          <div className="chart-surface"><ResponsiveContainer width="100%" height={220}><LineChart data={selected.sessions} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}><CartesianGrid stroke={CHART.grid} vertical={false} />
            <XAxis dataKey="session_index" tickFormatter={v => `S${v}`} tick={axisTick} stroke={CHART.axis} tickLine={false} padding={{ left: 16, right: 16 }} /><YAxis domain={[0, 24]} ticks={[0, 5, 10, 15, 20, 24]} width={30} tick={axisTick} axisLine={false} tickLine={false} /><Tooltip />
            <Line dataKey="gt_total" name="Ground truth" stroke={GT} strokeWidth={2} dot={{ r: 4, fill: GT, stroke: CHART.surface, strokeWidth: 2 }} isAnimationActive={false} />
            <Line dataKey="ai_total" name="AI estimate" stroke={AI} strokeWidth={2} strokeDasharray="5 4" dot={{ r: 4, fill: AI, stroke: CHART.surface, strokeWidth: 2, strokeDasharray: "0" }} isAnimationActive={false} /></LineChart></ResponsiveContainer></div>
          <table className="progress-table"><thead><tr><th scope="col">Session</th><th>GT</th><th>AI</th><th>Rated items</th><th>Hypothesis</th><th>Cited findings</th></tr></thead>
            <tbody>{selected.sessions.map(s => <tr key={s.session_index}><th scope="row">S{s.session_index}</th><td>{f(s.gt_total, 1)}</td><td>{s.ai_total}</td><td>{s.rated_items}/8</td><td>{s.label ?? "failed"}</td>
              <td><details><summary>{s.findings.filter(x => x.status === "met").length} met</summary><ul>{s.findings.filter(x => x.evidence.length).map(x => <li key={x.criterion_id}><strong>{x.criterion_id}</strong> ({x.status}): {x.evidence.map(e => `${e.basis} [${e.segment_id}]`).join("; ")}</li>)}</ul>
                {s.rationale.length > 0 && <p className="small-note">{s.rationale.map(r => r.sentence).join(" ")}</p>}</details></td></tr>)}</tbody></table></>}
      </section>
      <section className="panel progress-panel"><h3>Notes</h3><ul>{report.notes.map((n, i) => <li key={i}>{n}</li>)}</ul><p className="small-note">Runtime: {JSON.stringify(report.runtime)}</p></section>
    </>}
  </main></WorkspaceShell>;
}

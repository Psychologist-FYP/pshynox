import { useCallback, useEffect, useState } from "react";
import { WorkspaceShell } from "../../components/layout/AppShell";
import { Icon } from "../../components/ui/Icon";
import { api, formatDate, type Health, type Patient } from "../../lib/api";
import { progressApi, type PatientProgress } from "./api";
import { HypothesesPanel } from "./HypothesesPanel";
import { InstrumentChart } from "./InstrumentChart";
import { ChangesList, DomainGrid, PlanThread, PredictionTimeline, RiskStatementsPanel, SessionComparison } from "./panels";
import { printProgress } from "./printProgress";
import { JobStatus, useProgressJob } from "./jobs";
import { DsmCriteriaGrid, DsmHypotheses, DsmTimeline, EarlierTracking, ReanalysisPanel } from "./Dsm";
import "./progress.css";

export function ProgressPage({ health, patientId }: { health: Health | null; patientId: string }) {
  const [patient, setPatient] = useState<Patient | null>(null);
  const [data, setData] = useState<PatientProgress | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => progressApi.progress(patientId).then(setData).catch(e => setError(e.message)), [patientId]);
  const job = useProgressJob(load);
  useEffect(() => { api.patient(patientId).then(setPatient).catch(e => setError(e.message)); void load(); }, [patientId, load]);
  const latest = data?.sessions.at(-1);
  const dsm = data?.registry_kind === "dsm" && data.dsm ? data.dsm : null;
  const hasV1 = !!data && (data.predictions.length > 0 || data.domains.some(d => d.cells.some(c => c.record_id)));
  const core = data?.instruments.filter(b => !b.clinician_only) ?? [];
  const extra = data?.instruments.filter(b => b.clinician_only) ?? [];
  const extraUsed = extra.filter(b => b.series.some(s => s.points.some(p => p.record_id)));
  const dates = data?.sessions.length ? `${formatDate(data.sessions[0].created_at)} – ${formatDate(data.sessions.at(-1)!.created_at)}` : "No sessions yet";
  async function runLatest() {
    if (!latest) return;
    if (!window.confirm("Run local analysis for the latest session? Confirm that you have reviewed its transcript (speaker roles and text).")) return;
    await job.start(() => progressApi.startExtraction(latest.id, { transcript_reviewed: true }));
  }
  async function runHypotheses() {
    if (!latest) return;
    await job.start(() => progressApi.runPredictions(patientId, { as_of_session_id: latest.id }));
  }
  return <WorkspaceShell health={health} active="Patient record" patientId={patientId}><main className="section-wrap progress-page">
    <a className="back-link" href={`/workspace/patients/${patientId}`}><Icon name="chevron" /> Back to {patient?.name ?? "patient"}</a>
    {error && <div className="state-panel state-error" role="alert">{error}</div>}
    {!data && !error && <div className="state-panel" role="status">Loading progress…</div>}
    {data && <>
      <header className="progress-header panel">
        <div><p className="eyebrow">Longitudinal progress</p><h2>{patient?.name ?? "Patient"}</h2>
          <p>{data.sessions.length} {data.sessions.length === 1 ? "session" : "sessions"} · {dates}</p>
          {data.sessions.some(s => s.research_demo) && <p className="demo-banner">Research demo patient (synthetic data). Not a real clinical record.</p>}</div>
        <div className="header-stats">
          <p><strong>{data.pending_review_counts.total ?? 0}</strong> values pending review <span className="small-note">({Object.entries(data.pending_review_counts).filter(([k, v]) => k !== "total" && v).map(([k, v]) => `${v} ${k.replaceAll("_", " ")}`).join(", ") || "none"})</span></p>
          <p className="small-note">Registries: {dsm ? `${dsm.registry_version} (DSM-5-TR criteria) · ` : ""}{Object.values(data.versions).join(" · ")}{dsm && <> · <a className="text-link" href="/workspace/registry">Review the DSM registry</a></>}</p>
          <div className="panel-tools"><button type="button" className="button button-small" disabled={!latest || job.running} onClick={() => void runLatest()}>Run analysis for latest session</button>
            <button type="button" className="text-button" onClick={() => printProgress(data, patient?.name ?? "Patient")}>Print / Save as PDF</button></div>
          <JobStatus job={job} />
        </div>
      </header>
      {data.sessions.length === 0 && <div className="state-panel" role="status">No sessions yet. Progress appears after the first session is recorded and analysed or entered manually.</div>}
      {data.sessions.length === 1 && <p className="notice small-note">One session so far: values are shown, but changes and trajectories need at least two (trajectories three) assessed sessions.</p>}
      {data.sessions.length === 2 && <p className="notice small-note">Two sessions: changes are shown; trajectory descriptors need three assessed sessions.</p>}
      {data.sessions.length > 0 && <>
        {dsm ? <DsmHypotheses data={data} onChanged={() => void load()} onRun={() => void runHypotheses()} running={job.running} />
          : <HypothesesPanel data={data} onChanged={() => void load()} onRun={() => void runHypotheses()} running={job.running} />}
        <RiskStatementsPanel data={data} onChanged={() => void load()} />
        <div className="instrument-grid">{core.map(b => <InstrumentChart key={b.instrument_key} block={b} sessions={data.sessions} changes={data.change_events} patientId={patientId} onSaved={() => void load()} />)}</div>
        {extra.length > 0 && <details className="panel progress-panel more-instruments" open={extraUsed.length > 0}><summary><strong>More questionnaires (clinician-administered)</strong> <span className="small-note">{extra.map(b => b.label).join(" · ")}</span></summary>
          <div className="instrument-grid">{extra.map(b => <InstrumentChart key={b.instrument_key} block={b} sessions={data.sessions} changes={data.change_events} patientId={patientId} onSaved={() => void load()} />)}</div></details>}
        {dsm ? <>
          <DsmCriteriaGrid data={data} onChanged={() => void load()} />
          <DsmTimeline data={data} />
          <ReanalysisPanel data={data} job={job} onChanged={() => void load()} />
          <EarlierTracking show={hasV1}>
            {data.predictions.length > 0 && <HypothesesPanel data={data} onChanged={() => void load()} />}
            <DomainGrid data={data} onChanged={() => void load()} />
            <PredictionTimeline data={data} />
          </EarlierTracking>
        </> : <>
          <DomainGrid data={data} onChanged={() => void load()} />
          <PredictionTimeline data={data} />
        </>}
        <ChangesList data={data} onChanged={() => void load()} />
        <SessionComparison data={data} />
        <PlanThread data={data} onChanged={() => void load()} />
      </>}
    </>}
  </main></WorkspaceShell>;
}

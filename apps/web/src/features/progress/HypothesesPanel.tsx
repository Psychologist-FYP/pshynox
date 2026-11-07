import { useState } from "react";
import { PREDICTION_LABEL, SUBTYPE_LABEL, progressApi, type PatientProgress, type Prediction, type PredictionLabel } from "./api";
import { Citations, ReviewActions, ReviewBadge, sessionLabelMap } from "./common";

const ORDER: PredictionLabel[] = ["insufficient_evidence", "unlikely", "possible", "likely"];
const STATUS_ICON: Record<string, string> = { met: "✓ Met", not_met: "✕ Not met", insufficient_evidence: "? Insufficient evidence", conflicting: "⇄ Conflicting" };

function trend(data: PatientProgress, p: Prediction): string {
  const line = data.prediction_timeline.find(t => t.condition_key === p.condition_key);
  const assessed = (line?.points ?? []).filter(x => x.record_id && !x.gap_reason);
  const i = assessed.findIndex(x => x.record_id === p.id);
  if (i <= 0) return "First hypothesis";
  const before = assessed[i - 1].prediction as PredictionLabel, now = p.prediction;
  const d = ORDER.indexOf(now) - ORDER.indexOf(before);
  return d > 0 ? `↑ from ${PREDICTION_LABEL[before]}` : d < 0 ? `↓ from ${PREDICTION_LABEL[before]}` : `→ unchanged (${PREDICTION_LABEL[before]})`;
}

export function HypothesesPanel({ data, onChanged, onRun, running }: { data: PatientProgress; onChanged: () => void; onRun?: () => void; running?: boolean }) {
  const labels = sessionLabelMap(data.sessions);
  const byCondition = new Map(data.predictions.map(p => [p.condition_key, p]));
  return <section className="panel progress-panel hypotheses-panel" aria-labelledby="hypotheses-title">
    <div className="panel-heading"><div><p className="eyebrow">AI hypotheses for clinician review</p><h3 id="hypotheses-title">Clinical hypotheses</h3>
      <p className="small-note">Each hypothesis is built from criterion findings that cite transcript turns. It is not a diagnosis. Criteria summaries are pending clinical consultant verification.</p></div>
      {onRun && <button type="button" className="button button-small" disabled={running || !data.sessions.length} onClick={onRun}>{running ? "Running…" : "Re-run hypotheses"}</button>}</div>
    <div className="hypothesis-grid">{data.prediction_timeline.map(t => {
      const p = byCondition.get(t.condition_key);
      return p ? <HypothesisCard key={t.condition_key} p={p} data={data} labels={labels} onChanged={onChanged} /> :
        <article key={t.condition_key} className="hypothesis-card is-empty"><h4>{t.label}</h4><p className="small-note">No hypothesis yet. Analyse a session to generate one.</p></article>;
    })}</div>
  </section>;
}

function HypothesisCard({ p, data, labels, onChanged }: { p: Prediction; data: PatientProgress; labels: Record<string, string>; onChanged: () => void }) {
  const [detail, setDetail] = useState<Prediction | null>(null);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState("");
  async function toggle() {
    if (!open && !detail) {
      try { setDetail(await progressApi.prediction(data.patient_id, p.id)); } catch (e) { setError(e instanceof Error ? e.message : "Could not load evidence."); }
    }
    setOpen(!open);
  }
  const shown = detail ?? p;
  return <article className={`hypothesis-card label-${p.prediction}`} aria-labelledby={`hyp-${p.id}`}>
    <div className="card-head"><h4 id={`hyp-${p.id}`}>{p.condition_label}</h4><ReviewBadge info={p} /></div>
    <p className="hypothesis-label"><strong>{PREDICTION_LABEL[p.prediction]}</strong>{p.subtype_key && <> · {SUBTYPE_LABEL[p.subtype_key] ?? p.subtype_key}</>}</p>
    <dl className="hypothesis-facts">
      <div><dt>Criteria met</dt><dd>{p.criteria_met_count} / {p.criteria_required} required</dd></div>
      <div><dt>Duration</dt><dd>{p.duration_status === "met" ? "Met" : p.duration_status === "not_met" ? "Not met" : "Unknown"}</dd></div>
      <div><dt>Impairment</dt><dd>{p.impairment_status === "met" ? "Met" : p.impairment_status === "not_met" ? "Not met" : "Unknown"}</dd></div>
      <div><dt>Probability</dt><dd>{p.probability != null ? p.probability.toFixed(2) : <span className="small-note">{p.probability_note ?? "Not calibrated"}</span>}</dd></div>
      <div><dt>Trend</dt><dd>{trend(data, p)}</dd></div>
      <div><dt>As of</dt><dd>{labels[p.as_of_session_id] ?? p.as_of_session_id}</dd></div>
    </dl>
    <button type="button" className="text-button" aria-expanded={open} onClick={() => void toggle()}>{open ? "Hide evidence" : "Why? Show criteria and evidence"}</button>
    {error && <p className="form-error">{error}</p>}
    {open && detail && <div className="hypothesis-detail">
      {shown.rationale.length > 0 && <><h5>Rationale</h5><ul>{shown.rationale.map((r, i) => <li key={i}>{r.sentence} <span className="small-note">[{r.segment_ids.map(id => { const c = shown.findings.flatMap(f => [...f.evidence, ...f.counter_evidence]).find(e => e.segment_id === id); return c ? `${labels[c.session_id] ?? ""} ${Math.floor(c.start_ms / 60000)}:${String(Math.floor(c.start_ms / 1000) % 60).padStart(2, "0")}` : id; }).join(", ")}]</span></li>)}</ul></>}
      <h5>Criteria</h5>
      <ol className="criteria-list">{shown.findings.map(f => <li key={f.id} className={`criterion status-${f.status}`}>
        <div className="criterion-head"><span className="criterion-status">{STATUS_ICON[f.status]}</span> <span>{f.summary || f.criterion_id}</span>{f.timeframe !== "unclear" && <span className="small-note"> · {f.timeframe}</span>}</div>
        {f.duration_note && <p className="small-note">Duration: {f.duration_note}</p>}
        {f.evidence.length > 0 && <Citations items={f.evidence} sessionLabels={labels} />}
        {f.counter_evidence.length > 0 && <><p className="small-note">Counter-evidence</p><Citations items={f.counter_evidence} sessionLabels={labels} /></>}
      </li>)}</ol>
      {shown.rule_outs.length > 0 && <><h5>Rule-outs</h5><ul>{shown.rule_outs.map((r, i) => <li key={i}>{(r.consideration ?? r.key ?? "").replaceAll("_", " ")}: {(r.status ?? "not assessed").replaceAll("_", " ")}</li>)}</ul></>}
      {shown.differentials.length > 0 && <><h5>Differentials</h5><ul>{shown.differentials.map((d, i) => <li key={i}><strong>{d.condition.replaceAll("_", " ")}</strong>{d.why_considered ? ` — ${d.why_considered}` : ""}{d.distinguishing_missing?.length ? <span className="small-note"> Missing: {d.distinguishing_missing.join("; ")}</span> : null}</li>)}</ul></>}
      {shown.missing_information.length > 0 && <><h5>Information still needed</h5><ul className="missing-list">{shown.missing_information.map((m, i) => <li key={i}>{m}</li>)}</ul></>}
      {(shown.postprocess.notes?.length || shown.postprocess.removed_sentences?.length) ? <details><summary>Checks applied in code</summary><ul>{shown.postprocess.notes?.map((n, i) => <li key={`n${i}`}>{n}</li>)}{shown.postprocess.removed_sentences?.map((n, i) => <li key={`r${i}`}>Removed uncited sentence: {n}</li>)}</ul></details> : null}
      {p.reviewer_comment && <p className="small-note">Reviewer comment: {p.reviewer_comment}</p>}
      <p className="small-note">Model {shown.model_version || "—"} · {shown.prompt_version || "—"} · {shown.registry_version || "—"}</p>
    </div>}
    <ReviewActions info={p} onReview={async r => { await progressApi.reviewPrediction(data.patient_id, p.id, r); setDetail(null); setOpen(false); onChanged(); }}
      editForm={submit => <LabelEdit p={p} submit={submit} />} />
  </article>;
}

function LabelEdit({ p, submit }: { p: Prediction; submit: (v: Record<string, unknown>, c?: string) => void }) {
  const [label, setLabel] = useState<PredictionLabel>(p.prediction);
  const [comment, setComment] = useState("");
  return <div className="domain-edit"><label>Label<select value={label} onChange={e => setLabel(e.target.value as PredictionLabel)}>{Object.entries(PREDICTION_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
    <label className="wide">Comment<input value={comment} onChange={e => setComment(e.target.value)} /></label>
    <button type="button" className="button button-small" onClick={() => submit({ prediction: label }, comment || undefined)}>Save edit</button></div>;
}

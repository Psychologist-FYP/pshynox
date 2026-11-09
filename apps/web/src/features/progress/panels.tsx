import { useEffect, useState } from "react";
import { formatDate } from "../../lib/api";
import {
  DOMAIN_STATUS_LABEL, PREDICTION_LABEL, RISK_LABEL, SUBTYPE_LABEL, progressApi,
  type ChangeEvent, type DomainObservation, type DomainRow, type PatientProgress, type PredictionLabel, type SeriesPoint,
} from "./api";
import { Citations, ReviewActions, ReviewBadge, badgeText, sessionLabelMap } from "./common";
import { RiskList, RiskReport } from "./RiskStatements";

const STATE_TEXT: Record<string, string> = { explicit_denial: "Denied", conflicting: "Conflict", insufficient_evidence: "Insufficient" };

export function cellText(c: SeriesPoint): { main: string; sub: string } {
  if (!c.record_id) return { main: "—", sub: "not assessed" };
  if (c.review_status === "stale") return { main: c.rating != null ? String(c.rating) : STATE_TEXT[c.status ?? ""] ?? "?", sub: "sources changed" };
  if (c.status === "rated") return { main: String(c.rating ?? "?"), sub: [c.timeframe === "past" ? "past episode" : "", c.direction ?? "", c.frequency_text ?? ""].filter(Boolean).join(" · ") };
  return { main: STATE_TEXT[c.status ?? ""] ?? "?", sub: "" };
}

export function DomainGrid({ data, onChanged }: { data: PatientProgress; onChanged: () => void }) {
  const [open, setOpen] = useState<{ sessionId: string; domainKey: string } | null>(null);
  if (!data.sessions.length) return null;
  return <section className="panel progress-panel" aria-labelledby="domain-grid-title">
    <div className="panel-heading"><div><p className="eyebrow">Session observations</p><h3 id="domain-grid-title">Symptom and functioning domains</h3>
      <p className="small-note">Ratings 0–3 per session. AI values count immediately; their badge shows review status. “—” means not assessed (a gap, not zero).</p></div></div>
    <div className="heatmap-key" aria-hidden="true"><span>Rating:</span>{[["#eef5fd", "0 not present"], ["#b7d3f6", "1 mild"], ["#6da7ec", "2 moderate"], ["#1c5cab", "3 severe"]].map(([c, l]) => <span key={l}><i style={{ background: c }} />{l}</span>)}
      <span><i style={{ background: "#fff" }} />insufficient evidence</span><span><i style={{ background: "#fff", borderStyle: "dashed" }} />not assessed</span></div>
    <div className="table-scroll chart-surface"><table className="domain-grid"><caption className="sr-only">Domain ratings by session</caption>
      <thead><tr><th scope="col">Domain</th>{data.sessions.map(s => <th scope="col" key={s.id}>S{s.index}<span className="small-note"> {formatDate(s.created_at)}</span></th>)}</tr></thead>
      {data.domain_groups.map(g => <tbody key={g.key}><tr className="group-row"><th colSpan={data.sessions.length + 1} scope="rowgroup">{g.label}</th></tr>
        {data.domains.filter(d => d.group === g.key).map(d => <DomainLine key={d.domain_key} row={d} sessions={data.sessions.map(s => s.id)} onOpen={sid => setOpen({ sessionId: sid, domainKey: d.domain_key })} />)}</tbody>)}
    </table></div>
    {open && <EvidenceDrawer {...open} label={data.domains.find(d => d.domain_key === open.domainKey)?.label ?? open.domainKey} sessionLabels={sessionLabelMap(data.sessions)} onClose={() => setOpen(null)} onChanged={onChanged} />}
  </section>;
}

function DomainLine({ row, sessions, onOpen }: { row: DomainRow; sessions: string[]; onOpen: (sid: string) => void }) {
  return <tr><th scope="row">{row.label}</th>{row.cells.map((c, i) => {
    const t = cellText(c);
    const tone = !c.record_id ? "empty" : c.review_status === "stale" ? "stale" : c.review_status === "rejected" ? "rejected" : c.status === "rated" ? `rating-${c.rating}` : c.status ?? "";
    return <td key={sessions[i]} className={`domain-cell cell-${tone}`}>
      <button type="button" className="cell-button" onClick={() => onOpen(sessions[i])} aria-label={`${row.label}, session ${i + 1}: ${t.main} ${t.sub} ${c.provenance ? badgeText({ provenance: c.provenance, review_status: c.review_status ?? "pending", reviewer_name: c.reviewer_name }).label : ""}`}>
        <span className="cell-main">{t.main}</span>{t.sub && <span className="cell-sub">{t.sub}</span>}
        {c.provenance && <span className={`cell-badge badge-${c.provenance === "MANUAL" ? "reviewed" : c.review_status}`} aria-hidden="true">{c.provenance === "MANUAL" ? "✓" : c.review_status === "pending" ? "AI" : c.review_status === "accepted" ? "✓" : c.review_status === "edited" ? "✎" : c.review_status === "rejected" ? "✕" : "!"}</span>}
      </button></td>;
  })}</tr>;
}

export function DomainEditFields({ initial, directions, submit, submitLabel = "Save edit" }: { initial: Partial<DomainObservation>; directions: string[]; submit: (value: Record<string, unknown>, comment?: string) => void; submitLabel?: string }) {
  const [status, setStatus] = useState(initial.status_value ?? "rated");
  const [rating, setRating] = useState(initial.rating ?? 1);
  const [direction, setDirection] = useState(initial.direction ?? "");
  const [timeframe, setTimeframe] = useState(initial.timeframe ?? "current");
  const [basis, setBasis] = useState(initial.basis ?? "");
  const [comment, setComment] = useState("");
  return <div className="domain-edit">
    <label>Status<select value={status} onChange={e => setStatus(e.target.value as DomainObservation["status_value"])}>{Object.entries(DOMAIN_STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
    {status === "rated" && <label>Rating<select value={rating} onChange={e => setRating(Number(e.target.value))}>{[0, 1, 2, 3].map(v => <option key={v} value={v}>{v}</option>)}</select></label>}
    {directions.length > 0 && <label>Direction<select value={direction} onChange={e => setDirection(e.target.value)}><option value="">—</option>{directions.map(d => <option key={d} value={d}>{d.replaceAll("_", " ")}</option>)}</select></label>}
    <label>Timeframe<select value={timeframe} onChange={e => setTimeframe(e.target.value as "current" | "past" | "unclear")}><option value="current">current</option><option value="past">past</option><option value="unclear">unclear</option></select></label>
    <label className="wide">Basis<input value={basis} onChange={e => setBasis(e.target.value)} maxLength={600} /></label>
    <label className="wide">Comment<input value={comment} onChange={e => setComment(e.target.value)} /></label>
    <button type="button" className="button button-small" onClick={() => submit({ status_value: status, rating: status === "rated" ? rating : null, direction: direction || null, timeframe, basis }, comment || undefined)}>{submitLabel}</button>
  </div>;
}

export function EvidenceDrawer({ sessionId, domainKey, label, sessionLabels, onClose, onChanged }: { sessionId: string; domainKey: string; label: string; sessionLabels: Record<string, string>; onClose: () => void; onChanged: () => void }) {
  const [obs, setObs] = useState<DomainObservation | null | undefined>(undefined);
  const [error, setError] = useState("");
  const [directions, setDirections] = useState<string[]>([]);
  const load = () => progressApi.observations(sessionId).then(rows => setObs(rows.find(r => r.domain_key === domainKey) ?? null)).catch(e => setError(e.message));
  useEffect(() => { void load(); progressApi.registry().then(r => setDirections(r.domains.domains.find(d => d.key === domainKey)?.directions ?? [])).catch(() => {}); }, [sessionId, domainKey]); // eslint-disable-line react-hooks/exhaustive-deps
  return <aside className="evidence-drawer" role="dialog" aria-label={`${label} evidence`}>
    <div className="panel-heading"><div><p className="eyebrow">{sessionLabels[sessionId] ?? sessionId}</p><h4>{label}</h4></div><button type="button" className="text-button" onClick={onClose}>Close</button></div>
    {error && <p className="form-error">{error}</p>}
    {obs === null && <p className="small-note">Not assessed in this session. Use the session's Observations panel to add a value.</p>}
    {obs && <>
      <p><ReviewBadge info={obs} /> <strong>{obs.status_value === "rated" ? `Rating ${obs.rating}` : DOMAIN_STATUS_LABEL[obs.status_value]}</strong>{obs.direction ? ` · ${obs.direction.replaceAll("_", " ")}` : ""}{obs.timeframe !== "current" ? ` · ${obs.timeframe}` : ""}</p>
      {[obs.frequency_text, obs.intensity_text, obs.duration_text].some(Boolean) && <p className="small-note">{[obs.frequency_text && `Frequency: ${obs.frequency_text}`, obs.intensity_text && `Intensity: ${obs.intensity_text}`, obs.duration_text && `Duration: ${obs.duration_text}`].filter(Boolean).join(" · ")}</p>}
      {obs.basis && <p>{obs.basis}</p>}
      <Citations items={obs.evidence} sessionLabels={sessionLabels} />
      <ReviewActions info={obs} onReview={async p => { await progressApi.reviewObservation(sessionId, obs.id, p); await load(); onChanged(); }}
        editForm={submit => <DomainEditFields initial={obs} directions={directions} submit={submit} />} />
      {obs.history.length > 0 && <details><summary>Review history ({obs.history.length})</summary><ol className="history-list">{obs.history.map(h => <li key={String(h.id)}>{String(h.actor)} · {String(h.created_at).slice(0, 16).replace("T", " ")} · was {String(h.status_value)}{h.rating != null ? ` ${String(h.rating)}` : ""} ({String(h.review_status)})</li>)}</ol></details>}
      {obs.alternatives.length > 0 && <details open><summary>New AI suggestion not applied ({obs.alternatives.length})</summary><ul>{obs.alternatives.map(a => <li key={String(a.id)}>{String(a.status_value)} {a.rating != null ? String(a.rating) : ""} — {String(a.basis ?? "")}</li>)}</ul></details>}
      {obs.original_ai && obs.review_status === "edited" && <p className="small-note">Original AI output retained: {JSON.stringify(obs.original_ai)}</p>}
    </>}
  </aside>;
}

export function RiskStatementsPanel({ data, onChanged }: { data: PatientProgress; onChanged: () => void }) {
  const visible = data.risk_statements.filter(r => r.review_status !== "rejected");
  const rejected = data.risk_statements.filter(r => r.review_status === "rejected");
  return <RiskReport id="risk-title" title="Flagged statements: harm to self or others" risks={visible} rejected={rejected}>
    <RiskList risks={visible} sessionLabels={sessionLabelMap(data.sessions)}
      onReview={async (r, p) => { await progressApi.reviewRisk(data.patient_id, r.id, p); onChanged(); }}
      editForm={r => submit => <RiskEdit initial={r.category} basis={r.basis} submit={submit} />} />
  </RiskReport>;
}

export function RiskEdit({ initial, basis, submit }: { initial: string; basis: string; submit: (v: Record<string, unknown>, c?: string) => void }) {
  const [category, setCategory] = useState(initial);
  const [text, setText] = useState(basis);
  return <div className="domain-edit"><label>Category<select value={category} onChange={e => setCategory(e.target.value)}>{Object.entries(RISK_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
    <label className="wide">Basis<input value={text} onChange={e => setText(e.target.value)} /></label><button type="button" className="button button-small" onClick={() => submit({ category, basis: text })}>Save edit</button></div>;
}

const RULE_TEXT: Record<string, string> = {
  "instrument.reliable_change": "Reliable change", "domain.rating_jump": "Rating changed by ≥ 2", "domain.emergence": "Newly rated after insufficient evidence",
  "prediction.label_change": "Hypothesis label changed", "prediction.probability_shift": "Probability shifted ≥ 0.2",
};

export function ChangesList({ data, onChanged }: { data: PatientProgress; onChanged: () => void }) {
  const labels = sessionLabelMap(data.sessions);
  const [actor, setActor] = useState("");
  const [error, setError] = useState("");
  const domainLabel = Object.fromEntries(data.domains.map(d => [d.domain_key, d.label]));
  const name = (c: ChangeEvent) => c.measure_kind === "domain" ? domainLabel[c.measure_key] ?? c.measure_key : c.measure_kind === "prediction" ? data.prediction_timeline.find(t => t.condition_key === c.measure_key)?.label ?? c.measure_key : c.measure_key.replace(":phq8", " (PHQ-8)").toUpperCase();
  return <section className="panel progress-panel" aria-labelledby="changes-title">
    <div className="panel-heading"><div><p className="eyebrow">Detected changes</p><h3 id="changes-title">Changes between sessions</h3></div>
      <label className="inline-field">Acknowledge as<input value={actor} onChange={e => setActor(e.target.value)} placeholder="Your name" /></label></div>
    {!data.change_events.length && <p className="empty-note">No changes detected under the configured rules.</p>}
    {error && <p className="form-error">{error}</p>}
    <table className="progress-table"><thead><tr><th scope="col">Measure</th><th scope="col">Change</th><th scope="col">Sessions</th><th scope="col">Inputs</th><th scope="col">Status</th></tr></thead>
      <tbody>{data.change_events.map(c => <tr key={c.id}><td>{name(c)}{c.series !== "domain" && c.series !== "prediction" ? <span className="small-note"> · {c.series === "ai_estimate" ? "AI estimate" : "administered"}</span> : null}</td>
        <td>{RULE_TEXT[c.rule_id] ?? c.rule_id}{c.delta != null && c.rule_id !== "prediction.probability_shift" ? ` (${c.delta > 0 ? "+" : ""}${c.delta})` : ""}{c.from_label && c.to_label ? ` ${c.from_label.replace(/:$/, "")} → ${c.to_label.replace(/:$/, "")}` : ""}</td>
        <td>{labels[c.from_session_id] ?? c.from_session_id} → {labels[c.to_session_id] ?? c.to_session_id}</td>
        <td><span className={`inputs-tag inputs-${c.provenance_of_inputs}`}>{c.provenance_of_inputs === "ai" ? "AI values (review pending)" : c.provenance_of_inputs === "reviewed" ? "Reviewed values" : "Mixed AI and reviewed"}</span></td>
        <td>{c.acknowledged_at ? `Acknowledged by ${c.acknowledged_by}` : <button type="button" className="text-button" disabled={!actor.trim()} onClick={async () => { try { await progressApi.acknowledge(data.patient_id, c.id, { actor: actor.trim() }); onChanged(); } catch (e) { setError(e instanceof Error ? e.message : "Failed"); } }}>Acknowledge</button>}</td></tr>)}</tbody></table>
  </section>;
}

export function PredictionTimeline({ data }: { data: PatientProgress }) {
  if (!data.prediction_timeline.some(t => t.points.some(p => p.record_id))) return null;
  return <section className="panel progress-panel" aria-labelledby="timeline-title"><div className="panel-heading"><div><p className="eyebrow">Hypotheses over time</p><h3 id="timeline-title">Prediction timeline</h3></div></div>
    <div className="table-scroll"><table className="progress-table timeline-table"><thead><tr><th scope="col">Condition</th>{data.sessions.map(s => <th key={s.id} scope="col">As of S{s.index}</th>)}</tr></thead>
      <tbody>{data.prediction_timeline.map(t => <tr key={t.condition_key}><th scope="row">{t.label}</th>{t.points.map(p => <td key={p.slot} className={`timeline-cell label-${p.prediction ?? "none"}`}>
        {p.record_id ? <><strong>{PREDICTION_LABEL[(p.prediction ?? "insufficient_evidence") as PredictionLabel]}</strong>{p.subtype_key && <span className="cell-sub">{SUBTYPE_LABEL[p.subtype_key] ?? p.subtype_key}</span>}{p.probability != null && <span className="cell-sub">p = {p.probability.toFixed(2)}</span>}
          <span className="cell-sub">{badgeText({ provenance: p.provenance ?? "AI_GENERATED", review_status: p.review_status ?? "pending", reviewer_name: p.reviewer_name }).label}</span>{p.gap_reason && <span className="cell-sub">excluded ({p.gap_reason})</span>}</> : <span className="gap-mark">—</span>}</td>)}</tr>)}</tbody></table></div></section>;
}

export function SessionComparison({ data }: { data: PatientProgress }) {
  const [a, setA] = useState(data.sessions.at(-2)?.id ?? "");
  const [b, setB] = useState(data.sessions.at(-1)?.id ?? "");
  if (data.sessions.length < 2) return null;
  const idx = (sid: string) => data.sessions.findIndex(s => s.id === sid);
  const column = (sid: string) => {
    const i = idx(sid);
    return { domains: data.domains.map(d => ({ label: d.label, cell: d.cells[i] })), scores: data.instruments.map(ins => ({ label: ins.metric_label, series: ins.series.map(s => ({ name: s.series === "ai_estimate" ? "AI estimate" : "Administered", p: s.points.find(p => p.slot === sid) })) })),
      preds: data.prediction_timeline.map(t => ({ label: t.label, p: t.points[i] })) };
  };
  const left = column(a), right = column(b);
  return <section className="panel progress-panel" aria-labelledby="compare-title"><div className="panel-heading"><div><p className="eyebrow">Side by side</p><h3 id="compare-title">Session comparison</h3></div>
    <div className="panel-tools"><select aria-label="First session" value={a} onChange={e => setA(e.target.value)}>{data.sessions.map(s => <option key={s.id} value={s.id}>Session {s.index}</option>)}</select><select aria-label="Second session" value={b} onChange={e => setB(e.target.value)}>{data.sessions.map(s => <option key={s.id} value={s.id}>Session {s.index}</option>)}</select></div></div>
    <div className="table-scroll"><table className="progress-table"><thead><tr><th scope="col">Measure</th><th scope="col">Session {idx(a) + 1}</th><th scope="col">Session {idx(b) + 1}</th></tr></thead><tbody>
      {left.scores.map((s, i) => s.series.map((x, j) => <tr key={`${s.label}-${x.name}`}><th scope="row">{s.label} · {x.name}</th><td>{x.p?.value ?? "—"}</td><td>{right.scores[i].series[j].p?.value ?? "—"}</td></tr>))}
      {left.preds.map((p, i) => <tr key={p.label}><th scope="row">{p.label}</th><td>{p.p?.prediction ? PREDICTION_LABEL[p.p.prediction as PredictionLabel] : "—"}</td><td>{right.preds[i].p?.prediction ? PREDICTION_LABEL[right.preds[i].p.prediction as PredictionLabel] : "—"}</td></tr>)}
      {left.domains.map((d, i) => <tr key={d.label}><th scope="row">{d.label}</th><td>{d.cell ? cellText(d.cell).main : "—"}</td><td>{right.domains[i].cell ? cellText(right.domains[i].cell).main : "—"}</td></tr>)}
    </tbody></table></div>
    <p className="small-note"><a className="text-link" href={`/workspace/sessions/${a}`}>Open session {idx(a) + 1} note</a> · <a className="text-link" href={`/workspace/sessions/${b}`}>Open session {idx(b) + 1} note</a></p></section>;
}

const PLAN_STATUS: Record<string, string> = { not_reviewed: "Not reviewed", discussed_done: "Discussed — done", discussed_partial: "Discussed — partly done", discussed_not_done: "Discussed — not done", not_discussed: "Not discussed" };

export function PlanThread({ data, onChanged }: { data: PatientProgress; onChanged: () => void }) {
  const labels = sessionLabelMap(data.sessions);
  return <section className="panel progress-panel" aria-labelledby="plans-title"><div className="panel-heading"><div><p className="eyebrow">Treatment process</p><h3 id="plans-title">Plans and follow-up</h3></div></div>
    {!data.plan_followups.length && <p className="empty-note">No plan follow-ups yet. They appear after a follow-up session is analysed against the previous session's plan.</p>}
    <ol className="card-list plan-thread">{data.plan_followups.map(p => <li key={p.id} className="plan-card"><div className="card-head"><strong>{labels[p.origin_session_id]} plan:</strong> {p.plan_text}</div>
      <p>{labels[p.followup_session_id]}: <strong>{PLAN_STATUS[p.status] ?? p.status}</strong> <ReviewBadge info={p} /></p>{p.basis && <p className="small-note">{p.basis}</p>}
      <Citations items={p.evidence} sessionLabels={labels} empty="No cited turns in the follow-up session." />
      <ReviewActions info={p} onReview={async r => { await progressApi.reviewPlan(data.patient_id, p.id, r); onChanged(); }}
        editForm={submit => <PlanEdit status={p.status} submit={submit} />} /></li>)}</ol></section>;
}

function PlanEdit({ status, submit }: { status: string; submit: (v: Record<string, unknown>) => void }) {
  const [value, setValue] = useState(status);
  return <div className="domain-edit"><label>Status<select value={value} onChange={e => setValue(e.target.value)}>{Object.entries(PLAN_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label><button type="button" className="button button-small" onClick={() => submit({ status: value })}>Save edit</button></div>;
}

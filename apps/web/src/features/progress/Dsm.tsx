import { useCallback, useEffect, useState, type ReactNode } from "react";
import { formatDate } from "../../lib/api";
import {
  DSM_LABEL, DSM_STATUS, progressApi,
  type DsmCell, type DsmElement, type DsmFinding, type DsmGridRow, type DsmPrediction, type DsmRegistryView, type DsmStatus, type PatientProgress,
} from "./api";
import { Citations, ReviewActions, ReviewBadge, badgeText, sessionLabelMap } from "./common";
import type { JobHandle } from "./jobs";

const ELEMENT_MARK: Record<DsmElement["status"], string> = { met: "✓", not_met: "✕", unknown: "?", partial: "~", contradicted: "!", info: "·" };
const ELEMENT_TEXT: Record<DsmElement["status"], string> = { met: "met", not_met: "not met", unknown: "not established", partial: "partly met", contradicted: "contradicted by cited evidence", info: "note" };
const TARGETS = ["mdd", "bipolar_i", "bipolar_ii", "gad", "sad"];
const EPISODES = ["mde", "manic", "hypomanic"];
const DIFFERENTIALS = ["pdd", "cyclothymic"];
const STATUS_ORDER: DsmStatus[] = ["present", "subthreshold", "absent", "explicit_denial", "insufficient_evidence", "conflicting"];
export const VERIFICATION_TEXT: Record<string, string> = { pending: "Paraphrased from DSM-5-TR · pending clinical verification", verified: "Paraphrased from DSM-5-TR · verified by clinician", changed: "Paraphrased from DSM-5-TR · change requested" };

export function VerificationBadge({ status, compact = false }: { status: string; compact?: boolean }) {
  return <span className={`verify-badge verify-${status}`} title={VERIFICATION_TEXT[status] ?? status}>{compact ? (status === "verified" ? "Verified" : status === "changed" ? "Change requested" : "Pending verification") : VERIFICATION_TEXT[status] ?? status}</span>;
}

function periodText(p: DsmPrediction["period"]) {
  if (!p?.label) return "";
  const days = p.duration_days != null ? ` · about ${p.duration_days} days cited` : "";
  return `${p.label}${days}${p.hospitalised ? " · hospitalised" : ""}`;
}

/** Hypothesis cards: the DSM rule as a checklist. Card → checklist line → cited turn (▶ plays, link opens the transcript). */
export function DsmHypotheses({ data, onChanged, onRun, running }: { data: PatientProgress; onChanged: () => void; onRun?: () => void; running?: boolean }) {
  const dsm = data.dsm!;
  const labels = sessionLabelMap(data.sessions);
  const by = new Map(dsm.predictions.map(p => [p.condition_key, p]));
  const name = (key: string) => dsm.timeline.find(t => t.condition_key === key)?.label ?? key;
  const card = (key: string) => by.has(key) ? <DsmCard key={key} p={by.get(key)!} data={data} labels={labels} onChanged={onChanged} />
    : <article key={key} className="hypothesis-card is-empty" aria-label={name(key)}><h4>{name(key)}</h4><p className="small-note">No hypothesis yet. Analyse a session to generate one.</p></article>;
  return <section className="panel progress-panel hypotheses-panel" aria-labelledby="dsm-hyp-title">
    <div className="panel-heading"><div><p className="eyebrow">AI hypotheses for clinician review · DSM-5-TR criteria</p><h3 id="dsm-hyp-title">Clinical hypotheses</h3>
      <p className="small-note">Code computes each label from criterion findings that cite transcript turns. {dsm.label_footer} Criteria are {VERIFICATION_TEXT.pending.toLowerCase().replace("paraphrased from dsm-5-tr · ", "paraphrased from DSM-5-TR and ")}.</p></div>
      {onRun && <button type="button" className="button button-small" disabled={running || !data.sessions.length} onClick={onRun}>{running ? "Running…" : "Re-run hypotheses"}</button>}</div>
    <div className="hypothesis-grid">{TARGETS.map(card)}</div>
    <details className="dsm-more"><summary>Episode patterns ({EPISODES.filter(k => by.has(k)).length}) and differentials ({DIFFERENTIALS.filter(k => by.has(k)).length})</summary>
      <h4>Episodes</h4><div className="hypothesis-grid">{EPISODES.map(card)}</div>
      <h4>Differentials</h4><div className="hypothesis-grid">{DIFFERENTIALS.map(card)}</div></details>
  </section>;
}

function DsmCard({ p, data, labels, onChanged }: { p: DsmPrediction; data: PatientProgress; labels: Record<string, string>; onChanged: () => void }) {
  const [open, setOpen] = useState<string | null>(null);
  return <article className={`hypothesis-card dsm-card label-${p.label}`} aria-label={p.condition_label}>
    <div className="card-head"><h4>{p.condition_label}</h4><ReviewBadge info={p} /></div>
    <p className="hypothesis-label"><strong>{DSM_LABEL[p.label]}</strong></p>
    <p className="small-note dsm-label-text">{p.label_text}</p>
    <dl className="hypothesis-facts">
      <div><dt>Required criteria</dt><dd>{p.criteria_met_count} of {p.criteria_required} met</dd></div>
      <div><dt>As of</dt><dd>{labels[p.as_of_session_id] ?? p.as_of_session_id}</dd></div>
      {p.period?.label && <div><dt>Period</dt><dd>{periodText(p.period)}</dd></div>}
      <div><dt>Codes (reference)</dt><dd>ICD-10-CM {p.codes.icd10cm} · ICD-11 {p.codes.icd11}</dd></div>
    </dl>
    <ul className="dsm-checklist" aria-label={`${p.condition_label} criteria checklist`}>{p.elements.map(e => <li key={e.id} className={`dsm-line line-${e.status}`}>
      <button type="button" className="dsm-line-button" aria-expanded={open === e.id} onClick={() => setOpen(open === e.id ? null : e.id)}>
        <span className="dsm-mark" aria-hidden="true">{ELEMENT_MARK[e.status]}</span><span>{e.line.replace(/ [✓✕?~!·]$/, "")}</span><span className="sr-only"> ({ELEMENT_TEXT[e.status]})</span>
      </button>
      {open === e.id && <div className="dsm-line-detail">
        {e.items.length > 0 && <ul className="dsm-items">{e.items.map(r => <li key={r.item}><strong>{r.item}</strong> <span className={`dsm-status status-${r.status}`}>{DSM_STATUS[r.status]?.icon} {DSM_STATUS[r.status]?.text ?? r.status}</span>{r.frequency ? <span className="small-note"> · {r.frequency}</span> : null}{r.note && <span className="small-note"> · {r.note}</span>}{r.paraphrase && <p className="small-note">{r.paraphrase}</p>}</li>)}</ul>}
        <Citations items={e.evidence} sessionLabels={labels} empty="No cited evidence for this line." />
        {e.counter_evidence.length > 0 && <><p className="small-note"><strong>Counter-evidence</strong> (denials and alternative explanations)</p>
          <Citations items={e.counter_evidence.map(c => ({ ...c, basis: `${c.kind === "denial" ? "Client denial" : "Alternative explanation"}${c.basis ? `: ${c.basis}` : ""}` }))} sessionLabels={labels} /></>}
        {e.ask && <p className="small-note">To ask: {e.ask}</p>}
      </div>}
    </li>)}</ul>
    {p.specifiers.length > 0 && <><h5>Specifiers (suggested)</h5><ul>{p.specifiers.map(s => <li key={s.key}><strong>{s.label}</strong> <span className="small-note">{s.basis}{s.code ? ` · ${s.code}` : ""}</span></li>)}</ul></>}
    {p.missing_information.length > 0 && <><h5>Information still needed</h5><ul className="missing-list">{p.missing_information.map((m, i) => <li key={i}>{m}</li>)}</ul></>}
    {p.differentials.length > 0 && <details><summary>Differentials ({p.differentials.length})</summary><ul>{p.differentials.map(d => <li key={d.label}><strong>{d.label}</strong>: {d.why}</li>)}</ul></details>}
    <details><summary>Checks applied in code</summary><ul>
      <li>The model's own reading was {p.postprocess.model_label ? <strong>{DSM_LABEL[p.postprocess.model_label as keyof typeof DSM_LABEL] ?? p.postprocess.model_label}</strong> : "not given"}; it is logged for comparison and never used.</li>
      {(p.postprocess.notes ?? []).map((n, i) => <li key={i}>{n}</li>)}</ul>
      <p className="small-note">Model {p.model_version || "—"} · {p.prompt_version} · {p.registry_version}</p></details>
    <p className="dsm-footer">{p.footer}</p>
    {p.reviewer_comment && <p className="small-note">Reviewer comment: {p.reviewer_comment}</p>}
    <ReviewActions info={p} onReview={async r => { await progressApi.reviewDsmPrediction(data.patient_id, p.id, r); onChanged(); }}
      editForm={submit => <DsmLabelEdit p={p} submit={submit} />} />
  </article>;
}

function DsmLabelEdit({ p, submit }: { p: DsmPrediction; submit: (v: Record<string, unknown>, c?: string) => void }) {
  const [label, setLabel] = useState(p.label);
  const [comment, setComment] = useState("");
  return <div className="domain-edit"><label>Label<select value={label} onChange={e => setLabel(e.target.value as DsmPrediction["label"])}>{Object.entries(DSM_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
    <label className="wide">Comment<input value={comment} onChange={e => setComment(e.target.value)} /></label>
    <button type="button" className="button button-small" onClick={() => submit({ label }, comment || undefined)}>Save edit</button></div>;
}

function cellBadge(c: DsmCell) {
  if (!c.provenance) return null;
  const mark = c.provenance === "MANUAL" ? "✓" : c.review_status === "pending" ? "AI" : c.review_status === "accepted" ? "✓" : c.review_status === "edited" ? "✎" : c.review_status === "rejected" ? "✕" : "!";
  return <span className={`cell-badge badge-${c.provenance === "MANUAL" ? "reviewed" : c.review_status}`} aria-hidden="true">{mark}</span>;
}

function StatusCell({ c, label, index, onOpen }: { c: DsmCell; label: string; index: number; onOpen: () => void }) {
  const s = c.status ? DSM_STATUS[c.status] : null;
  const review = c.provenance ? badgeText({ provenance: c.provenance, review_status: c.review_status ?? "pending", reviewer_name: c.reviewer_name }).label : "";
  return <td className={`dsm-cell cell-${c.status ?? "empty"}`}>
    <button type="button" className="cell-button" onClick={onOpen} aria-label={`${label}, session ${index}: ${s ? s.text : "not assessed"}${c.frequency ? `, ${c.frequency}` : ""}${review ? `, ${review}` : ""}`}>
      <span className="cell-main">{s ? <><span aria-hidden="true">{s.icon} </span>{s.text}</> : "—"}</span>
      {c.frequency && c.status === "present" && <span className="cell-sub">{c.frequency}</span>}
      {c.note && c.status !== "present" && c.status !== null && <span className="cell-sub">{c.note.slice(0, 40)}</span>}
      {cellBadge(c)}
    </button></td>;
}

/** Criteria grid: condition → criterion letter → item, or shared findings once. Missing sessions are gaps ("—"). */
export function DsmCriteriaGrid({ data, onChanged }: { data: PatientProgress; onChanged: () => void }) {
  const dsm = data.dsm!;
  const [mode, setMode] = useState<"condition" | "shared">("condition");
  const [open, setOpen] = useState<{ sessionId: string; features: string[]; title: string } | null>(null);
  const labels = sessionLabelMap(data.sessions);
  const head = <thead><tr><th scope="col">{mode === "condition" ? "DSM criterion" : "Finding (feature)"}</th>{data.sessions.map(s => <th scope="col" key={s.id}>S{s.index}<span className="small-note"> {formatDate(s.created_at)}</span></th>)}</tr></thead>;
  const itemRow = (r: DsmGridRow) => <tr key={r.id}><th scope="row" className="dsm-row-head">
    <span className="dsm-id">{r.id}</span> {r.label}<VerificationBadge status={r.verification} compact /><span className="dsm-paraphrase">{r.paraphrase}</span>
    {!r.model_assessed && <span className="small-note"> Clinician sources only (accepted risk statement or PHQ-9 item 9).</span>}</th>
    {r.cells.map((c, i) => <StatusCell key={c.session_id} c={c} label={r.id} index={i + 1} onOpen={() => setOpen({ sessionId: c.session_id, features: r.features, title: `${r.id} ${r.label}` })} />)}</tr>;
  return <section className="panel progress-panel" aria-labelledby="dsm-grid-title">
    <div className="panel-heading"><div><p className="eyebrow">Session findings · DSM-5-TR criteria</p><h3 id="dsm-grid-title">Criteria grid</h3>
      <p className="small-note">Each row is a DSM-5-TR criterion item ({dsm.verification_note.toLowerCase()}). AI values count immediately; the corner mark shows review status. “—” means not assessed in that session: a gap, never “absent”.</p></div>
      <div className="segmented" role="group" aria-label="Grid grouping">
        <button type="button" aria-pressed={mode === "condition"} className={mode === "condition" ? "is-active" : ""} onClick={() => setMode("condition")}>By condition</button>
        <button type="button" aria-pressed={mode === "shared"} className={mode === "shared" ? "is-active" : ""} onClick={() => setMode("shared")}>Shared findings once</button></div></div>
    <div className="heatmap-key" aria-hidden="true">{STATUS_ORDER.map(s => <span key={s} className={`dsm-key cell-${s}`}>{DSM_STATUS[s].icon} {DSM_STATUS[s].text}</span>)}<span className="dsm-key cell-not_linked">{DSM_STATUS.not_linked.icon} Present but not linked (for example tiredness from low mood, not worry)</span><span className="dsm-key cell-stale">! Sources changed</span></div>
    <div className="table-scroll chart-surface"><table className="domain-grid dsm-grid"><caption className="sr-only">DSM criterion status by session</caption>{head}
      {mode === "condition" ? dsm.grid.map(g => <tbody key={g.key}><tr className="group-row"><th colSpan={data.sessions.length + 1} scope="rowgroup">{g.label}</th></tr>
        {g.rows.map((r, i) => <RowWithLetter key={r.id} row={r} previous={g.rows[i - 1]} colSpan={data.sessions.length + 1}>{itemRow(r)}</RowWithLetter>)}</tbody>)
        : dsm.feature_groups.map(g => <tbody key={g.key}><tr className="group-row"><th colSpan={data.sessions.length + 1} scope="rowgroup">{g.label}</th></tr>
          {dsm.features.filter(f => f.group === g.key).map(f => <tr key={f.key}><th scope="row" className="dsm-row-head">{f.label}<span className="dsm-paraphrase">Counts for {f.linked_items.join(", ") || "exclusions"}</span></th>
            {f.cells.map((c, i) => <StatusCell key={c.session_id} c={c} label={f.label} index={i + 1} onOpen={() => setOpen({ sessionId: c.session_id, features: [f.key], title: f.label })} />)}</tr>)}</tbody>)}
    </table></div>
    {open && <FindingDrawer {...open} sessionLabels={labels} onClose={() => setOpen(null)} onChanged={onChanged} />}
  </section>;
}

function RowWithLetter({ row, previous, colSpan, children }: { row: DsmGridRow; previous?: DsmGridRow; colSpan: number; children: ReactNode }) {
  const newDisorder = !previous || previous.disorder !== row.disorder;
  const newLetter = newDisorder || previous!.letter !== row.letter;
  return <>{newDisorder && <tr className="dsm-disorder-row"><th colSpan={colSpan} scope="rowgroup">{DISORDER_NAME[row.disorder] ?? row.disorder}</th></tr>}
    {newLetter && !newDisorder && <tr className="dsm-letter-row" aria-hidden="true"><td colSpan={colSpan} /></tr>}{children}</>;
}

const DISORDER_NAME: Record<string, string> = {
  MDE: "Major depressive episode (MDE)", ANXD: "Specifier: with anxious distress", PDD: "Persistent depressive disorder (differential)",
  MANIA: "Manic episode", HYPO: "Hypomanic episode", CYC: "Cyclothymic disorder (differential)", GAD: "Generalized anxiety disorder", SAD: "Social anxiety disorder",
};

/** Findings behind a grid cell: status, frequency, citations and counter-evidence, review and manual entry. */
export function FindingDrawer({ sessionId, features, title, sessionLabels, onClose, onChanged }: { sessionId: string; features: string[]; title: string; sessionLabels: Record<string, string>; onClose: () => void; onChanged: () => void }) {
  const [rows, setRows] = useState<DsmFinding[] | null>(null);
  const [registry, setRegistry] = useState<DsmRegistryView | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => progressApi.dsmFindings(sessionId).then(r => setRows(r.filter(f => features.includes(f.feature_key)))).catch(e => setError(e.message)), [sessionId, features]);
  useEffect(() => { void load(); progressApi.dsmRegistry().then(setRegistry).catch(() => {}); }, [load]);
  return <aside className="evidence-drawer" role="dialog" aria-label={`${title} evidence`}>
    <div className="panel-heading"><div><p className="eyebrow">{sessionLabels[sessionId] ?? sessionId}</p><h4>{title}</h4></div><button type="button" className="text-button" onClick={onClose}>Close</button></div>
    {error && <p className="form-error">{error}</p>}
    {rows?.length === 0 && <p className="small-note">Not assessed in this session. Use the session's DSM findings panel to analyse it or enter a finding.</p>}
    {rows?.map(f => <FindingCard key={f.id} f={f} registry={registry} sessionLabels={sessionLabels} onChanged={async () => { await load(); onChanged(); }} />)}
  </aside>;
}

export function FindingCard({ f, registry, sessionLabels, decided, onChanged }: { f: DsmFinding; registry: DsmRegistryView | null; sessionLabels?: Record<string, string>; decided?: boolean; onChanged: () => void }) {
  const feature = registry?.features.find(x => x.key === f.feature_key);
  return <div className="obs-card dsm-finding">
    <div className="card-head"><strong>{f.feature_label}</strong><span className={`dsm-status status-${f.status}`}>{DSM_STATUS[f.status]?.icon} {DSM_STATUS[f.status]?.text}</span><ReviewBadge info={f} compact /></div>
    <p className="small-note">{[f.group !== "exclusion" && f.frequency !== "unclear" && f.frequency, f.qualifier, f.degree?.replace("_", " "), f.time_frame === "past_episode" ? `past episode${f.period_label ? `: ${f.period_label}` : ""}` : "", f.onset_or_duration_text && `onset/duration: ${f.onset_or_duration_text}`,
      f.contexts.length ? `linked to ${f.contexts.map(c => c.replace("_", " ")).join(", ")}` : "", f.clinician_intensity != null ? `clinician intensity ${f.clinician_intensity}/3` : ""].filter(Boolean).join(" · ") || "No detail recorded."}</p>
    <p className="small-note">{f.linked_items.length ? `Counts for ${f.linked_items.join(", ")}` : "Exclusion check: present means it could explain the symptoms; absent means it is cited as ruled out."}</p>
    {f.basis && <p>{f.basis}</p>}
    <Citations items={f.evidence} sessionLabels={sessionLabels} empty="No cited turns." />
    {f.counter_evidence.length > 0 && <><p className="small-note"><strong>Counter-evidence</strong></p><Citations items={f.counter_evidence.map(c => ({ ...c, basis: c.kind === "denial" ? "Client denial" : "Alternative explanation" }))} sessionLabels={sessionLabels} /></>}
    {f.status === "present" && f.provenance === "AI_GENERATED" && <p className="small-note">Counter-evidence search: {f.counter_check?.searched ? (f.counter_evidence.length ? "found the turns above" : "none found") : "not reported"}{f.counter_check?.alternative_explanation ? ` · ${f.counter_check.alternative_explanation}` : ""}</p>}
    <ReviewActions info={f} disabled={decided} onReview={async p => { await progressApi.reviewDsmFinding(f.session_id, f.id, p); onChanged(); }}
      editForm={submit => <FindingFields initial={f} qualifiers={feature?.qualifiers ?? []} degree={feature?.degree ?? false} contexts={registry?.contexts ?? []} submit={submit} />} />
    {f.alternatives.length > 0 && <details open><summary>New AI suggestion not applied ({f.alternatives.length})</summary><ul>{f.alternatives.map((a, i) => <li key={i}>{String(a.status)} — {String(a.basis ?? "")}</li>)}</ul></details>}
    {f.history.length > 0 && <details><summary>Review history ({f.history.length})</summary><ol className="history-list">{f.history.map((h, i) => <li key={i}>{String(h.actor)} · {String(h.created_at).slice(0, 16).replace("T", " ")} · was {String(h.status)} ({String(h.review_status)})</li>)}</ol></details>}
  </div>;
}

export function FindingFields({ initial, qualifiers, degree, contexts, submit, submitLabel = "Save edit", intensity = false }: {
  initial: Partial<DsmFinding>; qualifiers: string[]; degree: boolean; contexts: string[]; submit: (value: Record<string, unknown>, comment?: string) => void; submitLabel?: string; intensity?: boolean;
}) {
  const [status, setStatus] = useState<DsmStatus>(initial.status ?? "present");
  const [frequency, setFrequency] = useState(initial.frequency ?? "unclear");
  const [qualifier, setQualifier] = useState(initial.qualifier ?? "");
  const [deg, setDeg] = useState(initial.degree ?? "");
  const [ctx, setCtx] = useState<string[]>(initial.contexts ?? []);
  const [basis, setBasis] = useState(initial.basis ?? "");
  const [level, setLevel] = useState(initial.clinician_intensity != null ? String(initial.clinician_intensity) : "");
  const [comment, setComment] = useState("");
  return <div className="domain-edit">
    <label>Status<select value={status} onChange={e => setStatus(e.target.value as DsmStatus)}>{STATUS_ORDER.map(s => <option key={s} value={s}>{DSM_STATUS[s].text}</option>)}</select></label>
    <label>Frequency<select value={frequency} onChange={e => setFrequency(e.target.value)}>{["nearly every day", "more days than not", "most of the day", "occasional", "unclear"].map(v => <option key={v} value={v}>{v}</option>)}</select></label>
    {qualifiers.length > 0 && <label>Qualifier<select value={qualifier} onChange={e => setQualifier(e.target.value)}><option value="">—</option>{qualifiers.map(q => <option key={q} value={q}>{q}</option>)}</select></label>}
    {degree && <label>Degree<select value={deg} onChange={e => setDeg(e.target.value)}><option value="">—</option><option value="clinically_significant">clinically significant</option><option value="marked">marked</option></select></label>}
    {intensity && <label>Intensity (clinician, 0–3)<select value={level} onChange={e => setLevel(e.target.value)}><option value="">—</option>{[0, 1, 2, 3].map(v => <option key={v} value={v}>{v}</option>)}</select></label>}
    <fieldset className="dsm-contexts"><legend>Linked to</legend>{contexts.map(c => <label key={c}><input type="checkbox" checked={ctx.includes(c)} onChange={e => setCtx(e.target.checked ? [...ctx, c] : ctx.filter(x => x !== c))} /> {c.replace("_", " ")}</label>)}</fieldset>
    <label className="wide">Basis<input value={basis} onChange={e => setBasis(e.target.value)} maxLength={600} /></label>
    {!intensity && <label className="wide">Comment<input value={comment} onChange={e => setComment(e.target.value)} /></label>}
    <button type="button" className="button button-small" onClick={() => submit({ status, frequency, qualifier: qualifier || null, degree: deg || null, contexts: ctx, basis, ...(intensity ? { clinician_intensity: level === "" ? null : Number(level) } : {}) }, comment || undefined)}>{submitLabel}</button>
  </div>;
}

export function DsmTimeline({ data }: { data: PatientProgress }) {
  const dsm = data.dsm!;
  const rows = dsm.timeline.filter(t => t.points.some(p => p.record_id));
  if (!rows.length) return null;
  return <section className="panel progress-panel" aria-labelledby="dsm-timeline-title">
    <div className="panel-heading"><div><p className="eyebrow">Over time</p><h3 id="dsm-timeline-title">Hypothesis timeline</h3><p className="small-note">Label and required criteria met at each session the hypotheses were run as of. Blank cells are gaps.</p></div></div>
    <div className="table-scroll"><table className="progress-table"><thead><tr><th scope="col">Condition</th>{data.sessions.map(s => <th key={s.id} scope="col">S{s.index}</th>)}</tr></thead>
      <tbody>{rows.map(t => <tr key={t.condition_key}><th scope="row">{t.label}{t.role !== "target" ? <span className="small-note"> ({t.role})</span> : null}</th>{t.points.map(p => <td key={p.slot}>{p.record_id ? <><strong>{DSM_LABEL[(p.label ?? "insufficient_evidence") as keyof typeof DSM_LABEL]}</strong><span className="cell-sub"> {p.criteria_met_count}/{p.criteria_required} met</span></> : <span className="gap-mark">—</span>}</td>)}</tr>)}</tbody></table></div>
  </section>;
}

/** Explicit, reversible re-analysis of past sessions with the DSM registry. Decided-note sessions are left as they are. */
export function ReanalysisPanel({ data, job, onChanged }: { data: PatientProgress; job: JobHandle; onChanged: () => void }) {
  const [ack, setAck] = useState(false);
  const [actor, setActor] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const batches = data.dsm?.batches ?? [];
  const decided = data.sessions.filter(s => !["DRAFT", "NONE", "none", ""].includes(s.note_status)).length;
  async function revert(id: string) {
    if (!actor.trim()) { setError("Enter your name to revert."); return; }
    setError("");
    try { const r = await progressApi.revertBatch(data.patient_id, id, { actor: actor.trim() }); setDone(`Reverted: ${r.deactivated} DSM values deactivated, ${r.restored} earlier values restored.`); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not revert."); }
  }
  return <details className="panel progress-panel reanalysis-panel"><summary><strong>Re-analyse earlier sessions with DSM criteria</strong> <span className="small-note">({batches.length} past analyses)</span></summary>
    <p className="small-note">Runs the DSM extraction on every session whose note is not decided ({data.sessions.length - decided} of {data.sessions.length}), then the hypotheses. For each re-analysed session, the earlier unreviewed model output leaves the dashboard (clinician-entered and reviewed values stay). Nothing is deleted: “Revert” restores it.</p>
    <label><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} /> I have reviewed these transcripts (speaker roles and text)</label>
    <div className="panel-tools"><button type="button" className="button button-small" disabled={!ack || job.running} onClick={() => void job.start(() => progressApi.startReanalysis(data.patient_id, { transcript_reviewed: true }))}>Start re-analysis</button></div>
    {batches.length > 0 && <><label className="inline-field">Reverting as<input value={actor} onChange={e => setActor(e.target.value)} placeholder="Your name" /></label>
      <ul className="card-list">{batches.map(b => <li key={b.id} className="obs-card"><div className="card-head"><strong>{b.kind === "reanalysis" ? "Re-analysis" : "Session analysis"}</strong>
        <span className="small-note">{formatDate(b.created_at)} · {b.session_ids.length} session{b.session_ids.length === 1 ? "" : "s"}{b.skipped.length ? ` · skipped ${b.skipped.length} (decided notes)` : ""} · {b.status}{b.reverted_by ? ` by ${b.reverted_by}` : ""}</span>
        {!b.reverted_at && <button type="button" className="text-button" onClick={() => void revert(b.id)}>Revert</button>}</div></li>)}</ul></>}
    {error && <p className="form-error" role="alert">{error}</p>}{done && <p className="small-note" role="status">{done}</p>}
  </details>;
}

export function EarlierTracking({ show, children }: { show: boolean; children: ReactNode }) {
  if (!show) return null;
  return <details className="panel progress-panel earlier-tracking"><summary><strong>Earlier (non-DSM) tracking</strong> <span className="small-note">Values from registry v1 (20 domains), kept readable and never converted.</span></summary>
    <p className="small-note">See <a className="text-link" href="/workspace/registry">the DSM registry</a> for how each earlier domain maps to DSM criteria.</p>{children}</details>;
}

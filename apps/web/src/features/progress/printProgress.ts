import { formatTimestamp } from "../../lib/api";
import { DOMAIN_STATUS_LABEL, PREDICTION_LABEL, RISK_LABEL, SUBTYPE_LABEL, type Cited, type PatientProgress } from "./api";
import { badgeText } from "./common";
import { NO_FLAGS_TEXT } from "./RiskStatements";

const esc = (v: unknown) => String(v ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));
const PLAN_STATUS: Record<string, string> = { not_reviewed: "Not reviewed", discussed_done: "Discussed — done", discussed_partial: "Discussed — partly done", discussed_not_done: "Discussed — not done", not_discussed: "Not discussed" };
const FINDING: Record<string, string> = { met: "Met", not_met: "Not met", insufficient_evidence: "Insufficient evidence", conflicting: "Conflicting" };

/** Printable summary: badges on every value, citations as session/timestamp references, no signature. */
export function printProgress(data: PatientProgress, patientName: string) {
  const label = Object.fromEntries(data.sessions.map(s => [s.id, `S${s.index}`]));
  const ref = (c: { session_id: string; start_ms: number }) => `${label[c.session_id] ?? c.session_id} ${formatTimestamp(c.start_ms)}`;
  const badge = (i: { provenance: never | string; review_status: string; reviewer_name?: string | null }) => `<em>[${esc(badgeText(i as Parameters<typeof badgeText>[0]).label)}]</em>`;
  // Rationale sentences cite segment ids; print them as session/timestamp references where the findings resolve them.
  const cited = new Map<string, Cited>();
  for (const p of data.predictions) for (const f of p.findings) for (const e of [...f.evidence, ...f.counter_evidence]) cited.set(e.segment_id, e);
  const refs = (ids: string[]) => ids.map(id => cited.has(id) ? ref(cited.get(id)!) : "cited turn").join(", ");

  const hypotheses = data.predictions.map(p => {
    const findings = p.findings.filter(f => f.status !== "insufficient_evidence");
    return `<li><strong>${esc(p.condition_label)}: ${esc(PREDICTION_LABEL[p.prediction])}</strong>${p.subtype_key ? ` (${esc(SUBTYPE_LABEL[p.subtype_key] ?? p.subtype_key)})` : ""} — criteria met ${p.criteria_met_count}/${p.criteria_required}, duration ${esc(p.duration_status)}, impairment ${esc(p.impairment_status)}, as of ${esc(label[p.as_of_session_id])}${p.probability != null ? ` · calibrated probability ${p.probability.toFixed(2)} (research)` : ""} ${badge(p)}
      ${p.rationale.length ? `<ul>${p.rationale.map(r => `<li>${esc(r.sentence)} [${esc(refs(r.segment_ids))}]</li>`).join("")}</ul>` : ""}
      ${findings.length ? `<table><tr><th>Criterion</th><th>Finding</th><th>Cited turns</th></tr>${findings.map(f => `<tr><td>${esc(f.summary || f.criterion_id)}</td><td>${esc(FINDING[f.status] ?? f.status)}</td><td>${esc([...f.evidence, ...f.counter_evidence].map(ref).join(", ") || "—")}</td></tr>`).join("")}</table>` : ""}
      ${p.missing_information.length ? `<p>Information still needed: ${p.missing_information.map(esc).join(" · ")}</p>` : ""}</li>`;
  }).join("") || "<li>No hypotheses generated.</li>";
  const risks = data.risk_statements.filter(r => r.review_status !== "rejected").map(r => `<li><strong>${esc(RISK_LABEL[r.category])}</strong> · ${esc(label[r.session_id])} ${badge(r)}
      <ul>${r.evidence.map(e => `<li>${esc(ref(e))} ${e.speaker_role === "CLIENT" ? "Client" : e.speaker_role === "CLINICIAN" ? "Clinician" : "Unknown"}: “${esc(e.text ?? "")}”</li>`).join("")}</ul><p>Model's reason: ${esc(r.basis)}</p></li>`).join("") || `<li>${esc(NO_FLAGS_TEXT)}</li>`;
  const scores = data.instruments.map(ins => `<h3>${esc(ins.label)} (${esc(ins.metric_label)})</h3><table><tr><th>Session</th>${ins.series.map(s => `<th>${s.series === "ai_estimate" ? "AI estimate" : "Administered"}</th>`).join("")}</tr>${ins.series[0].points.map((p, i) => `<tr><td>${esc(label[p.slot] ?? p.date?.slice(0, 10) ?? p.slot)}</td>${ins.series.map(s => { const q = s.points[i]; return `<td>${q?.value ?? "—"}${q?.provenance ? " " + badge({ provenance: q.provenance, review_status: q.review_status ?? "pending", reviewer_name: q.reviewer_name }) : ""}</td>`; }).join("")}</tr>`).join("")}</table>`).join("");
  const domains = `<table><tr><th>Domain</th>${data.sessions.map(s => `<th>S${s.index}</th>`).join("")}</tr>${data.domains.map(d => `<tr><td>${esc(d.label)}</td>${d.cells.map(c => `<td>${!c.record_id ? "—" : c.status === "rated" ? esc(c.rating) : esc(DOMAIN_STATUS_LABEL[c.status as keyof typeof DOMAIN_STATUS_LABEL] ?? c.status)}${c.provenance ? `<br>${badge({ provenance: c.provenance, review_status: c.review_status ?? "pending", reviewer_name: c.reviewer_name })}` : ""}</td>`).join("")}</tr>`).join("")}</table>`;
  const plans = data.plan_followups.map(p => `<li>${esc(label[p.origin_session_id])} plan: ${esc(p.plan_text)} → ${esc(label[p.followup_session_id])}: <strong>${esc(PLAN_STATUS[p.status] ?? p.status)}</strong>${p.evidence.length ? ` (${esc(p.evidence.map(ref).join(", "))})` : ""} ${badge(p)}</li>`).join("") || "<li>No plan follow-ups recorded.</li>";
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Progress summary</title><style>body{font:12px/1.45 system-ui,sans-serif;color:#222;margin:24px}table{border-collapse:collapse;margin:6px 0 14px}td,th{border:1px solid #999;padding:3px 6px;text-align:left;vertical-align:top}em{color:#555;font-size:10px}h1{font-size:18px}h2{font-size:14px;margin-top:18px}h3{font-size:12px}li{margin:3px 0}</style></head><body>
<h1>Progress summary — ${esc(patientName)}</h1><p>${data.sessions.length} sessions · ${data.pending_review_counts.total ?? 0} values pending review. Printed ${esc(new Date().toLocaleString())}. AI-generated values are labelled; unreviewed values are not clinician conclusions. Hypotheses are AI-generated for clinician review and are not diagnoses. Registries: ${esc(Object.values(data.versions).join("; "))}.</p>
<h2>Clinical hypotheses</h2><ul>${hypotheses}</ul><h2>Flagged statements (harm to self or others)</h2><p><em>Flagged for clinician review. The model does not score risk; the absence of flags does not establish safety.</em></p><ul>${risks}</ul><h2>Questionnaires</h2>${scores}<h2>Domains</h2>${domains}
<h2>Changes</h2><ul>${data.change_events.map(c => `<li>${esc(c.measure_key)} ${esc(c.rule_id)} ${esc(label[c.from_session_id] ?? c.from_session_id)} → ${esc(label[c.to_session_id] ?? c.to_session_id)}${c.delta != null ? ` (Δ ${c.delta})` : ""} · inputs ${esc(c.provenance_of_inputs)}${c.acknowledged_by ? ` · acknowledged by ${esc(c.acknowledged_by)}` : ""}</li>`).join("") || "<li>None detected.</li>"}</ul>
<h2>Plans and follow-up</h2><ul>${plans}</ul>
<p><em>Citations are session/timestamp references to the local transcript. This printout carries no signature.</em></p></body></html>`;
  const w = window.open("", "_blank");
  if (!w) return;
  w.document.write(html);
  w.document.close();
  w.focus();
  w.print();
}

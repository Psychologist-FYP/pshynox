import { formatTimestamp } from "../../lib/api";
import { DOMAIN_STATUS_LABEL, PREDICTION_LABEL, RISK_LABEL, SUBTYPE_LABEL, type PatientProgress } from "./api";
import { badgeText } from "./common";

const esc = (v: unknown) => String(v ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]!));

/** Printable summary: badges on every value, citations as session/timestamp references, no signature. */
export function printProgress(data: PatientProgress, patientName: string) {
  const label = Object.fromEntries(data.sessions.map(s => [s.id, `S${s.index}`]));
  const ref = (c: { session_id: string; start_ms: number }) => `${label[c.session_id] ?? c.session_id} ${formatTimestamp(c.start_ms)}`;
  const badge = (i: { provenance: never | string; review_status: string; reviewer_name?: string | null }) => `<em>[${esc(badgeText(i as Parameters<typeof badgeText>[0]).label)}]</em>`;
  const hypotheses = data.predictions.map(p => `<li><strong>${esc(p.condition_label)}: ${esc(PREDICTION_LABEL[p.prediction])}</strong>${p.subtype_key ? ` (${esc(SUBTYPE_LABEL[p.subtype_key] ?? p.subtype_key)})` : ""} — criteria ${p.criteria_met_count}/${p.criteria_required}, duration ${esc(p.duration_status)}, as of ${esc(label[p.as_of_session_id])} ${badge(p)}<ul>${p.rationale.map(r => `<li>${esc(r.sentence)} [${esc(r.segment_ids.join(", "))}]</li>`).join("")}</ul></li>`).join("") || "<li>No hypotheses generated.</li>";
  const risks = data.risk_statements.filter(r => r.review_status !== "rejected").map(r => `<li>${esc(RISK_LABEL[r.category])} · ${esc(label[r.session_id])} · ${esc(r.basis)} (${r.evidence.map(ref).join(", ")}) ${badge(r)}</li>`).join("") || "<li>No statements flagged by the model. This is not a risk assessment.</li>";
  const scores = data.instruments.map(ins => `<h3>${esc(ins.label)} (${esc(ins.metric_label)})</h3><table><tr><th>Session</th>${ins.series.map(s => `<th>${s.series === "ai_estimate" ? "AI estimate" : "Administered"}</th>`).join("")}</tr>${ins.series[0].points.map((p, i) => `<tr><td>${esc(label[p.slot] ?? p.date?.slice(0, 10) ?? p.slot)}</td>${ins.series.map(s => { const q = s.points[i]; return `<td>${q?.value ?? "—"}${q?.provenance ? " " + badge({ provenance: q.provenance, review_status: q.review_status ?? "pending", reviewer_name: q.reviewer_name }) : ""}</td>`; }).join("")}</tr>`).join("")}</table>`).join("");
  const domains = `<table><tr><th>Domain</th>${data.sessions.map(s => `<th>S${s.index}</th>`).join("")}</tr>${data.domains.map(d => `<tr><td>${esc(d.label)}</td>${d.cells.map(c => `<td>${!c.record_id ? "—" : c.status === "rated" ? esc(c.rating) : esc(DOMAIN_STATUS_LABEL[c.status as keyof typeof DOMAIN_STATUS_LABEL] ?? c.status)}${c.provenance ? `<br>${badge({ provenance: c.provenance, review_status: c.review_status ?? "pending", reviewer_name: c.reviewer_name })}` : ""}</td>`).join("")}</tr>`).join("")}</table>`;
  const html = `<!doctype html><html><head><meta charset="utf-8"><title>Progress summary</title><style>body{font:12px/1.45 system-ui,sans-serif;color:#222;margin:24px}table{border-collapse:collapse;margin:6px 0 14px}td,th{border:1px solid #999;padding:3px 6px;text-align:left;vertical-align:top}em{color:#555;font-size:10px}h1{font-size:18px}h2{font-size:14px;margin-top:18px}h3{font-size:12px}</style></head><body>
<h1>Progress summary — ${esc(patientName)}</h1><p>${data.sessions.length} sessions. Printed ${esc(new Date().toLocaleString())}. AI-generated values are labelled; unreviewed values are not clinician conclusions. Hypotheses are not diagnoses. Registries: ${esc(Object.values(data.versions).join("; "))}.</p>
<h2>Clinical hypotheses</h2><ul>${hypotheses}</ul><h2>Flagged statements (harm to self or others)</h2><ul>${risks}</ul><h2>Questionnaires</h2>${scores}<h2>Domains</h2>${domains}
<h2>Changes</h2><ul>${data.change_events.map(c => `<li>${esc(c.measure_key)} ${esc(c.rule_id)} ${esc(label[c.from_session_id] ?? c.from_session_id)} → ${esc(label[c.to_session_id] ?? c.to_session_id)}${c.delta != null ? ` (Δ ${c.delta})` : ""} · inputs ${esc(c.provenance_of_inputs)}${c.acknowledged_by ? ` · acknowledged by ${esc(c.acknowledged_by)}` : ""}</li>`).join("") || "<li>None detected.</li>"}</ul>
<p><em>Citations are session/timestamp references to the local transcript. This printout carries no signature.</em></p></body></html>`;
  const w = window.open("", "_blank");
  if (!w) return;
  w.document.write(html);
  w.document.close();
  w.focus();
  w.print();
}

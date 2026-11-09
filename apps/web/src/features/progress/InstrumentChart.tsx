import { useMemo, useState } from "react";
import { CartesianGrid, LabelList, Line, LineChart, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { formatDate } from "../../lib/api";
import { progressApi, type ChangeEvent, type InstrumentBlock, type ProgressSession, type SeriesPoint } from "./api";
import { CHART, axisTick } from "./chartTheme";
import { badgeText } from "./common";

// Identity is carried by colour, line style and the legend together: solid blue = clinician-administered,
// dashed orange = AI estimate from the transcript.
const SERIES = {
  administered: { name: "Clinician-administered", color: CHART.blue, dash: undefined as string | undefined },
  ai_estimate: { name: "AI estimate", color: CHART.orange, dash: "5 4" },
};

type SeriesKey = keyof typeof SERIES;
type Row = { slot: string; label: string; date: string; administered: number | null; ai_estimate: number | null; points: Partial<Record<SeriesKey, SeriesPoint>> };

const shortDate = (iso?: string) => iso ? new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "";

export function InstrumentChart({ block, sessions, changes, patientId, onSaved }: { block: InstrumentBlock; sessions: ProgressSession[]; changes: ChangeEvent[]; patientId: string; onSaved: () => void }) {
  const [table, setTable] = useState(false);
  const [adding, setAdding] = useState(false);
  const bySession = useMemo(() => Object.fromEntries(sessions.map(s => [s.id, s])), [sessions]);
  const rows = useMemo(() => {
    const out = new Map<string, Row>();
    for (const series of block.series) {
      for (const p of series.points) {
        const session = bySession[p.slot];
        const label = session ? `S${session.index}` : `${p.date ? formatDate(p.date) : p.slot} (between sessions)`;
        const row = out.get(p.slot) ?? { slot: p.slot, label, date: shortDate(session?.created_at ?? p.date), administered: null, ai_estimate: null, points: {} };
        row[series.series] = p.value;
        row.points[series.series] = p;
        out.set(p.slot, row);
      }
    }
    return [...out.values()];
  }, [block, bySession]);
  const markers = changes.filter(c => c.measure_kind === "instrument" && c.measure_key.startsWith(block.instrument_key));
  // One marker per session; several series changing at the same session share it.
  const markerRows = rows.flatMap(row => {
    const here = markers.filter(m => m.to_session_id === row.slot && m.delta != null);
    if (!here.length) return [];
    const text = here.map(m => `${m.series === "administered" ? "Admin." : "AI"} ${m.delta! > 0 ? "+" : "−"}${Math.abs(m.delta!)}`).join("  ");
    return [{ row, text: `Δ ${text}` }];
  });
  const present = block.series.filter(s => s.summary.sessions_assessed > 0);
  const hasData = present.length > 0;
  const ticks = [...new Set([0, ...block.bands.map(b => b.min), block.metric_max])].sort((a, b) => a - b);
  const dates = Object.fromEntries(rows.map(r => [r.label, r.date]));
  const lastIndex = Object.fromEntries(present.map(s => [s.series, rows.reduce((last, r, i) => (r[s.series] != null ? i : last), -1)]));
  const caption = `${block.metric_label}${block.reliable_change_threshold == null ? " · reliable-change threshold not set, so no change markers" : ` · change marker when |Δ| ≥ ${block.reliable_change_threshold}`}`;
  return <section className="panel progress-panel instrument-panel" aria-labelledby={`chart-${block.instrument_key}`}>
    <div className="panel-heading"><div><p className="eyebrow">Questionnaire</p><h3 id={`chart-${block.instrument_key}`}>{block.label}</h3><p className="small-note">{caption}</p></div>
      <div className="panel-tools"><button type="button" className="text-button" aria-pressed={table} onClick={() => setTable(!table)}>{table ? "Show chart" : "Show table"}</button><button type="button" className="button button-small" onClick={() => setAdding(!adding)}>Add administration</button></div></div>
    {adding && <AdministrationForm block={block} sessions={sessions} patientId={patientId} onDone={() => { setAdding(false); onSaved(); }} />}
    {!hasData && <p className="empty-note">No {block.label} values yet. Missing sessions are gaps, not zero.</p>}
    {hasData && !table && <figure className="chart-surface" aria-label={`${block.label} total score by session`}>
      <div className="chart-head">
        <span className="chart-axis-title">Total score (0–{block.metric_max})</span>
        <div className="chart-legend">{present.map(s => <span key={s.series}><svg width="28" height="10" aria-hidden="true"><line x1="1" x2="27" y1="5" y2="5" stroke={SERIES[s.series].color} strokeWidth="2" strokeDasharray={SERIES[s.series].dash} /><circle cx="14" cy="5" r="3.5" fill={SERIES[s.series].color} stroke={CHART.surface} strokeWidth="1.5" /></svg>{SERIES[s.series].name}{s.trajectory ? <span className="legend-note"> · trend {s.trajectory}</span> : null}</span>)}</div>
      </div>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={rows} margin={{ top: 26, right: 104, bottom: 4, left: 0 }}>
          {block.bands.map((b, i) => <ReferenceArea key={b.label} y1={b.min} y2={Math.min(b.max + (i === block.bands.length - 1 ? 0 : 1), block.metric_max)} fill={i % 2 ? CHART.bandB : CHART.bandA} fillOpacity={1} stroke="none" ifOverflow="hidden"
            label={{ value: b.label.charAt(0).toUpperCase() + b.label.slice(1), position: "right", offset: 10, fontSize: 11, fill: CHART.muted, fontFamily: CHART.font }} />)}
          <CartesianGrid stroke={CHART.grid} vertical={false} />
          <XAxis dataKey="label" interval={0} tickLine={false} axisLine={{ stroke: CHART.axis }} padding={{ left: 20, right: 20 }} height={40}
            tick={({ x, y, payload }: { x: number | string; y: number | string; payload: { value: string } }) => <g transform={`translate(${x},${y})`}>
              <text y={14} textAnchor="middle" fontSize={11} fontWeight={600} fill={CHART.ink} fontFamily={CHART.font}>{payload.value}</text>
              {dates[payload.value] && <text y={28} textAnchor="middle" fontSize={10} fill={CHART.muted} fontFamily={CHART.font}>{dates[payload.value]}</text>}</g>} />
          <YAxis domain={[0, block.metric_max]} ticks={ticks} interval={0} width={34} tick={axisTick} tickLine={false} axisLine={false} />
          {markerRows.map(({ row, text }) => <ReferenceLine key={row.slot} x={row.label} stroke={CHART.axis} strokeWidth={1} strokeDasharray="3 3"
            label={{ value: text, position: "top", fontSize: 10, fill: CHART.inkSecondary, fontFamily: CHART.font }} />)}
          <Tooltip content={<PointTooltip block={block} />} cursor={{ stroke: CHART.axis, strokeWidth: 1 }} isAnimationActive={false} />
          {present.map(s => <Line key={s.series} type="linear" dataKey={s.series} name={SERIES[s.series].name} stroke={SERIES[s.series].color} strokeWidth={2} strokeDasharray={SERIES[s.series].dash}
            strokeLinejoin="round" strokeLinecap="round" dot={{ r: 4, strokeWidth: 2, stroke: CHART.surface, fill: SERIES[s.series].color, strokeDasharray: "0" }} activeDot={{ r: 6, strokeWidth: 2, stroke: CHART.surface, strokeDasharray: "0" }}
            connectNulls={false} isAnimationActive={false}>
            <LabelList dataKey={s.series} content={(props: { x?: number | string; y?: number | string; value?: unknown; index?: number }) => props.index === lastIndex[s.series] && props.value != null
              ? <text x={Number(props.x)} y={Number(props.y) - 10} textAnchor="middle" fontSize={12} fontWeight={600} fill={CHART.ink} fontFamily={CHART.font}>{String(props.value)}</text> : null} />
          </Line>)}
        </LineChart>
      </ResponsiveContainer>
      <figcaption className="chart-note">Shaded rows are severity ranges. A break in a line is a session without a value; gaps are not zero and are not interpolated.
        {present.some(s => s.series === "ai_estimate") ? " AI estimates come from the session transcript and stay pending until a clinician reviews them." : ""}</figcaption>
    </figure>}
    {hasData && table && <InstrumentTable block={block} rows={rows} />}
    {hasData && !!block.followup_domains?.length && <FollowupDomains block={block} rows={rows} />}
  </section>;
}

/** Level 1 Cross-Cutting measure: a domain needs further inquiry when its highest item reaches the domain threshold. */
function FollowupDomains({ block, rows }: { block: InstrumentBlock; rows: Row[] }) {
  const latest = [...rows].reverse().find(r => r.points.administered?.items);
  if (!latest) return null;
  const items = latest.points.administered!.items!;
  const flagged = block.followup_domains!.filter(d => d.items.some(i => (items[i] ?? -1) >= d.threshold));
  return <div className="followup-domains"><p className="small-note"><strong>{latest.label}: domains for further inquiry</strong> (highest item at or above the domain threshold; not a diagnosis)</p>
    {flagged.length ? <ul>{flagged.map(d => <li key={d.domain}>{d.domain}. {d.label} (threshold {d.threshold}: highest item {Math.max(...d.items.map(i => items[i] ?? 0))})</li>)}</ul> : <p className="small-note">None at threshold.</p>}</div>;
}

function PointTooltip({ active, payload, block }: { active?: boolean; payload?: { payload: Row }[]; block: InstrumentBlock }) {
  if (!active || !payload?.length) return null;
  const row = payload[0].payload;
  return <div className="chart-tooltip"><strong>{row.label}{row.date ? ` · ${row.date}` : ""}</strong>
    {(["administered", "ai_estimate"] as const).map(key => { const p = row.points[key]; if (!p || p.value == null) return null; return <div key={key} className="tooltip-series">
      <span className="tooltip-line"><svg width="18" height="8" aria-hidden="true"><line x1="1" x2="17" y1="4" y2="4" stroke={SERIES[key].color} strokeWidth="2" strokeDasharray={SERIES[key].dash} /></svg> {SERIES[key].name}<b>{p.value}</b>{p.partial ? " (partial)" : ""}{key === "administered" && p.total != null && block.metric ? ` · full total ${p.total}` : ""}</span>
      {p.provenance && <span className="tooltip-badge">{badgeText({ provenance: p.provenance, review_status: p.review_status ?? "pending", reviewer_name: p.reviewer_name }).label}</span>}
      {p.items && <span className="tooltip-items">Items {Object.entries(p.items).map(([id, v]) => `${id}=${v ?? "–"}`).join("  ")}</span>}
    </div>; })}
  </div>;
}

function InstrumentTable({ block, rows }: { block: InstrumentBlock; rows: Row[] }) {
  return <div className="table-scroll"><table className="progress-table"><caption className="sr-only">{block.label} values by session</caption>
    <thead><tr><th scope="col">Session</th><th scope="col">Administered ({block.metric_label})</th><th scope="col">AI estimate</th><th scope="col">Items (administered / AI)</th></tr></thead>
    <tbody>{rows.map(r => <tr key={r.slot}><th scope="row">{r.label}</th>
      {(["administered", "ai_estimate"] as const).map(k => { const p = r.points[k]; return <td key={k}>{p?.value != null ? <>{p.value}{p.partial ? " (partial)" : ""} <span className="small-note">{p.provenance ? badgeText({ provenance: p.provenance, review_status: p.review_status ?? "pending", reviewer_name: p.reviewer_name }).label : ""}</span></> : <span className="gap-mark">{p?.gap_reason === "rejected" ? "rejected" : p?.gap_reason === "stale" ? "sources changed" : "gap"}</span>}</td>; })}
      <td className="small-note">{(["administered", "ai_estimate"] as const).map(k => r.points[k]?.items ? Object.entries(r.points[k]!.items!).map(([id, v]) => `${id}:${v ?? "–"}`).join(" ") : "–").join(" / ")}</td></tr>)}</tbody></table></div>;
}

function AdministrationForm({ block, sessions, patientId, onDone }: { block: InstrumentBlock; sessions: ProgressSession[]; patientId: string; onDone: () => void }) {
  const [actor, setActor] = useState("");
  const [sessionId, setSessionId] = useState(sessions.at(-1)?.id ?? "");
  const [date, setDate] = useState("");
  const [items, setItems] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  async function save() {
    setError("");
    try {
      await progressApi.addScore(patientId, { actor, instrument_key: block.instrument_key, session_id: sessionId || null, administered_at: sessionId ? null : date || null,
        items: Object.fromEntries(block.items.map(i => [i.id, items[i.id] === undefined || items[i.id] === "" ? null : Number(items[i.id])])) });
      onDone();
    } catch (e) { setError(e instanceof Error ? e.message : "Could not save."); }
  }
  return <form className="administration-form" onSubmit={e => { e.preventDefault(); void save(); }} aria-label={`Add ${block.label} administration`}>
    <div className="form-row"><label>Clinician<input required value={actor} onChange={e => setActor(e.target.value)} /></label>
      <label>Session<select value={sessionId} onChange={e => setSessionId(e.target.value)}><option value="">Between sessions (enter date)</option>{sessions.map(s => <option key={s.id} value={s.id}>Session {s.index} · {formatDate(s.created_at)}</option>)}</select></label>
      {!sessionId && <label>Date<input type="date" required value={date} onChange={e => setDate(e.target.value)} /></label>}</div>
    <div className="item-grid">{block.items.map(i => <label key={i.id}><span>{i.id}. {i.label}</span><select aria-label={`Item ${i.id}`} value={items[i.id] ?? ""} onChange={e => setItems({ ...items, [i.id]: e.target.value })}><option value="">Not administered</option>{Array.from({ length: (block.item_max ?? 3) - (block.item_min ?? 0) + 1 }, (_, n) => n + (block.item_min ?? 0)).map(v => <option key={v} value={v}>{v}</option>)}</select></label>)}</div>
    {block.licence_note && <p className="small-note">{block.licence_note}{block.reporting_period ? ` Reporting period: ${block.reporting_period}.` : ""}</p>}
    <button className="button button-small" type="submit">Save administration</button>{error && <p className="form-error" role="alert">{error}</p>}
  </form>;
}

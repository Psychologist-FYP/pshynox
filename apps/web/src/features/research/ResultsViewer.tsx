import { useEffect, useState } from 'react';
import { formatTimestamp } from '../../lib/api';
import { download, methods, research, type Dataset, type Job, type Report } from './api';
const metricNames: Record<string, string> = { recall_at_5: 'Recall@5', recall_at_10: 'Recall@10', precision_at_5: 'Precision@5', mrr_at_10: 'MRR@10', ndcg_at_10: 'nDCG@10', false_positive_return_rate: 'No-answer false positives', abstention_rate_no_answer: 'No-answer abstention' };
const number = (value: number | null | undefined, digits = 3) => value == null ? '—' : value.toFixed(digits);

export function ResultsViewer({ dataset }: { dataset: Dataset | undefined }) {
  const [jobs, setJobs] = useState<Job[]>([]), [id, setId] = useState('');
  const [view, setView] = useState<{ job: Job; report: Report | null } | null>(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [split, setSplit] = useState('dev'), [selectedMethods, setMethods] = useState(methods);
  const [threshold, setThreshold] = useState(''), [frozen, setFrozen] = useState('');
  const [metric, setMetric] = useState('ndcg_at_10'), [methodIndex, setMethodIndex] = useState(0), [qid, setQid] = useState('');
  useEffect(() => {
    let active = true;
    const refresh = async () => { try { const items = await research.runs(); if (active) { setJobs(items); setId(current => current || items[0]?.run_id || ''); } } catch (e) { if (active) setError((e as Error).message); } };
    void refresh(); const timer = setInterval(refresh, 1800);
    return () => { active = false; clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!id) return;
    let active = true;
    const refresh = async () => { try { const result = await research.result(id); if (active) setView(result); } catch (e) { if (active) setError((e as Error).message); } };
    setView(null); setQid(''); setMethodIndex(0); void refresh(); const timer = setInterval(refresh, 1800);
    return () => { active = false; clearInterval(timer); };
  }, [id]);
  const report = view?.report;
  const data = report?.evaluation_dataset;
  const method = report?.results[methodIndex];
  const availableQueries = data?.queries.filter(q => method?.queries?.some(row => row.query_id === q.query_id)) || [];
  const query = availableQueries.find(q => q.query_id === qid) || availableQueries[0];
  const conversation = data?.conversations.find(c => c.conversation_id === query?.conversation_id);
  const rankedSegments = method?.variant === 'asr' ? conversation?.asr_segments : conversation?.segments;
  const row = method?.queries?.find(q => q.query_id === query?.query_id);
  async function start() {
    if (!dataset) return;
    setBusy(true); setError('');
    try {
      const job = await research.run(dataset.dataset_id, { split, methods: selectedMethods, min_score: split === 'dev' && threshold !== '' ? Number(threshold) : null, frozen_run: frozen || null });
      setJobs(current => [job, ...current]); setId(job.run_id);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function exportDataset() {
    if (!dataset) return;
    setBusy(true); setError('');
    try { download(`${dataset.dataset_id}-${split}.json`, JSON.stringify(await research.export(dataset.dataset_id, split), null, 2)); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function csv() {
    if (!report) return;
    const keys = Object.keys(metricNames);
    const rows = [['method', 'variant', 'status', ...keys, 'warm_median_ms', 'rss_bytes'], ...report.results.map(r => [r.method, r.variant, r.status, ...keys.map(k => r.quality?.[k] ?? ''), r.latency_ms?.warm_median ?? '', r.memory?.isolated_process_peak_rss_bytes ?? ''])];
    download(`${id}-metrics.csv`, rows.map(row => row.map(v => `"${String(v).replaceAll('"', '""')}"`).join(',')).join('\n'), 'text/csv');
  }
  return <div className="research-results">
    <section className="panel research-card">
      <h2>Evaluate the annotated pilot</h2>
      <p>Every query in the selected pilot split must be approved and every turn reviewed. A no-answer query also requires explicit confirmation.</p>
      <div className="research-controls">
        <label>Evaluation split<select value={split} onChange={e => { setSplit(e.target.value); setThreshold(''); setFrozen(''); }}><option value="dev">Development</option><option value="test">Held-out test</option></select></label>
        <label>Frozen development configuration<select value={frozen} onChange={e => { setFrozen(e.target.value); setThreshold(''); }}><option value="">Baseline configuration</option>{jobs.filter(j => j.status === 'completed' && j.split === 'dev' && j.dataset_id === dataset?.dataset_id && j.dataset_version === dataset?.version).map(j => <option key={j.run_id} value={j.run_id}>{j.run_id}</option>)}</select></label>
        <label>Development minimum score<input type="number" step="any" value={threshold} disabled={split !== 'dev' || !!frozen || selectedMethods.length !== 1} onChange={e => setThreshold(e.target.value)} placeholder="Default: no threshold" /></label>
      </div>
      <fieldset><legend>Methods to compare</legend>{methods.map(m => <label className="research-check" key={m}><input type="checkbox" checked={selectedMethods.includes(m)} onChange={e => { setThreshold(''); setMethods(current => e.target.checked ? [...current, m] : current.filter(v => v !== m)); }} />{m}</label>)}</fieldset>
      <p className="small-note">Thresholds are method-specific. Select one method for a development threshold experiment. Apply its frozen development configuration to the same method on test data.</p>
      <div className="research-actions"><button className="button button-primary" disabled={busy || !dataset || !selectedMethods.length || jobs.some(j => j.status === 'running')} onClick={start}>Run benchmark</button><button className="button button-secondary" disabled={busy || !dataset} onClick={exportDataset}>Export validated dataset</button></div>
      {error && <p role="alert" className="form-error">{error}</p>}
    </section>
    <section className="panel research-card">
      <label>Import a benchmark results JSON<input type="file" accept=".json,application/json" disabled={busy} onChange={async e => {
        const file = e.target.files?.[0]; if (!file) return;
        setBusy(true); setError('');
        try { const job = await research.importReport(JSON.parse(await file.text())); setJobs(current => [job, ...current]); setId(job.run_id); }
        catch (error) { setError((error as Error).message); } finally { setBusy(false); e.target.value = ''; }
      }} /></label>
      <label>Saved benchmark run<select value={id} onChange={e => setId(e.target.value)}><option value="">Select a run</option>{jobs.map(j => <option key={j.run_id} value={j.run_id}>{j.dataset_id} · {j.split} · {j.status} · {j.run_id}</option>)}</select></label>
      {view && <p role="status">Run: {view.job.status}{view.job.error && ` — ${view.job.error}`}</p>}
      {report && <>
        <h2>{report.result_kind === 'fictional' ? 'Fictional smoke test — not dataset evaluation' : `Dataset evaluation: ${data?.dataset_id || report.dataset_description}`}</h2>
        <p>{report.split} split · {report.query_count ?? data?.queries.length} queries · {report.annotation_status}</p>
        <p className="small-note research-wrap">Dataset SHA-256: {report.dataset_sha256}<br />Version: {report.dataset_version || 'Recorded by dataset hash'}</p>
        <div className="research-actions"><button className="button button-secondary" onClick={() => download(`${id}.json`, JSON.stringify(report, null, 2))}>Export full results JSON</button><button className="button button-secondary" onClick={csv}>Export metrics CSV</button>{report.split === 'dev' && <button className="button button-secondary" onClick={() => download(`${id}-config.json`, JSON.stringify({ settings: report.settings, calibration: report.calibration, methods: report.results.filter(r => r.status === 'measured').map(r => r.method) }, null, 2))}>Export frozen configuration</button>}</div>
        <div className="research-table"><table><thead><tr><th>Method / variant</th><th>Status</th>{Object.values(metricNames).map(v => <th key={v}>{v}</th>)}</tr></thead><tbody>{report.results.map((r, i) => <tr key={i}><th>{r.method} / {r.variant}</th><td>{r.status}{r.reason && <details><summary>Reason</summary><p>{r.reason}</p></details>}</td>{Object.keys(metricNames).map(k => <td key={k}>{number(r.quality?.[k])}</td>)}</tr>)}</tbody></table></div>
        <label>Comparison chart<select value={metric} onChange={e => setMetric(e.target.value)}>{Object.entries(metricNames).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
        <div className="research-chart" role="img" aria-label={`${metricNames[metric]} comparison, values from zero to one`}>{report.results.map((r, i) => <div key={i} className="research-bar-row"><span>{r.method} / {r.variant}</span><div className="research-bar-track">{r.quality?.[metric] != null && <div className="research-bar" style={{ width: `${100 * r.quality[metric]!}%` }} />}</div><span>{number(r.quality?.[metric])}</span></div>)}</div>
        <div className="research-table"><table><thead><tr><th>Method</th><th>First searches total (ms)</th><th>Warm median (ms)</th><th>Warm p95 (ms)</th><th>Peak process RSS (MiB)</th><th>Python allocation peak (MiB)</th></tr></thead><tbody>{report.results.map((r, i) => <tr key={i}><th>{r.method} / {r.variant}</th><td>{number(r.latency_ms?.first_search_total, 2)}</td><td>{number(r.latency_ms?.warm_median, 2)}</td><td>{number(r.latency_ms?.warm_p95, 2)}</td><td>{number(r.memory?.isolated_process_peak_rss_bytes == null ? null : r.memory.isolated_process_peak_rss_bytes / 1048576, 1)}</td><td>{number(r.memory?.python_tracemalloc_peak_bytes == null ? null : r.memory.python_tracemalloc_peak_bytes / 1048576, 1)}</td></tr>)}</tbody></table></div>
        <p className="small-note">First searches can include cold library/model loading; their sum is not a single cold-start measurement. Warm timings include fingerprinting and tracemalloc instrumentation. RSS includes interpreter, native libraries and model weights; Python allocation tracking excludes most native memory. Missing results are shown as —. Retrieval scores describe ranking relevance.</p>
        <details><summary>Configuration, versions and ASR measurement status</summary><pre>{JSON.stringify({ settings: report.settings, calibration: report.calibration, asr: report.asr, ...Object.fromEntries(Object.entries(report).filter(([key]) => ['python', 'platform', 'packages', 'created_at', 'metric_policy', 'memory_policy'].includes(key))) }, null, 2)}</pre></details>
      </>}
    </section>
    {report && <section className="panel research-card"><h2>Inspect individual queries</h2><div className="research-controls"><label>Method results<select value={methodIndex} onChange={e => { setMethodIndex(Number(e.target.value)); setQid(''); }}>{report.results.map((r, i) => <option key={i} value={i}>{r.method} / {r.variant} · {r.status}</option>)}</select></label><label>Evaluated query<select value={query?.query_id || ''} onChange={e => setQid(e.target.value)}>{availableQueries.map(q => <option key={q.query_id} value={q.query_id}>{q.query_id}: {q.text}</option>)}</select></label></div>
      {query && <><p><strong>{query.text}</strong></p><p>{query.intent}</p></>}
      {method?.status !== 'measured' && <p>No measured query results for this method.</p>}
      {row && !row.ranked_source_ids.length && <p>No passages returned.</p>}
      {row?.ranked_source_ids.map((sid, rank) => {
        const index = rankedSegments?.findIndex(s => s.id === sid) ?? -1;
        const central = rankedSegments?.[index];
        const judgment = data?.judgments.find(j => j.query_id === query?.query_id && j.source_segment_id === sid);
        const radius = report.settings.context_turns ?? 1;
        return <article className="research-hit" key={sid}><p><strong>#{rank + 1} · {central?.speaker_role || 'Source unavailable'} · Grade {judgment?.grade ?? 'unreviewed'}</strong> · Retrieval score {number(row.scores[rank])}</p><code>{sid}</code><p>{central?.text || 'Original source text unavailable in this report'}</p>{judgment && <p className="small-note">{judgment.reviewer}: {judgment.rationale}</p>}<details><summary>Source context</summary>{rankedSegments?.slice(Math.max(0, index - radius), index + radius + 1).map(s => <p key={s.id}><strong>{s.id === sid ? 'Matched' : 'Context'} · {s.speaker_role} · {formatTimestamp(s.start_ms)}–{formatTimestamp(s.end_ms)}</strong><br />{s.text}</p>)}</details></article>;
      })}
    </section>}
  </div>;
}

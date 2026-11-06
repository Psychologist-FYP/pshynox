import { useState } from 'react';
import { request } from '../../lib/api';
import type { Detail } from './api';
export type AiDraftSet = { model: string; model_version: string; annotation_policy_version: string; queries: {query_id: string; text: string; intent: string; kind: string; suggestions: {source_segment_id: string; suggested_grade: number; rationale: string; ambiguous: boolean; ambiguity_explanation: string; context_turn_ids: string[]}[]}[] };
export function AiDrafts({ detail, name, reviewer, update }: {detail: Detail; name: string; reviewer: string; update: (d: Detail) => void}) {
  const [qi, setQi] = useState(0), [si, setSi] = useState(0);
  const [grade, setGrade] = useState<number | null>(null), [rationale, setRationale] = useState<string | null>(null);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const drafts = detail.ai_drafts;
  if (!drafts) return null;
  const q = drafts.queries[qi], p = q?.suggestions[si];
  if (!q || !p) return null;
  const human = detail.queries.find(v => v.query_id === q.query_id)?.judgments || {};
  const count = drafts.queries.reduce((n, v) => n + v.suggestions.length, 0);
  const reviewed = drafts.queries.reduce((n, v) => n + Object.keys(detail.queries.find(h => h.query_id === v.query_id)?.judgments || {}).length, 0);
  const bulk = drafts.queries.reduce((n, v) => n + Object.values(detail.queries.find(h => h.query_id === v.query_id)?.judgments || {}).filter(j => j.review_action === 'bulk-accepted').length, 0);
  const turn = detail.conversation.segments.find(s => s.id === p.source_segment_id);
  const reset = () => { setGrade(null); setRationale(null); setError(''); };
  async function accept() {
    setBusy(true); setError('');
    try {
      const d = await request<Detail>(`/api/v1/research/datasets/${encodeURIComponent(name)}/conversations/${encodeURIComponent(detail.conversation.conversation_id)}/ai/${encodeURIComponent(q.query_id)}/turns/${encodeURIComponent(p.source_segment_id)}`, undefined,
        {method: 'POST', body: JSON.stringify({revision: detail.revision, grade: grade ?? p.suggested_grade, rationale: rationale ?? p.rationale, reviewer})});
      update(d); reset(); setSi(i => Math.min(i + 1, q.suggestions.length - 1));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <section className="panel research-card"><h2>AI suggestions · human review pending</h2>
    <p>{drafts.queries.length} AI draft queries · {count} AI turn suggestions · {reviewed - bulk} individually human-reviewed · {bulk} bulk accepted from a sample · {count - reviewed} pending</p>
    <p className="small-note">{drafts.model} · version {drafts.model_version} · policy {drafts.annotation_policy_version}. These suggestions do not count toward evaluation until human review, query approval and any no-answer confirmation.</p>
    <label>AI query<select value={qi} disabled={busy} onChange={e => {setQi(Number(e.target.value)); setSi(0); reset();}}>{drafts.queries.map((v, i) => <option key={v.query_id} value={i}>{v.kind} · {v.text}</option>)}</select></label>
    <p><strong>{q.text}</strong></p><p>{q.intent}</p>
    <label>Suggested turn<select value={si} disabled={busy} onChange={e => {setSi(Number(e.target.value)); reset();}}>{q.suggestions.map((v, i) => <option key={v.source_segment_id} value={i}>{v.source_segment_id} · AI {v.suggested_grade}{v.ambiguous ? ' · AMBIGUOUS' : ''}{human[v.source_segment_id] ? ' · human reviewed' : ''}</option>)}</select></label>
    <p><strong>{turn?.speaker_role}</strong> · {turn?.text}</p>
    {p.context_turn_ids.map(id => {const s = detail.conversation.segments.find(v => v.id === id); return <p key={id} className="small-note">Context {id} · {s?.speaker_role}: {s?.text}</p>;})}
    {p.ambiguous && <p className="research-notice"><strong>Ambiguous:</strong> {p.ambiguity_explanation}</p>}
    <p>Original AI suggestion: grade {p.suggested_grade} · {p.rationale}</p>
    <label>Human-reviewed grade<select value={grade ?? p.suggested_grade} disabled={busy} onChange={e => setGrade(Number(e.target.value))}>{[0,1,2].map(v => <option key={v} value={v}>{v}</option>)}</select></label>
    <label>Human-reviewed rationale<textarea value={rationale ?? p.rationale} disabled={busy} onChange={e => setRationale(e.target.value)} /></label>
    <p>Reviewer: {reviewer || 'Enter your name in the query panel below.'}</p>
    <button className="button button-primary" disabled={busy || !reviewer.trim() || !(rationale ?? p.rationale).trim() || !!human[p.source_segment_id]} onClick={accept}>Accept or save edited suggestion & next</button>
    {human[p.source_segment_id] && <p>Human judgment saved. Select this query in the human annotation editor to revise it.</p>}
    <p>After reviewing, reload saved progress and select the AI query in the human query editor below to approve its wording explicitly.</p>
    {error && <p role="alert">{error}</p>}
  </section>;
}

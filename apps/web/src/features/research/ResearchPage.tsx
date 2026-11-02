import { useEffect, useRef, useState } from 'react';
import { TopBar } from '../../components/layout/AppShell';
import { formatTimestamp, type EvidenceHit, type Health } from '../../lib/api';
import { methods, research, type Dataset, type Detail } from './api';
import { ResultsViewer } from './ResultsViewer';
import './research.css';

export function ResearchPage({ health }: { health: Health | null }) {
  const [datasets, setDatasets] = useState<Dataset[]>([]), [name, setName] = useState('annomi'), [cid, setCid] = useState('');
  const [detail, setDetail] = useState<Detail | null>(null), [qid, setQid] = useState('');
  const [mode, setMode] = useState('annotate'), [pilotOnly, setPilotOnly] = useState(true);
  const [text, setText] = useState(''), [intent, setIntent] = useState(''), [approved, setApproved] = useState(false);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem('research-reviewer') || '');
  const [index, setIndex] = useState(0), [grade, setGrade] = useState<number | null>(null), [rationale, setRationale] = useState('');
  const [keepRationale, setKeepRationale] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState('');
  const [exploreText, setExploreText] = useState(''), [method, setMethod] = useState('bm25'), [hits, setHits] = useState<EvidenceHit[] | null>(null);
  const turnRef = useRef<HTMLButtonElement | null>(null);
  const dataset = datasets.find(d => d.dataset_id === name);
  const query = detail?.queries.find(q => q.query_id === qid);
  const segment = detail?.conversation.segments[index];
  const currentJudgment = segment && query?.judgments[segment.id];
  const queryDirty = !!query && (text !== query.text || intent !== query.intent || approved === query.draft);
  const refresh = async () => { const items = await research.datasets(); setDatasets(items); return items; };
  useEffect(() => { void refresh().catch(e => setError(e.message)); }, []);
  useEffect(() => {
    const selected = dataset?.conversations.find(c => c.conversation_id === cid && (!pilotOnly || c.pilot));
    if (!selected) setCid(dataset?.selection.pilot_ids[0] || dataset?.conversations[0]?.conversation_id || '');
  }, [name, dataset, pilotOnly]);
  useEffect(() => {
    if (!cid) return;
    const controller = new AbortController(); setDetail(null); setError(''); setHits(null); setIndex(0);
    research.conversation(name, cid, controller.signal).then(result => { setDetail(result); setQid(result.queries[0]?.query_id || ''); }).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [name, cid]);
  useEffect(() => { setText(query?.text || ''); setIntent(query?.intent || ''); setApproved(query ? !query.draft : false); setIndex(0); }, [qid, cid]);
  useEffect(() => {
    setGrade(currentJudgment?.grade ?? null);
    setRationale(current => currentJudgment?.rationale || (keepRationale ? current : ''));
    turnRef.current?.scrollIntoView({ block: 'nearest' });
  }, [index, qid, cid]);
  async function mutate(operation: () => Promise<Detail>, message: string) {
    setBusy(true); setError(''); setSaved('');
    try { const result = await operation(); setDetail(result); setSaved(message); await refresh(); return result; }
    catch (e) { setError((e as Error).message); return null; }
    finally { setBusy(false); }
  }
  async function saveQuery(noAnswer = false) {
    if (!detail) return;
    const result = await mutate(() => research.query(name, cid, { revision: detail.revision, query_id: qid || null, text, intent, draft: !approved, no_answer_confirmed: noAnswer, reviewer }), 'Query saved');
    if (result && !qid) setQid(result.queries[result.queries.length - 1].query_id);
  }
  async function saveGrade(next = false, value = grade) {
    if (!detail || !segment || !query || busy) return;
    if (queryDirty || (next && value === null)) { setError('Save query changes and select a relevance grade before saving a judgment.'); return; }
    const result = await mutate(() => research.grade(name, cid, qid, segment.id, { revision: detail.revision, grade: value, reviewer, rationale }), value === null ? 'Turn marked unreviewed' : 'Judgment saved');
    if (result && next) setIndex(i => Math.min(i + 1, result.conversation.segments.length - 1));
    if (result && value === null) { setGrade(null); setRationale(''); }
  }
  async function explore() {
    if (!detail) return;
    setBusy(true); setError(''); setHits(null);
    try { const result = await research.explore(name, cid, { revision: detail.revision, text: exploreText, method }); setHits(result.hits); setDetail({ ...detail, revision: result.revision, explored: true }); }
    catch (e) { setError((e as Error).message); const result = await research.conversation(name, cid); setDetail(result); }
    finally { setBusy(false); }
  }
  function shortcuts(event: React.KeyboardEvent) {
    if (mode !== 'annotate' || busy) return;
    if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); void saveGrade(true); return; }
    const tag = (event.target as HTMLElement).tagName;
    if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(tag)) return;
    if (['0', '1', '2'].includes(event.key)) { event.preventDefault(); setGrade(Number(event.key)); }
    if (event.key === 'j' || event.key === 'ArrowDown') { event.preventDefault(); setIndex(i => Math.min((detail?.conversation.segments.length || 1) - 1, i + 1)); }
    if (event.key === 'k' || event.key === 'ArrowUp') { event.preventDefault(); setIndex(i => Math.max(0, i - 1)); }
  }
  return <div className="research-app" onKeyDown={shortcuts}>
    <TopBar health={health} workspace />
    <main className="research-main">
      <div className="research-heading"><div><p className="eyebrow">Research · separate from clinical records</p><h1>Retrieval evaluation</h1><p>Prepare human relevance judgments, explore development transcripts, and compare retrieval methods.</p></div><a href="/workspace" className="button button-secondary">Clinical workspace</a></div>
      <nav className="research-tabs" aria-label="Research modes">{[['annotate', 'Annotate without rankings'], ['explore', 'Explore without labels'], ['results', 'Evaluation results']].map(([key, label]) => <button key={key} aria-pressed={mode === key} className={`button ${mode === key ? 'button-primary' : 'button-secondary'}`} onClick={() => { setMode(key); setHits(null); setError(''); }}>{label}</button>)}</nav>
      <section className="panel research-card">
        <div className="research-controls"><label>Research dataset<select value={name} disabled={busy} onChange={e => { setName(e.target.value); setCid(''); }}>{datasets.map(d => <option key={d.dataset_id} value={d.dataset_id}>{d.dataset_id} · {d.conversations.length} conversations</option>)}</select></label>
          {mode !== 'results' && <><label>Conversation<select value={cid} disabled={busy} onChange={e => setCid(e.target.value)}>{dataset?.conversations.filter(c => !pilotOnly || c.pilot).map(c => <option key={c.conversation_id} value={c.conversation_id}>{c.conversation_id} · {c.split} · {c.turns} turns · {c.complete_queries}/{c.queries} queries complete{c.pilot ? ' · pilot' : ''}</option>)}</select></label><label className="research-check"><input type="checkbox" checked={pilotOnly} onChange={e => setPilotOnly(e.target.checked)} />Pilot conversations only</label></>}
        </div>
        {dataset && <><p><strong>Annotation status: {dataset.conversations.filter(c => c.pilot).reduce((total, c) => total + c.complete_queries, 0)}/{dataset.conversations.filter(c => c.pilot).reduce((total, c) => total + c.queries, 0)} pilot queries complete</strong></p><p className="small-note">Selection seed {dataset.selection.seed} · {dataset.selection.pilot_ids.length} pilot conversations · target approximately {dataset.selection.target_queries} reviewed queries. Drafts and unfinished judgments await your annotation.</p><p className="small-note">{dataset.selection.identity_limit}</p></>}
        {!datasets.length && <p>Prepare the supplied data first: <code>.venv/bin/python -m services.retrieval.prepare</code></p>}
        {detail && !dataset?.conversations.find(c => c.conversation_id === cid)?.pilot && <button className="button button-secondary" disabled={busy} onClick={async () => { setBusy(true); try { await research.expand(name, cid, detail.revision); await refresh(); setDetail(await research.conversation(name, cid)); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }}>Add this conversation and its source group to pilot</button>}
        {error && <p role="alert" className="form-error">{error}</p>}
        {saved && <p className="research-saved" role="status">{saved}</p>}
        <button className="research-reload" disabled={busy} onClick={async () => { setError(''); try { await refresh(); if (cid) setDetail(await research.conversation(name, cid)); } catch (e) { setError((e as Error).message); } }}>Reload saved progress</button>
      </section>
      {mode === 'results' ? <ResultsViewer dataset={dataset} /> : detail && <>
        {mode === 'annotate' && <section className="panel research-card">
          <h2>Query and intended evidence</h2><p>Rankings are hidden here. Read the conversation before grading. Query suggestions are drafts, not approved research questions.</p>
          {detail.explored && <p className="research-notice">This development conversation has been explored. That exposure is recorded in exports; use an independent reviewer for unbiased judgments where practical.</p>}
          <div className="research-controls"><label>Annotation query<select value={qid} disabled={busy} onChange={e => setQid(e.target.value)}><option value="">Create a new query</option>{detail.queries.map(q => <option key={q.query_id} value={q.query_id}>{q.draft ? 'Draft' : 'Approved wording'} · {q.progress.reviewed}/{q.progress.total} reviewed · {q.text}</option>)}</select></label><label>Reviewer name<input value={reviewer} onChange={e => { setReviewer(e.target.value); localStorage.setItem('research-reviewer', e.target.value); }} placeholder="Your name or reviewer identifier" /></label></div>
          <label>Query wording<textarea value={text} onChange={e => setText(e.target.value)} /></label>
          <label>What evidence should this query retrieve?<textarea value={intent} onChange={e => setIntent(e.target.value)} /></label>
          <label className="research-check"><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)} />I reviewed and approved this query wording and intended evidence.</label>
          <p className="small-note">Changing saved wording or intent clears that query’s old judgments, so they cannot silently apply to a different question.</p>
          <div className="research-actions"><button className="button button-primary" disabled={busy || !text.trim() || !intent.trim()} onClick={() => saveQuery()}>Save query</button>{query?.progress.needs_no_answer_confirmation && <button className="button button-secondary" disabled={busy || !approved || !reviewer.trim()} onClick={() => saveQuery(true)}>Confirm I reviewed the full conversation: no relevant evidence</button>}</div>
          {query && <p className="research-progress"><strong>{query.progress.reviewed}/{query.progress.total} turns reviewed</strong> · {query.progress.total - query.progress.reviewed} unreviewed · {query.progress.complete ? 'Complete for export' : query.draft ? 'Awaiting query approval and annotation' : query.no_answer_confirmed ? 'No-answer confirmed' : 'Annotation or no-answer confirmation still needed'}</p>}
        </section>}
        {mode === 'explore' && <section className="panel research-card"><h2>Exploration — not evaluation</h2><p>Search without seeing relevance labels. Exploration is recorded and limited to development conversations. Results are evidence candidates; scores describe retrieval relevance.</p><div className="research-controls"><label>Exploration query<input value={exploreText} onChange={e => setExploreText(e.target.value)} /></label><label>Retrieval method<select value={method} onChange={e => setMethod(e.target.value)}>{methods.map(m => <option key={m}>{m}</option>)}</select></label><button className="button button-primary" disabled={busy || !exploreText.trim() || detail.conversation.split !== 'dev'} onClick={explore}>Explore</button></div>{detail.conversation.split === 'test' && <p>Held-out test conversation: search is locked until evaluation; its transcript remains available for annotation.</p>}{hits && (hits.length ? hits.map(hit => <article key={hit.segment_id} className="research-hit"><strong>{hit.speaker_role} · {formatTimestamp(hit.start_ms)} · relevance {hit.score.toFixed(3)}</strong><p>{hit.excerpt}</p><code>{hit.segment_id}</code><details><summary>Surrounding conversation</summary>{hit.sources?.map(s => <p key={s.id}><strong>{s.matched ? 'Matched' : 'Context'} · {s.speaker_role}</strong><br />{s.text}</p>)}</details></article>) : <p>No passages retrieved.</p>)}</section>}
        <div className={`research-annotation-grid ${mode === 'explore' ? 'research-read-only' : ''}`}>
          <section className="panel research-card"><h2>Transcript · {cid}</h2><p>{detail.conversation.split} split · {detail.conversation.segments.length} turns. Point timestamps indicate that AnnoMI does not supply authoritative end times.</p>
            <div className="research-transcript" tabIndex={0} aria-label="Transcript keyboard controls">{detail.conversation.segments.map((s, i) => {
              const judgment = mode === 'annotate' ? query?.judgments[s.id] : undefined;
              return <button type="button" ref={i === index ? turnRef : undefined} key={s.id} className={`research-turn ${mode === 'annotate' && i === index ? 'selected' : ''}`} onClick={() => setIndex(i)} aria-pressed={i === index}><span><strong>{s.speaker_role} · {formatTimestamp(s.start_ms)}–{formatTimestamp(s.end_ms)}</strong>{mode === 'annotate' && <span className={`research-grade ${judgment ? 'reviewed' : ''}`}>{judgment ? `Grade ${judgment.grade}` : 'Unreviewed'}</span>}</span><code>{s.id}</code><p>{s.text}</p>{detail.conversation.source_files?.[s.id] && <small>Source: {detail.conversation.source_files[s.id]}</small>}</button>;
            })}</div>
          </section>
          {mode === 'annotate' && <section className="panel research-card research-grading" tabIndex={0}><h2>Review selected turn</h2>{segment && <><code>{segment.id}</code><p><strong>{segment.speaker_role} · {formatTimestamp(segment.start_ms)}–{formatTimestamp(segment.end_ms)}</strong></p><p>{segment.text}</p><p>Saved query: <strong>{query?.text || 'None selected'}</strong></p>{queryDirty && <p>Save query changes before grading.</p>}
            <fieldset disabled={busy || !query || queryDirty}><legend>Relevance to the saved query</legend>{[[0, 'Irrelevant'], [1, 'Partial evidence or useful context'], [2, 'Direct evidence']].map(([v, label]) => <label className="research-check" key={v}><input type="radio" name="grade" checked={grade === v} onChange={() => setGrade(Number(v))} />{v} = {label}</label>)}</fieldset>
            <label>Rationale<textarea value={rationale} disabled={busy || !query} onChange={e => setRationale(e.target.value)} placeholder="Why does this turn receive this grade?" /></label>
            <label className="research-check"><input type="checkbox" checked={keepRationale} onChange={e => setKeepRationale(e.target.checked)} />Carry this rationale to the next turn for explicit reuse</label>
            {currentJudgment && <p className="small-note">Saved by {currentJudgment.reviewer}: {currentJudgment.rationale}</p>}
            <div className="research-actions"><button className="button button-primary" disabled={busy || !query || queryDirty || grade === null || !reviewer.trim() || !rationale.trim()} onClick={() => saveGrade(true)}>Save judgment & next</button><button className="button button-secondary" disabled={busy || !query || queryDirty || !currentJudgment} onClick={() => saveGrade(false, null)}>Mark unreviewed</button></div>
            <div className="research-actions"><button className="button button-secondary" disabled={busy || index === 0} onClick={() => setIndex(i => i - 1)}>Previous turn</button><button className="button button-secondary" disabled={busy || index === detail.conversation.segments.length - 1} onClick={() => setIndex(i => i + 1)}>Next turn</button></div>
            <p className="small-note">Keyboard: focus this panel, then 0/1/2 selects a grade; J/K or arrows moves turns. Ctrl+Enter saves and advances, including from the rationale field. Unreviewed is different from grade 0.</p>
            {!query && <p>Create and save a query before grading.</p>}
          </>}</section>}
        </div>
      </>}
    </main>
  </div>;
}

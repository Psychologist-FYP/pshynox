import { useEffect, useState, type ReactNode } from "react";
import { api, formatTimestamp, type TranscriptSegment } from "../../lib/api";
import { RISK_LABEL, audioUrl, playCitation, type Cited, type ReviewPayload, type RiskStatement } from "./api";
import { ReviewActions, badgeText, transcriptHref } from "./common";

export const NO_FLAGS_TEXT = "No statements flagged by the model. This is not a risk assessment.";

type Line = { id: string; start_ms: number; end_ms: number; speaker_role: string; text: string; flagged: boolean; cited?: Cited };
const SPEAKER: Record<string, string> = { CLIENT: "Client", CLINICIAN: "Clinician", UNKNOWN: "Unknown" };

/** Full transcripts of the sessions that carry flags, so each flag can show its neighbouring turns. */
function useTranscripts(sessionIds: string[]) {
  const key = [...new Set(sessionIds)].sort().join("|");
  const [byId, setById] = useState<Record<string, TranscriptSegment[] | null>>({});
  useEffect(() => {
    if (!key) return;
    const controller = new AbortController();
    for (const id of key.split("|")) {
      api.transcript(id, controller.signal).then(rows => setById(m => ({ ...m, [id]: rows }))).catch(() => setById(m => ({ ...m, [id]: null })));
    }
    return () => controller.abort();
  }, [key]);
  return byId;
}

/** Flagged turns plus one turn either side; gaps between windows are marked. Falls back to the cited turns alone. */
function excerpt(evidence: Cited[], transcript: TranscriptSegment[] | null | undefined): (Line | "gap")[] {
  const cited = new Map(evidence.map(c => [c.segment_id, c]));
  if (!transcript?.length) return evidence.map(c => ({ id: c.segment_id, start_ms: c.start_ms, end_ms: c.end_ms, speaker_role: c.speaker_role, text: c.text ?? "", flagged: true, cited: c }));
  const hits = transcript.flatMap((s, i) => (cited.has(s.id) ? [i] : []));
  if (!hits.length) return excerpt(evidence, null);
  const keep = new Set(hits.flatMap(i => [i - 1, i, i + 1]).filter(i => i >= 0 && i < transcript.length));
  const out: (Line | "gap")[] = [];
  let last = -1;
  for (const i of [...keep].sort((a, b) => a - b)) {
    if (last >= 0 && i > last + 1) out.push("gap");
    const s = transcript[i];
    out.push({ id: s.id, start_ms: s.start_ms, end_ms: s.end_ms, speaker_role: s.speaker_role, text: s.text, flagged: cited.has(s.id), cited: cited.get(s.id) });
    last = i;
  }
  return out;
}

function Excerpt({ lines }: { lines: (Line | "gap")[] }) {
  return <table className="risk-excerpt">
    <caption className="sr-only">Transcript excerpt; flagged turns are highlighted</caption>
    <tbody>{lines.map((l, n) => l === "gap"
      ? <tr key={`gap-${n}`} className="risk-gap"><td colSpan={3}>⋯</td></tr>
      : <tr key={l.id} className={l.flagged ? "is-flagged" : undefined}>
          <td className="risk-time">{formatTimestamp(l.start_ms)}</td>
          <th scope="row" className="risk-speaker">{SPEAKER[l.speaker_role] ?? l.speaker_role}</th>
          <td className="risk-text">{l.flagged ? <mark>{l.text || "(text unavailable)"}</mark> : l.text}
            {l.flagged && l.cited && <span className="risk-links">
              {audioUrl(l.cited) && <button type="button" className="risk-link" onClick={() => playCitation(l.cited!)}>Play audio</button>}
              <a className="risk-link" href={transcriptHref(l.cited)}>Open in transcript</a></span>}
          </td>
        </tr>)}</tbody>
  </table>;
}

export function RiskList({ risks, sessionLabels, decided, onReview, editForm }: {
  risks: RiskStatement[]; sessionLabels?: Record<string, string>; decided?: boolean;
  onReview: (r: RiskStatement, payload: ReviewPayload) => Promise<void>;
  editForm?: (r: RiskStatement) => (submit: (value: Record<string, unknown>, comment?: string) => void) => ReactNode;
}) {
  const transcripts = useTranscripts(risks.map(r => r.session_id));
  if (!risks.length) return <p className="risk-empty" role="status">{NO_FLAGS_TEXT}</p>;
  return <ol className="risk-list">{risks.map(r => {
    const status = badgeText(r);
    const first = r.evidence[0];
    return <li key={r.id} className="risk-item">
      <div className="risk-meta">
        <span className="risk-category">{RISK_LABEL[r.category]}</span>
        <span>{[sessionLabels?.[r.session_id], first ? `at ${formatTimestamp(first.start_ms)}` : ""].filter(Boolean).join(" ")}</span>
        <span className={`risk-status risk-status-${status.tone}`}>{status.label}</span>
      </div>
      <div className="risk-body">
        <Excerpt lines={excerpt(r.evidence, transcripts[r.session_id])} />
        <p className="risk-reason"><span>Model's reason:</span> {r.basis}</p>
        <ReviewActions info={r} disabled={decided} onReview={p => onReview(r, p)} editForm={editForm?.(r)} />
      </div>
    </li>;
  })}</ol>;
}

export function RiskReport({ id, title, risks, rejected, children }: { id: string; title: string; risks: RiskStatement[]; rejected: RiskStatement[]; children: ReactNode }) {
  const pending = risks.filter(r => r.review_status === "pending" || r.review_status === "stale").length;
  return <section className="risk-report" aria-labelledby={id}>
    <header className="risk-bar">
      <h3 id={id}>{title}</h3>
      <span>{risks.length ? `${risks.length} flagged · ${pending} awaiting review` : "None flagged"}</span>
    </header>
    <p className="risk-disclaimer">Statements the model flagged for clinician review. The model does not score risk, and the absence of flags does not establish safety.</p>
    {children}
    {rejected.length > 0 && <details className="risk-rejected"><summary>Rejected flags kept in history ({rejected.length})</summary>
      <ul>{rejected.map(r => <li key={r.id}>{RISK_LABEL[r.category]} · {r.reviewer_name ?? "reviewer"}: {r.reviewer_comment}</li>)}</ul></details>}
  </section>;
}

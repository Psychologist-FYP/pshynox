import { useState, type ReactNode } from "react";
import { formatTimestamp } from "../../lib/api";
import { audioUrl, playCitation, rememberReviewer, savedReviewer, type Cited, type ReviewInfo, type ReviewPayload } from "./api";

/** One badge for every AI or reviewed value: chart tooltips, grid cells, cards and print. */
export function ReviewBadge({ info, compact = false }: { info: Pick<ReviewInfo, "provenance" | "review_status" | "reviewer_name">; compact?: boolean }) {
  const { label, tone } = badgeText(info);
  return <span className={`review-badge review-${tone}${compact ? " is-compact" : ""}`} title={label}>{label}</span>;
}

export function badgeText(info: Pick<ReviewInfo, "provenance" | "review_status" | "reviewer_name">): { label: string; tone: string } {
  const who = info.reviewer_name || "reviewer";
  if (info.review_status === "stale") return { label: "Sources changed — regenerate or review", tone: "stale" };
  if (info.provenance === "MANUAL") return { label: `Entered by ${who}`, tone: "reviewed" };
  if (info.review_status === "accepted") return { label: `Reviewed by ${who}`, tone: "reviewed" };
  if (info.review_status === "edited") return { label: `Edited by ${who}`, tone: "edited" };
  if (info.review_status === "rejected") return { label: `Rejected by ${who}`, tone: "rejected" };
  return { label: "AI-generated · human review pending", tone: "pending" };
}

export function transcriptHref(c: Pick<Cited, "session_id" | "segment_id">) {
  return `/workspace/sessions/${c.session_id}#segment-${c.segment_id}`;
}

/** Prediction card → criterion → this row: ▶ plays the source audio, the link opens the transcript turn. */
export function CitationRow({ c, sessionLabel }: { c: Cited; sessionLabel?: string }) {
  return <li className="citation-row">
    <div className="citation-meta">
      <span className={`role-tag role-${c.speaker_role.toLowerCase()}`}>{c.speaker_role === "CLIENT" ? "Client" : c.speaker_role === "CLINICIAN" ? "Clinician" : "Unknown speaker"}</span>
      <span>{sessionLabel ? `${sessionLabel} · ` : ""}{formatTimestamp(c.start_ms)}–{formatTimestamp(c.end_ms)}</span>
      {audioUrl(c) ? <button type="button" className="text-button" aria-label={`Play source audio at ${formatTimestamp(c.start_ms)}`} onClick={() => playCitation(c)}>▶ Play</button> : <span className="small-note">No audio</span>}
      <a className="text-link" href={transcriptHref(c)}>Open in transcript</a>
    </div>
    {c.basis && <p className="citation-basis">{c.basis}</p>}
    {c.text && <p className="citation-text">“{c.text}”</p>}
  </li>;
}

export function Citations({ items, sessionLabels, empty = "No cited turns." }: { items: Cited[]; sessionLabels?: Record<string, string>; empty?: string }) {
  if (!items.length) return <p className="small-note">{empty}</p>;
  return <ul className="citation-list">{items.map(c => <CitationRow key={`${c.segment_id}-${c.basis}`} c={c} sessionLabel={sessionLabels?.[c.session_id]} />)}</ul>;
}

/** Accept / edit / reject. Edit content is supplied by the caller's form. */
export function ReviewActions({ info, disabled, onReview, editForm }: {
  info: ReviewInfo; disabled?: boolean; onReview: (payload: ReviewPayload) => Promise<void>;
  editForm?: (submit: (value: Record<string, unknown>, comment?: string) => void) => ReactNode;
}) {
  const [actor, setActor] = useState(savedReviewer());
  const [mode, setMode] = useState<"" | "edit" | "reject">("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (info.provenance !== "AI_GENERATED" || info.review_status === "rejected") return null;
  async function send(payload: Omit<ReviewPayload, "actor" | "expected_updated_at">) {
    if (!actor.trim()) { setError("Enter your name to review."); return; }
    setBusy(true); setError("");
    try {
      rememberReviewer(actor.trim());
      await onReview({ ...payload, actor: actor.trim(), expected_updated_at: info.updated_at });
      setMode(""); setReason("");
    } catch (e) { setError(e instanceof Error ? e.message : "Review failed."); }
    finally { setBusy(false); }
  }
  const locked = disabled || info.locked || busy;
  return <div className="review-actions">
    <label className="sr-only" htmlFor={`actor-${info.created_at}`}>Reviewer name</label>
    <input id={`actor-${info.created_at}`} className="reviewer-input" placeholder="Reviewer name" value={actor} onChange={e => setActor(e.target.value)} disabled={locked} />
    {(info.review_status === "pending" || info.review_status === "stale") && <button type="button" className="button button-small" disabled={locked} onClick={() => void send({ action: "accept" })}>Accept</button>}
    {editForm && <button type="button" className="button button-small button-secondary" disabled={locked} onClick={() => setMode(mode === "edit" ? "" : "edit")}>Edit</button>}
    <button type="button" className="button button-small button-ghost" disabled={locked} onClick={() => setMode(mode === "reject" ? "" : "reject")}>Reject</button>
    {info.locked && <span className="small-note">Read-only: this session's note is decided.</span>}
    {mode === "reject" && <div className="review-inline"><label>Reason<input value={reason} onChange={e => setReason(e.target.value)} /></label><button type="button" className="button button-small" disabled={locked || !reason.trim()} onClick={() => void send({ action: "reject", reason })}>Confirm rejection</button></div>}
    {mode === "edit" && editForm && <div className="review-inline">{editForm((value, comment) => void send({ action: "edit", value, comment }))}</div>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </div>;
}

export function sessionLabelMap(sessions: { id: string; index: number }[]): Record<string, string> {
  return Object.fromEntries(sessions.map(s => [s.id, `Session ${s.index}`]));
}

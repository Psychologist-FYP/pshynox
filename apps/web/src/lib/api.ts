export type Health = {
  status: string;
  app_version: string;
  fixture_mode: boolean;
};

export type Session = {
  id: string;
  pseudonym_id: string;
  session_type: "INITIAL_ASSESSMENT" | "FOLLOW_UP";
  consent: Record<string, string | boolean>;
  workflow_status: "DRAFT" | "IN_PROGRESS" | "READY_FOR_REVIEW" | "APPROVED" | "REJECTED";
  created_at: string;
};

export type TranscriptSegment = {
  id: string;
  session_id: string;
  start_ms: number;
  end_ms: number;
  speaker_role: "CLIENT" | "CLINICIAN" | "UNKNOWN";
  text: string;
  revision: number;
};

export type EvidenceHit = {
  segment_id: string;
  session_id: string;
  start_ms: number;
  end_ms: number;
  excerpt: string;
  score: number;
  section: string;
  query: string;
};
export type NoteField = {
  section: "subjective" | "objective" | "assessment" | "plan" | "functioning";
  text: string;
  provenance: "MANUAL" | "RETRIEVAL_CANDIDATE" | "TEMPLATE";
  evidence_ids: string[];
  review_status: "PENDING" | "REVIEWED";
};
export type NoteDraft = { id: string; session_id: string; fields: NoteField[]; created_at: string; updated_at: string };

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, signal?: AbortSignal, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, signal, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) {
    let message = "The local API could not complete that request.";
    try {
      const body = (await response.json()) as { error?: { message?: string } };
      message = body.error?.message ?? message;
    } catch {
      // Keep the safe generic message when the server response is not JSON.
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  health: (signal?: AbortSignal) => request<Health>("/api/v1/health", signal),
  sessions: (signal?: AbortSignal) => request<Session[]>("/api/v1/sessions", signal),
  createSession: (payload: { pseudonym_id: string; session_type: Session["session_type"]; consent: Record<string, string | boolean> }) =>
    request<Session>("/api/v1/sessions", undefined, { method: "POST", body: JSON.stringify(payload) }),
  session: (id: string, signal?: AbortSignal) => request<Session>(`/api/v1/sessions/${id}`, signal),
  transcript: (id: string, signal?: AbortSignal) =>
    request<TranscriptSegment[]>(`/api/v1/sessions/${id}/transcript`, signal),
  evidence: (id: string, query: string, signal?: AbortSignal) =>
    request<EvidenceHit[]>(
      `/api/v1/sessions/${id}/evidence?query=${encodeURIComponent(query)}`,
      signal,
    ),
  updateSegment: (sessionId: string, segmentId: string, payload: { text: string; speaker_role: TranscriptSegment["speaker_role"] }) =>
    request<TranscriptSegment>(`/api/v1/sessions/${sessionId}/transcript/${segmentId}`, undefined, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  noteDraft: (sessionId: string) => request<NoteDraft>(`/api/v1/sessions/${sessionId}/note-draft`),
  saveNoteDraft: (sessionId: string, fields: NoteField[]) =>
    request<NoteDraft>(`/api/v1/sessions/${sessionId}/note-draft`, undefined, {
      method: "PUT",
      body: JSON.stringify({ fields }),
    }),
};

export function formatTimestamp(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

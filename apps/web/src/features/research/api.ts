import { request, type TranscriptSegment, type EvidenceHit } from '../../lib/api';
export type Judgment = { grade: number; reviewer: string; rationale: string };
export type ResearchQuery = { query_id: string; conversation_id: string; text: string; intent?: string; draft: boolean; no_answer_confirmed: boolean; judgments: Record<string, Judgment>; progress: { reviewed: number; total: number; complete: boolean; needs_no_answer_confirmation: boolean } };
export type Conversation = { conversation_id: string; split: string; segments: TranscriptSegment[]; asr_segments?: TranscriptSegment[]; source_files?: Record<string, string> };
export type Dataset = { dataset_id: string; version: string; description: string; revision: number; selection: { seed: number; pilot_ids: string[]; identity_limit: string; target_queries: number }; conversations: { conversation_id: string; split: string; turns: number; pilot: boolean; queries: number; complete_queries: number }[] };
export type Detail = { conversation: Conversation; revision: number; queries: ResearchQuery[]; explored: boolean };
export type Job = { run_id: string; dataset_id?: string; split?: string; status: string; query_count?: number; annotation_status?: string; error?: string; dataset_version?: string };
export type MethodResult = { method: string; variant: string; status: string; reason?: string; quality?: Record<string, number | null>; latency_ms?: Record<string, number | null>; memory?: Record<string, number | null>; queries?: { query_id: string; ranked_source_ids: string[]; scores: number[] }[] };
export type Report = { result_kind: string; dataset_description: string; dataset_sha256: string; dataset_version?: string; split: string; query_count: number; annotation_status: string; settings: { context_turns?: number } & Record<string, unknown>; calibration?: Record<string, unknown>; results: MethodResult[]; evaluation_dataset: { dataset_id?: string; conversations: Conversation[]; queries: { query_id: string; conversation_id: string; text: string; intent?: string }[]; judgments: { query_id: string; source_segment_id: string; grade: number; reviewer: string; rationale: string }[] }; asr: Record<string, unknown> };
const base = '/api/v1/research';
const location = (name: string, cid: string) => `${base}/datasets/${encodeURIComponent(name)}/conversations/${encodeURIComponent(cid)}`;
const post = <T>(path: string, payload: unknown, method = 'POST') => request<T>(path, undefined, { method, body: JSON.stringify(payload) });
export const research = {
  datasets: () => request<Dataset[]>(`${base}/datasets`),
  conversation: (name: string, cid: string, signal?: AbortSignal) => request<Detail>(location(name, cid), signal),
  query: (name: string, cid: string, payload: unknown) => post<Detail>(`${location(name, cid)}/queries`, payload, 'PUT'),
  grade: (name: string, cid: string, qid: string, sid: string, payload: unknown) => post<Detail>(`${location(name, cid)}/queries/${encodeURIComponent(qid)}/turns/${encodeURIComponent(sid)}`, payload, 'PUT'),
  expand: (name: string, cid: string, revision: number) => post<Dataset>(`${location(name, cid)}/include`, { revision }),
  explore: (name: string, cid: string, payload: unknown) => post<{ hits: EvidenceHit[]; revision: number }>(`${location(name, cid)}/explore`, payload),
  export: (name: string, split: string) => request<unknown>(`${base}/datasets/${encodeURIComponent(name)}/export?split=${split}`),
  run: (name: string, payload: unknown) => post<Job>(`${base}/datasets/${encodeURIComponent(name)}/runs`, payload),
  runs: () => request<Job[]>(`${base}/runs`),
  importReport: (report: unknown) => post<Job>(`${base}/runs/import`, report),
  result: (id: string) => request<{ job: Job; report: Report | null }>(`${base}/runs/${encodeURIComponent(id)}`),
};
export function download(name: string, content: string, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const methods = ['fixture', 'tfidf', 'bm25', 'e5', 'hybrid'];

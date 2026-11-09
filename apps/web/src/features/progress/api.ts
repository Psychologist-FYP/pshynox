import { request } from "../../lib/api";

export type Provenance = "AI_GENERATED" | "MANUAL" | "RETRIEVAL_CANDIDATE" | "TEMPLATE";
export type ReviewStatus = "pending" | "accepted" | "edited" | "rejected" | "stale";
export type DomainStatus = "rated" | "explicit_denial" | "conflicting" | "insufficient_evidence";
export type PredictionLabel = "likely" | "possible" | "unlikely" | "insufficient_evidence";

export type Cited = { segment_id: string; session_id: string; start_ms: number; end_ms: number; speaker_role: "CLIENT" | "CLINICIAN" | "UNKNOWN"; basis: string; text?: string | null; recording_id?: string | null; revision?: number | null };
export type ReviewInfo = { provenance: Provenance; review_status: ReviewStatus; reviewer_name?: string | null; reviewed_at?: string | null; reviewer_comment?: string | null; original_ai?: Record<string, unknown> | null; extraction_job_id?: string | null; locked: boolean; created_at: string; updated_at: string };
export type DomainObservation = ReviewInfo & { id: string; patient_id: string; session_id: string; domain_key: string; registry_version: string; status_value: DomainStatus; rating: number | null; direction: string | null; timeframe: "current" | "past" | "unclear"; frequency_text: string; intensity_text: string; duration_text: string; basis: string; evidence: Cited[]; alternatives: Record<string, unknown>[]; history: Record<string, unknown>[] };
export type ItemScore = { item_id: string; score: number | null; evidence_segment_ids: string[]; basis: string; status: string };
export type InstrumentScore = ReviewInfo & { id: string; patient_id: string; session_id: string | null; instrument_key: string; administered_at: string | null; items: ItemScore[]; total: number | null; total_is_partial: boolean; subtotals: Record<string, { total: number | null; partial: boolean; band: string | null }>; band: string | null; evidence: Cited[] };
export type Finding = { id: string; criterion_id: string; summary: string; status: "met" | "not_met" | "insufficient_evidence" | "conflicting"; evidence: Cited[]; counter_evidence: Cited[]; timeframe: string; duration_note: string };
export type Prediction = ReviewInfo & { id: string; patient_id: string; as_of_session_id: string; condition_key: string; condition_label: string; subtype_key: string | null; prediction: PredictionLabel; probability: number | null; probability_note: string | null; criteria_met_count: number; criteria_required: number; duration_status: "met" | "not_met" | "unknown"; impairment_status: "met" | "not_met" | "unknown"; rule_outs: { consideration?: string; key?: string; status?: string; evidence?: string[]; segment_ids?: string[] }[]; differentials: { condition: string; why_considered?: string; distinguishing_present?: string[]; distinguishing_missing?: string[] }[]; missing_information: string[]; rationale: { sentence: string; segment_ids: string[] }[]; postprocess: { notes?: string[]; removed_sentences?: string[]; model_label?: string; model_subtype?: string | null }; findings: Finding[]; model_version: string; prompt_version: string; registry_version: string };
export type RiskStatement = ReviewInfo & { id: string; patient_id: string; session_id: string; category: "self_harm" | "suicidal_ideation" | "harm_to_others" | "other_safety"; basis: string; evidence: Cited[] };
export type PlanFollowup = ReviewInfo & { id: string; origin_session_id: string; plan_text: string; followup_session_id: string; status: string; basis: string; evidence: Cited[] };
export type ChangeEvent = { id: string; measure_kind: "instrument" | "domain" | "prediction"; measure_key: string; series: string; from_session_id: string; to_session_id: string; delta: number | null; from_label: string | null; to_label: string | null; rule_id: string; provenance_of_inputs: "ai" | "reviewed" | "mixed"; acknowledged_by: string | null; acknowledged_at: string | null; comment: string | null; created_at: string };
export type SeriesPoint = { slot: string; value: number | null; raw_value: number | null; label: string | null; probability: number | null; status: string | null; provenance: Provenance | null; review_status: ReviewStatus | null; record_id: string | null; gap_reason: string | null; date?: string; partial?: boolean; total?: number | null; items?: Record<string, number | null>; session_id?: string | null; reviewer_name?: string | null; rating?: number | null; timeframe?: string; direction?: string | null; frequency_text?: string; intensity_text?: string; duration_text?: string; prediction?: string; subtype_key?: string | null; criteria_met_count?: number; criteria_required?: number };
export type InstrumentSeries = { series: "administered" | "ai_estimate"; points: SeriesPoint[]; summary: { sessions_assessed: number; sessions_total: number; delta_baseline: number | null }; trajectory: string | null; changes: number };
export type InstrumentBlock = { instrument_key: string; label: string; metric: string | null; metric_label: string; metric_max: number; bands: { min: number; max: number; label: string }[]; reliable_change_threshold: number | null; series: InstrumentSeries[]; items: { id: string; label: string; clinician_only: boolean }[] };
export type DomainRow = { domain_key: string; label: string; group: string; directions: string[]; cells: SeriesPoint[]; summary: { sessions_assessed: number; sessions_total: number }; changes: number };
export type ProgressSession = { id: string; index: number; created_at: string; session_type: string; note_status: string; research_demo: boolean };
export type PatientProgress = { patient_id: string; sessions: ProgressSession[]; versions: Record<string, string>; pending_review_counts: Record<string, number>; instruments: InstrumentBlock[]; domains: DomainRow[]; domain_groups: { key: string; label: string }[]; change_events: ChangeEvent[]; predictions: Prediction[]; prediction_timeline: { condition_key: string; label: string; points: SeriesPoint[] }[]; risk_statements: RiskStatement[]; plan_followups: PlanFollowup[]; instrument_scores: InstrumentScore[] };
export type ReviewPayload = { actor: string; action: "accept" | "edit" | "reject"; value?: Record<string, unknown>; comment?: string; reason?: string; expected_updated_at?: string };
export type ProgressJob = { id: string; status: "running" | "completed" | "failed" | "cancelled" | "interrupted"; stage: string; error?: string | null; limitations?: string[]; session_id: string; patient_id: string; created_at: string; metrics_summary?: Record<string, unknown> };

const json = (body: unknown, method = "POST"): RequestInit => ({ method, body: JSON.stringify(body) });

export const progressApi = {
  progress: (pid: string) => request<PatientProgress>(`/api/v1/patients/${pid}/progress`),
  observations: (sid: string) => request<DomainObservation[]>(`/api/v1/sessions/${sid}/domain-observations`),
  putObservation: (sid: string, domain: string, body: Record<string, unknown>) => request<DomainObservation>(`/api/v1/sessions/${sid}/domain-observations/${domain}`, undefined, json(body, "PUT")),
  reviewObservation: (sid: string, id: string, body: ReviewPayload) => request<DomainObservation>(`/api/v1/sessions/${sid}/domain-observations/${id}/review`, undefined, json(body)),
  scores: (pid: string) => request<InstrumentScore[]>(`/api/v1/patients/${pid}/instrument-scores`),
  addScore: (pid: string, body: Record<string, unknown>) => request<InstrumentScore>(`/api/v1/patients/${pid}/instrument-scores`, undefined, json(body)),
  reviewScore: (pid: string, id: string, body: ReviewPayload) => request<InstrumentScore>(`/api/v1/patients/${pid}/instrument-scores/${id}/review`, undefined, json(body)),
  prediction: (pid: string, id: string) => request<Prediction>(`/api/v1/patients/${pid}/condition-predictions/${id}`),
  reviewPrediction: (pid: string, id: string, body: ReviewPayload) => request<Prediction>(`/api/v1/patients/${pid}/condition-predictions/${id}/review`, undefined, json(body)),
  runPredictions: (pid: string, body: { as_of_session_id: string; condition_keys?: string[] }) => request<{ job_id: string }>(`/api/v1/patients/${pid}/condition-predictions`, undefined, json(body)),
  reviewRisk: (pid: string, id: string, body: ReviewPayload) => request<RiskStatement>(`/api/v1/patients/${pid}/risk-statements/${id}/review`, undefined, json(body)),
  reviewPlan: (pid: string, id: string, body: ReviewPayload) => request<PlanFollowup>(`/api/v1/patients/${pid}/plan-followups/${id}/review`, undefined, json(body)),
  acknowledge: (pid: string, id: string, body: { actor: string; comment?: string }) => request<ChangeEvent>(`/api/v1/patients/${pid}/change-events/${id}/acknowledge`, undefined, json(body)),
  startExtraction: (sid: string, body: { transcript_reviewed: boolean; evidence_mode?: string }) => request<{ job_id: string }>(`/api/v1/sessions/${sid}/progress-extraction`, undefined, json(body)),
  sessionJobs: (sid: string) => request<ProgressJob[]>(`/api/v1/sessions/${sid}/progress-jobs`),
  job: (id: string) => request<ProgressJob>(`/api/v1/progress-jobs/${id}`),
  cancelJob: (id: string) => request<{ cancel_requested: boolean }>(`/api/v1/progress-jobs/${id}/cancel`, undefined, { method: "POST" }),
  retryJob: (id: string) => request<{ job_id: string }>(`/api/v1/progress-jobs/${id}/retry`, undefined, { method: "POST" }),
  registry: () => request<{ versions: Record<string, string>; domains: { domains: { key: string; label: string; group: string; directions: string[] }[]; groups: { key: string; label: string }[]; rating_scale: Record<string, string> }; instruments: { instruments: { key: string; label: string; items: { id: string; label: string; clinician_only: boolean }[] }[] } }>(`/api/v1/progress/registry`),
};

export const DOMAIN_STATUS_LABEL: Record<DomainStatus, string> = {
  rated: "Rated", explicit_denial: "Explicitly denied", conflicting: "Conflicting accounts", insufficient_evidence: "Insufficient evidence",
};
export const PREDICTION_LABEL: Record<PredictionLabel, string> = {
  likely: "Likely", possible: "Possible", unlikely: "Unlikely", insufficient_evidence: "Insufficient evidence",
};
export const SUBTYPE_LABEL: Record<string, string> = {
  bipolar_i_pattern: "Bipolar I pattern", bipolar_ii_pattern: "Bipolar II pattern", cyclothymic_pattern: "Cyclothymic pattern",
  depressive_features_only: "Depressive features only — bipolarity not assessable",
};
export const RISK_LABEL: Record<RiskStatement["category"], string> = {
  self_harm: "Self-harm", suicidal_ideation: "Suicidal thoughts", harm_to_others: "Harm to others", other_safety: "Other safety concern",
};

export function audioUrl(c: Cited): string | null {
  return c.recording_id ? `/api/v1/sessions/${c.session_id}/recordings/${c.recording_id}/audio` : null;
}

let player: HTMLAudioElement | null = null;
export function playCitation(c: Cited) {
  const url = audioUrl(c);
  if (!url) return;
  player?.pause();
  player = new Audio(url);
  player.addEventListener("loadedmetadata", () => { if (player) { player.currentTime = c.start_ms / 1000; void player.play(); } }, { once: true });
  player.load();
}

const ACTOR_KEY = "pshynox.progress.reviewer";
export function savedReviewer(): string {
  try { return localStorage.getItem(ACTOR_KEY) ?? ""; } catch { return ""; }
}
export function rememberReviewer(name: string) {
  try { localStorage.setItem(ACTOR_KEY, name); } catch { /* reviewer name is a convenience only */ }
}

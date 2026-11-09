import { request } from "../../lib/api";

export type Provenance = "AI_GENERATED" | "MANUAL" | "RETRIEVAL_CANDIDATE" | "TEMPLATE";
export type ReviewStatus = "pending" | "accepted" | "edited" | "rejected" | "stale";
export type DomainStatus = "rated" | "explicit_denial" | "conflicting" | "insufficient_evidence";
export type PredictionLabel = "likely" | "possible" | "unlikely" | "insufficient_evidence" | "not_met";
export type DsmLabel = "likely" | "possible" | "insufficient_evidence" | "not_met";
export type DsmStatus = "present" | "subthreshold" | "absent" | "explicit_denial" | "insufficient_evidence" | "conflicting";

export type Cited = { segment_id: string; session_id: string; start_ms: number; end_ms: number; speaker_role: "CLIENT" | "CLINICIAN" | "UNKNOWN"; basis: string; text?: string | null; recording_id?: string | null; revision?: number | null };
export type ReviewInfo = { provenance: Provenance; review_status: ReviewStatus; reviewer_name?: string | null; reviewed_at?: string | null; reviewer_comment?: string | null; original_ai?: Record<string, unknown> | null; extraction_job_id?: string | null; locked: boolean; created_at: string; updated_at: string };
export type DomainObservation = ReviewInfo & { id: string; patient_id: string; session_id: string; domain_key: string; registry_version: string; status_value: DomainStatus; rating: number | null; direction: string | null; timeframe: "current" | "past" | "unclear"; frequency_text: string; intensity_text: string; duration_text: string; basis: string; evidence: Cited[]; alternatives: Record<string, unknown>[]; history: Record<string, unknown>[] };
export type ItemScore = { item_id: string; score: number | null; evidence_segment_ids: string[]; basis: string; status: string };
export type InstrumentScore = ReviewInfo & { id: string; patient_id: string; session_id: string | null; instrument_key: string; administered_at: string | null; items: ItemScore[]; total: number | null; total_is_partial: boolean; subtotals: Record<string, { total: number | null; partial: boolean; band: string | null }>; band: string | null; evidence: Cited[] };
export type Finding = { id: string; criterion_id: string; summary: string; status: "met" | "not_met" | "insufficient_evidence" | "conflicting"; evidence: Cited[]; counter_evidence: Cited[]; timeframe: string; duration_note: string };
export type Prediction = ReviewInfo & { id: string; patient_id: string; as_of_session_id: string; condition_key: string; condition_label: string; subtype_key: string | null; prediction: PredictionLabel; probability: number | null; probability_note: string | null; criteria_met_count: number; criteria_required: number; duration_status: "met" | "not_met" | "unknown"; impairment_status: "met" | "not_met" | "unknown"; rule_outs: { consideration?: string; key?: string; status?: string; evidence?: string[]; segment_ids?: string[] }[]; differentials: { condition: string; why_considered?: string; distinguishing_present?: string[]; distinguishing_missing?: string[] }[]; missing_information: string[]; rationale: { sentence: string; segment_ids: string[] }[]; postprocess: { notes?: string[]; removed_sentences?: string[]; model_label?: string; model_subtype?: string | null }; findings: Finding[]; model_version: string; prompt_version: string; registry_version: string };
export type RiskCategory = "self_harm" | "suicidal_ideation" | "harm_to_others" | "other_safety" | "suicidal_ideation_passive" | "suicidal_ideation_active" | "self_harm_past" | "self_harm_recent" | "protective_factor" | "denial_of_plan_or_intent";
export type RiskStatement = ReviewInfo & { id: string; patient_id: string; session_id: string; category: RiskCategory; categories?: RiskCategory[]; note_kind?: "flag" | "supporting"; registry_kind?: "v1" | "dsm"; basis: string; evidence: Cited[] };
export type PlanFollowup = ReviewInfo & { id: string; origin_session_id: string; plan_text: string; followup_session_id: string; status: string; basis: string; evidence: Cited[] };
export type ChangeEvent = { id: string; measure_kind: "instrument" | "domain" | "prediction"; measure_key: string; series: string; from_session_id: string; to_session_id: string; delta: number | null; from_label: string | null; to_label: string | null; rule_id: string; provenance_of_inputs: "ai" | "reviewed" | "mixed"; acknowledged_by: string | null; acknowledged_at: string | null; comment: string | null; created_at: string };
export type SeriesPoint = { slot: string; value: number | null; raw_value: number | null; label: string | null; probability: number | null; status: string | null; provenance: Provenance | null; review_status: ReviewStatus | null; record_id: string | null; gap_reason: string | null; date?: string; partial?: boolean; total?: number | null; items?: Record<string, number | null>; session_id?: string | null; reviewer_name?: string | null; rating?: number | null; timeframe?: string; direction?: string | null; frequency_text?: string; intensity_text?: string; duration_text?: string; prediction?: string; subtype_key?: string | null; criteria_met_count?: number; criteria_required?: number };
export type InstrumentSeries = { series: "administered" | "ai_estimate"; points: SeriesPoint[]; summary: { sessions_assessed: number; sessions_total: number; delta_baseline: number | null }; trajectory: string | null; changes: number };
export type InstrumentBlock = { instrument_key: string; label: string; metric: string | null; metric_label: string; metric_max: number; bands: { min: number; max: number; label: string }[]; reliable_change_threshold: number | null; series: InstrumentSeries[]; items: { id: string; label: string; clinician_only: boolean }[]; item_min?: number; item_max?: number; clinician_only?: boolean; reporting_period?: string; licence_note?: string; followup_domains?: { domain: string; label: string; items: string[]; threshold: number }[] };
export type DomainRow = { domain_key: string; label: string; group: string; directions: string[]; cells: SeriesPoint[]; summary: { sessions_assessed: number; sessions_total: number }; changes: number };
export type ProgressSession = { id: string; index: number; created_at: string; session_type: string; note_status: string; research_demo: boolean };
export type DsmCited = Cited & { kind?: "denial" | "alternative" };
export type DsmItemResult = { item: string; status: DsmStatus | "not_linked"; note: string; frequency?: string; paraphrase?: string; evidence: DsmCited[]; counter_evidence: DsmCited[] };
export type DsmElement = { id: string; type: string; label: string; status: "met" | "not_met" | "unknown" | "partial" | "contradicted" | "info"; detail: string; line: string; ask: string; items: DsmItemResult[]; evidence: DsmCited[]; counter_evidence: DsmCited[] };
export type DsmPrediction = ReviewInfo & { id: string; patient_id: string; as_of_session_id: string; condition_key: string; condition_label: string; role: string; label: DsmLabel; label_text: string; footer: string; period: { id?: string; label?: string; time_frame?: string; duration_days?: number | null; hospitalised?: boolean }; elements: DsmElement[]; specifiers: { key: string; label: string; basis: string; code: string }[]; codes: { icd10cm?: string; icd11?: string; suggested?: string }; differentials: { label: string; why: string }[]; missing_information: string[]; periods: { id: string; label: string; time_frame: string; duration_days: number | null; features: string[] }[]; criteria_met_count: number; criteria_required: number; postprocess: { model_label?: string | null; notes?: string[] }; model_version: string; prompt_version: string; registry_version: string; batch_id?: string | null };
export type DsmCell = { session_id: string; status: DsmStatus | "not_linked" | "stale" | null; note?: string; frequency?: string | null; qualifier?: string | null; time_frame?: string | null; record_id?: string | null; finding_ids?: string[]; provenance: Provenance | null; review_status: ReviewStatus | null; reviewer_name: string | null };
export type DsmGridRow = { id: string; label: string; paraphrase: string; disorder: string; criterion: string; kind: string; letter: string; verification: "pending" | "verified" | "changed"; features: string[]; model_assessed: boolean; cells: DsmCell[] };
export type DsmFeatureRow = { key: string; label: string; group: string; linked_items: string[]; cells: DsmCell[] };
export type DsmBatch = { id: string; kind: string; status: string; created_at: string; job_id: string | null; session_ids: string[]; skipped: { session_id: string; reason: string }[]; reverted_at: string | null; reverted_by: string | null };
export type DsmBlock = { registry_version: string; label_footer: string; verification_summary: Record<string, number>; verification_note: string; grid: { key: string; label: string; rows: DsmGridRow[] }[]; features: DsmFeatureRow[]; feature_groups: { key: string; label: string }[]; predictions: DsmPrediction[]; timeline: { condition_key: string; label: string; role: string; points: SeriesPoint[] }[]; pending: Record<string, number>; has_findings: boolean; batches: DsmBatch[] };
export type DsmFinding = ReviewInfo & { id: string; patient_id: string; session_id: string; feature_key: string; feature_label: string; group: string; registry_version: string; status: DsmStatus; frequency: string; onset_or_duration_text: string; time_frame: string; period_label: string; qualifier: string | null; degree: string | null; contexts: string[]; linked_items: string[]; basis: string; clinician_intensity: number | null; evidence: Cited[]; counter_evidence: DsmCited[]; counter_check: { searched?: boolean; alternative_explanation?: string }; batch_id: string | null; alternatives: Record<string, unknown>[]; history: Record<string, unknown>[] };
export type DsmRegistryView = { active: "v1" | "dsm"; version: string; label_footer: string; note: string; rows: { entity_kind: string; id: string; label: string; text: string; chapter: string; icd10cm: string; icd11: string; content_sha256: string; current_status: string; verified_by: string; verified_on: string; comment: string }[]; items: { id: string; label: string; paraphrase: string; disorder: string; criterion: string; kind: string; chapter: string; notes: string; verification: { status: string; verified_by?: string | null; verified_on?: string | null; comment?: string } }[]; features: { key: string; label: string; group: string; definition: string; qualifiers: string[]; degree: boolean; separate_from: { feature: string; reason: string }[]; verification: { status: string } }[]; conditions: { key: string; label: string; role: string; chapter: string; icd10cm: string; icd11: string; rule_summary: string; verification: { status: string } }[]; contexts: string[]; frequencies: string[]; statuses: DsmStatus[]; feature_groups: { key: string; label: string }[] };
export type PatientProgress = { registry_kind?: "v1" | "dsm"; dsm?: DsmBlock | null; patient_id: string; sessions: ProgressSession[]; versions: Record<string, string>; pending_review_counts: Record<string, number>; instruments: InstrumentBlock[]; domains: DomainRow[]; domain_groups: { key: string; label: string }[]; change_events: ChangeEvent[]; predictions: Prediction[]; prediction_timeline: { condition_key: string; label: string; points: SeriesPoint[] }[]; risk_statements: RiskStatement[]; plan_followups: PlanFollowup[]; instrument_scores: InstrumentScore[] };
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
  dsmRegistry: () => request<DsmRegistryView>(`/api/v1/progress/registry/dsm`),
  importRegistryReview: (body: { actor: string; csv: string }) => request<{ import_batch_id: string; recorded: number; rejected: { line: number; entity_kind: string; id: string; reason: string }[] }>(`/api/v1/progress/registry/dsm/verifications/import`, undefined, json(body)),
  dsmFindings: (sid: string) => request<DsmFinding[]>(`/api/v1/sessions/${sid}/dsm-findings`),
  putDsmFinding: (sid: string, key: string, body: Record<string, unknown>) => request<DsmFinding>(`/api/v1/sessions/${sid}/dsm-findings/${key}`, undefined, json(body, "PUT")),
  reviewDsmFinding: (sid: string, id: string, body: ReviewPayload) => request<DsmFinding>(`/api/v1/sessions/${sid}/dsm-findings/${id}/review`, undefined, json(body)),
  dsmPrediction: (pid: string, id: string) => request<DsmPrediction>(`/api/v1/patients/${pid}/dsm-predictions/${id}`),
  reviewDsmPrediction: (pid: string, id: string, body: ReviewPayload) => request<DsmPrediction>(`/api/v1/patients/${pid}/dsm-predictions/${id}/review`, undefined, json(body)),
  startReanalysis: (pid: string, body: { transcript_reviewed: boolean; session_ids?: string[] }) => request<{ job_id: string }>(`/api/v1/patients/${pid}/dsm-reanalysis`, undefined, json(body)),
  revertBatch: (pid: string, id: string, body: { actor: string }) => request<{ batch_id: string; deactivated: number; restored: number }>(`/api/v1/patients/${pid}/dsm-batches/${id}/revert`, undefined, json(body)),
  registry: () => request<{ active?: "v1" | "dsm"; dsm_version?: string; versions: Record<string, string>; domains: { domains: { key: string; label: string; group: string; directions: string[] }[]; groups: { key: string; label: string }[]; rating_scale: Record<string, string> }; instruments: { instruments: { key: string; label: string; items: { id: string; label: string; clinician_only: boolean }[] }[] } }>(`/api/v1/progress/registry`),
};

export const DOMAIN_STATUS_LABEL: Record<DomainStatus, string> = {
  rated: "Rated", explicit_denial: "Explicitly denied", conflicting: "Conflicting accounts", insufficient_evidence: "Insufficient evidence",
};
export const PREDICTION_LABEL: Record<PredictionLabel, string> = {
  likely: "Likely", possible: "Possible", unlikely: "Unlikely", insufficient_evidence: "Insufficient evidence", not_met: "Not met",
};
export const DSM_LABEL: Record<DsmLabel, string> = { likely: "Likely", possible: "Possible", insufficient_evidence: "Insufficient evidence", not_met: "Not met" };
export const DSM_STATUS: Record<string, { icon: string; text: string }> = {
  present: { icon: "●", text: "Present" }, subthreshold: { icon: "◐", text: "Subthreshold" }, absent: { icon: "○", text: "Absent" },
  explicit_denial: { icon: "✕", text: "Denied" }, insufficient_evidence: { icon: "?", text: "Insufficient" }, conflicting: { icon: "⇄", text: "Conflicting" },
  not_linked: { icon: "◌", text: "Not linked" }, stale: { icon: "!", text: "Sources changed" },
};
export const REGISTRY_EXPORT = { csv: "/api/v1/progress/registry/dsm/export.csv", html: "/api/v1/progress/registry/dsm/export.html" };
export const SUBTYPE_LABEL: Record<string, string> = {
  bipolar_i_pattern: "Bipolar I pattern", bipolar_ii_pattern: "Bipolar II pattern", cyclothymic_pattern: "Cyclothymic pattern",
  depressive_features_only: "Depressive features only — bipolarity not assessable",
};
export const RISK_LABEL: Record<RiskCategory, string> = {
  self_harm: "Self-harm", suicidal_ideation: "Suicidal thoughts", harm_to_others: "Harm to others", other_safety: "Other safety concern",
  suicidal_ideation_passive: "Passive suicidal thoughts", suicidal_ideation_active: "Active suicidal thoughts", self_harm_past: "Past self-harm",
  self_harm_recent: "Recent or current self-harm", protective_factor: "Protective factor", denial_of_plan_or_intent: "Denial of plan or intent",
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

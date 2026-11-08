# Progress Monitoring and Evidence-Cited Condition Prediction — Implementation Plan

**Status (8 November 2026):** **all phases (0–6) built.** The plan was approved on 7 November 2026 with decisions
Q1–Q3 (§13) and a revised Phase 5 execution model (§8.5). The full-transcript benchmark arms were dropped by the user
on 8 November. Optional user-run evaluation steps (reports, freeze, calibration, final run) and the vignette cases
remain; see the Phase 6 entry in §15.
**Branch:** `progress-monitoring` (single-commit rewrite of the earlier `feat/progress-monitoring`, based on `main` at `d4f4cb8`, see §1.1)
**Prepared:** 7 November 2026
**Persistent rules:** the "Progress monitoring module" section of the root [`CLAUDE.md`](../CLAUDE.md).
Every session must read that section and this whole document before starting a phase.

This document holds everything a fresh session needs to carry out any phase: data model,
endpoints, UI routes, extraction/prediction schemas, evaluation protocol, file list,
open questions and a progress log (§15). Section numbers in the form "brief §N" refer to
the original task prompt, which `CLAUDE.md` summarises.

---

## 1. Scope change (for the team, supervisor and clinical consultant)

**This module changes the agreed project scope.** The current documents describe Pshynox as
documentation support that does **not** diagnose. This module adds AI-generated,
criterion-level condition predictions. Before the final report, the team must update the
proposal/report and inform the supervisor and Ms. Kinza Nazir (clinical consultant).

| Existing statement | Where | Conflict introduced by this module |
|---|---|---|
| "The system will not identify bipolar disorder from a recording or independently classify a manic or depressive episode." | Revised proposal §4.1 (`docs/FYP_progresss1.pdf`, p. 6) | Stage B predicts bipolar I/II/cyclothymic *patterns* and manic/hypomanic/depressive episode criteria from transcripts. |
| "Autonomous diagnosis … excluded." | Proposal §4.4 | Predictions are produced automatically. They are labelled as AI hypotheses that need clinician review, but the computation does not wait for review. |
| "Separate depression … modules … deferred" | Proposal Table 2 | A depression / major depressive episode track is added. It is the only track the available benchmark can evaluate. |
| "The dashboard will organize **reviewed** observations" | Proposal §8; objective 4 ("Compare reviewed symptom and functioning records") | Under principle 3, AI values pending review are charted and trended at full weight, with a badge. |
| "It does not diagnose or recommend treatment." | `README.md` line 8 | Must be reworded. |
| "It does not implement automated diagnosis" | `docs/PROJECT_STATUS.md` §1 | Needs a dated addendum (Phase 6). |
| `description="Offline documentation support API. Not a diagnostic system."` | `services/api/main.py:100` | Needs rewording in Phase 6 (wording to be agreed). |
| LongCounsel-8 terms: "Models trained on it must not be used for diagnosis, autonomous triage, treatment decisions, or other decisions about a person's care." | `data/research/datasets/longcounsel-8/raw/README.md` | A calibration mapping fitted on LongCounsel-8 is a model trained on it. Applying it to clinical patients' depression predictions is likely to conflict with these terms. See **Q3**. |
| Proposal §7 risk review: "A risk-review state is a workflow status, not a predicted risk level." | Proposal §7 | **Unchanged.** Risk statements are flagged with citations, never scored (principle 7). |

**Phase 6 (8 November 2026):** draft wording is now in the API description, the workspace footer, the landing page,
the README and a dated PROJECT_STATUS addendum: "AI condition hypotheses are for clinician review, not diagnoses; no risk
scoring or treatment recommendations." The supervisor and consultant still need to agree it.

What stays the same: the clinician remains the decision-maker. Review status is always
visible. Nothing is approved automatically. The manual risk-review gate on notes is unchanged.
Everything runs locally. The team should record the consultant's agreement, or any
restrictions, in this section before Phase 4 (condition prediction) is merged.

---

## 1.1 Baseline and environment findings (Phase 0)

| Item | Finding |
|---|---|
| Git state | Untracked `PSHYNOX_PROJECT_REPORT.md` (a context report written 6 Nov, not code, not mine). **Left untouched and never staged.** No other changes. |
| Base branch | The brief says to branch from `annotation`. Checkout was `main`. `git log main..annotation` is empty (annotation is fully merged into `main` via PR #10). The branch was created from `main` at `d4f4cb8`, which has the same content plus the merge commit. |
| Backend baseline | `.venv/bin/python -m pytest -q` → **201 passed in 26.70 s** (Python 3.14.8). The TestClient stall recorded in PROJECT_STATUS §7 did **not** occur here. |
| Frontend baseline | `npm run build --prefix apps/web` → passed (JS 358.64 kB, CSS 51.72 kB). |
| Plan mode | Phase 0 was done as read-only exploration. The plan-mode tool was not entered because it blocks writing this file. The only files written are this document and `CLAUDE.md`. |
| LongCounsel-8 | **Present** at `data/research/datasets/longcounsel-8/raw/`: `README.md`, `LICENSE` (CC BY-NC 4.0), `manifest.json` (schema v3), three editions as `data.tar.zst` plus `train_val_test_split.json`. Primary edition `longcounsel-8-gpt-5.4-mini-low`: 369 trajectories, split 258/37/74, profile-disjoint (`profile_disjoint_split: true`). Nothing was extracted to disk; files were only listed and streamed for schema inspection (one *train* trajectory). |
| LC8 transcript fields | The release README and manifest say `transcript.jsonl` rows contain **exactly** `session_id` (1–5), `turn_id` (1–20), `speaker` (`Counselor`/`Client`), `text`, `message_id`, which matches what was observed. **The brief's statement that hidden state/trend metadata repeats on every row does not match this release.** The leakage guard is still built as an allowlist with tests, because other editions or future releases might differ. |
| LC8 labels | `ground_truth.json` → `sessions[].ground_truth_score.{symptom_scores{anhedonia, depressed_mood, sleep, fatigue, appetite, self_worth, concentration, psychomotor}, overall_score, severity_band}` (floats: latent values). `self_report.json` → per session `self_report_score` aggregate (floats) plus 5 integer `samples`, and `captured_at` (a generation timestamp, not a clinical date). |
| LC8 trajectory type | **Not released as a field.** The split file has `trend_name` equal to the edition name, and the split rule says "per-parent-trend". `run_id` prefixes (`pdt`, `cbt`, `integrative`, …) look like therapy orientation, not trajectory type. See **Q6**. |
| Dependencies | `scikit-learn` 1.9.1 and `numpy` 2.5.4 are already declared or installed, enough for isotonic/Platt calibration, AUROC and bootstrap. PyYAML is installed only through the optional `dense` extra (huggingface_hub/transformers) and is **not** a core dependency. See **Q2**. |

---

## 2. Architecture and reuse map

```text
apps/web/src/features/progress/*         (UI, Recharts)            ── /api/v1 ──┐
services/api/progress_routes.py          (FastAPI router, persistence, review, audit, locks)
services/progress/
   registry/   (JSON registries + Pydantic loader, version + sha256)
   trends.py   (pure trend/change engine)
   store.py    (ORM <-> contracts, evidence resolution, evidence links, audit helper)
   evidence.py (domain/criterion retrieval packets; reuses EvidenceRetrievalProvider)
   prompts.py  (progress-extract-v1, condition-predict-v1)
   extraction.py / validation.py   (Stage A, DB-free: snapshot in -> result out)
   prediction.py / postprocess.py  (Stage B, DB-free core)
   jobs.py     (job runner: shared LOCK + memory_slot + Runtime + Recovery cache)
   staleness.py
   evaluation/ (LongCounsel-8, vignettes, metrics, calibration, run_final)
```

Reuse points (no second LLM runtime):

| Need | Reuse |
|---|---|
| llama-server process, `/apply-template` + `/tokenize` budgeting, finish-reason checks, truncation discard, metrics | `services/note_generation/runtime.py` `Runtime` (`generate()` rejects `output_limit` and malformed JSON; `fits()`/`count()` budget) |
| GPU/RAM exclusion | `services/note_generation/storage.py` `memory_slot()` (cross-process flock), **plus** the in-process `LOCK` from `services/api/note_generation_routes.py:18`, imported so that a note job and a progress job never run at the same time |
| Checkpoint cache and split-on-output-limit | `services/note_generation/recovery.py` `Recovery` (cache under `<jobs root>/cache-v4`, key = revision + model sha + version + schema + payload) with a group-splitting subclass modelled on `grouped.GroupRecovery` |
| Source aliases constrained by grammar | Same approach as `grouped.py:274` (`T0…` → original IDs, enum-constrained `refs` in the `json_schema`). Stage B uses patient-wide aliases (`A0…`). |
| Retrieval | `services/retrieval/engine.py` `EvidenceRetrievalProvider.search(query, session_id, segments, k, scope=SearchScope)` (BM25 default, context_turns=2). It is per-session, so Stage B queries each session and merges by rank. |
| Transcript-review acknowledgement, active-transcription/meeting guard | Same rules as `note_generation_routes.snapshot()` (lines 36–67) and `main.ensure_no_active_transcription` (line 699) |
| Staleness hook | `main.update_transcript_segment` (line 346): call `staleness.mark_stale(db, segment_id)` before the commit, next to the existing note `sources_stale` logic |
| Audit | `main._audit` (line 214) pattern; see **Q1** for patient-level rows |
| Tracing | `TerminalTrace`, which respects `NOTE_LOG_LEVEL` (off/events/full; patient text only at `full`) |
| Demo import | `services/api/import_annomi.py` pattern (SQLite backup first, no overwrite of existing rows) |
| Frozen final run | `services/retrieval/run_final.py` pattern (config-hash check, refuses to overwrite) |

Module rule (MODULE_INTEGRATION_GUIDE §1): the `services/progress` cores take plain
snapshots and return results. They do not write SQLite. Persistence happens only in
`services/api/progress_routes.py` and `services/progress/store.py`. The same cores
run on LongCounsel-8 research files without the database.

---

## 3. Registries (`services/progress/registry/`)

**Format: JSON**, validated by Pydantic. Using YAML would add a core dependency, see **Q2**;
if approved, the loader accepts `.yaml` without any change to the models. Files:

| File | Content |
|---|---|
| `instruments.json` | `phq9`, `gad7` (below) |
| `domains.json` | 20 session domains, groups, allowed directions, retrieval keywords (en / Roman Urdu / Urdu where known), mappings |
| `conditions.json` | Target conditions, criteria, requirements, sub-predictions, rule-outs, differentials |
| `loader.py` | Pydantic models, `load_registry()` → `{kind: (version, sha256, model)}`. The sha256 covers canonical JSON (sorted keys). It inserts or verifies `registry_versions` rows at startup. |

Each generated or stored value records `registry_version` (e.g. `domains@1.0.0+<sha8>`).

### 3.1 Instruments
- **phq9**: items 1–9, 0–3. `total` is 0–27. Bands: 0–4 minimal, 5–9 mild, 10–14 moderate, 15–19 moderately severe, 20–27 severe. `reliable_change_threshold: 5` (Löwe et al. 2004, *Medical Care* 42(12)). `ai_estimable_items: [1..8]`. Item 9 is `clinician_only: true`; contracts reject an AI value for it. A derived subtotal `phq8_total` (items 1–8, 0–24, same cut-points) is stored on every row (see **Q7**).
- **gad7**: items 1–7, 0–3, total 0–21, bands 0–4, 5–9, 10–14, 15–21. `reliable_change_threshold: null`, so no change markers until the consultant sets one.
- Extension point: `instruments.json` entries need `licence_confirmed: true` before they load. A mania/hypomania self-rating scale is documented as a placeholder only.

### 3.2 Domains (`domains.json`)
Groups: **depressive** (`low_mood`, `interest`, `sleep`, `appetite`, `energy`, `self_worth`, `concentration`, `psychomotor`), **manic/hypomanic** (`elevated_mood`, `irritability`, `racing_thoughts_pressured_speech`, `increased_goal_directed_activity_risky_behaviour`, `grandiosity`), **anxiety** (`worry`, `restlessness_tension`, `social_fear_avoidance`, `performance_fear`), **functioning** (`social_functioning`, `academic_work_functioning`, `self_care`).
Directions: `sleep` ∈ {reduced, increased, reduced_need_without_fatigue, disrupted}; `psychomotor` ∈ {slowed, restless}; `appetite` ∈ {reduced, increased}; others have none.

Mapping (one extraction feeds all three uses):

| Domain | PHQ item | GAD-7 item | Criteria (ids in conditions.json) |
|---|---|---|---|
| interest | 1 | – | `mde.anhedonia` |
| low_mood | 2 | – | `mde.depressed_mood` |
| sleep | 3 | – | `mde.sleep`; `mania.decreased_sleep_need` (direction `reduced_need_without_fatigue` only) |
| energy | 4 | – | `mde.fatigue` |
| appetite | 5 | – | `mde.appetite_weight` |
| self_worth | 6 | – | `mde.worthlessness_guilt` |
| concentration | 7 | – | `mde.concentration`; `gad.concentration`; `mania.distractibility` |
| psychomotor | 8 | 5 (restless) | `mde.psychomotor`; `gad.restlessness` |
| elevated_mood | – | – | `mania.elevated_mood` (A criterion) |
| irritability | – | 6 | `mania.irritable_mood` (A); `gad.irritability` |
| racing_thoughts_pressured_speech | – | – | `mania.pressured_speech`, `mania.racing_thoughts` |
| increased_goal_directed_activity_risky_behaviour | – | – | `mania.goal_directed_activity`, `mania.risky_activities` |
| grandiosity | – | – | `mania.grandiosity` |
| worry | – | 1, 2, 3, 7 | `gad.excessive_worry`, `gad.difficult_to_control` |
| restlessness_tension | – | 4, 5 | `gad.restlessness`, `gad.muscle_tension` |
| social_fear_avoidance | – | – | `sad.fear_of_scrutiny`, `sad.avoidance` |
| performance_fear | – | – | `sad.fear_of_scrutiny` (performance specifier) |
| social_functioning / academic_work_functioning / self_care | – | – | `*.impairment` for every condition |

(`gad.fatigue`, `gad.sleep` reuse `energy`/`sleep` evidence; `mde.suicidal_thoughts` is **not** a model criterion. It is covered by risk statements and clinician-entered PHQ-9 item 9.)

### 3.3 Conditions (`conditions.json`)
Each condition has `key`, `label`, `source: "DSM-5-TR, <section name>"`, `verification: "pending clinical consultant verification"`, paraphrased `criteria[]` (`id`, `summary` in our own words, `domains[]`, `evidence_types[]`, `core: bool`), `requirements` (`min_met`, `must_include_any_of`, `duration_days_min`, `duration_note`, `impairment_required`), `rule_outs[]`, `differentials[]` and `retrieval_queries[]`. No verbatim DSM/ICD text.

| Condition key | Requirement summary (paraphrased) | Sub-predictions |
|---|---|---|
| `depressive_episode` (MDE) | ≥5 of 9 symptoms, including low mood or loss of interest; ≥14 days; impairment; rule-outs: substance/medication, medical condition | – |
| `bipolar` | Built from criterion groups `manic_episode` (A plus ≥3 B, or ≥4 if only irritable; ≥7 days or any duration if hospitalised; marked impairment), `hypomanic_episode` (same symptom count; ≥4 consecutive days; observable change, not marked impairment) and `depressive_episode` | `bipolar_i_pattern`, `bipolar_ii_pattern`, `cyclothymic_pattern`, `depressive_features_only_bipolarity_not_assessable` |
| `gad` | Excessive worry about several topics, hard to control, ≥3 of 6 associated symptoms; ≥6 months; impairment | – |
| `social_anxiety` | Fear of scrutiny, consistent fear when exposed, avoidance or enduring with distress, out of proportion; typically ≥6 months; impairment; `performance_only` specifier | – |

Differentials listed in the registry include: bipolar vs unipolar depression vs borderline personality features vs ADHD vs substance-induced; GAD vs SAD vs panic disorder vs depression with anxious distress. Bulimia and other conditions that the proposal excludes are **not** added. `enabled_conditions` in the registry makes the list configurable.

---

## 4. Data model — migration `0008_progress_monitoring` (additive only)

All ids are `String(100)` with prefixes (`dobs-`, `iscore-`, `pred-`, `crit-`, `risk-`, `chg-`, `plan-`). Timestamps are tz-aware. JSON columns are `Text`, following the repo's convention. Lowercase review statuses match `GeneratedNoteItem.review_status`.

| Table | Columns (beyond the brief's list) and constraints |
|---|---|
| `registry_versions` | id, kind, version, sha256, created_at; unique(kind, version, sha256) |
| `instrument_scores` | brief columns plus **`administered_at`** (date, required when `session_id` is null, used for ordering), `phq8_total`, `is_active` (bool), `superseded_by_id`, `rejection_reason`. One active row per (patient, session_id, instrument_key, provenance), enforced in code plus a partial unique index (`sqlite_where is_active = 1`). |
| `domain_observations` | brief columns plus **`timeframe`** (`current`, `past`, `unclear`); unique(session_id, domain_key); `locked` is derived (decided note) and not stored |
| `domain_observation_versions` | id, observation_id, version_no, role (`history`, `alternative`), snapshot_json, actor, created_at. An AI suggestion that arrives when a manual or reviewed value exists is stored as `role=alternative` and shown in the review panel. |
| `condition_predictions` | brief columns plus `is_active`, `superseded_by_id`, `postprocess_json` (downgrades and removed rationale sentences, with reasons), `stale` handled through `review_status='stale'`, `calibration_id` (nullable) |
| `criterion_findings` | brief columns (`evidence`/`counter_evidence` JSON lists of {segment_id, session_id, start_ms, end_ms, speaker_role, basis}) |
| `risk_statements` | brief columns plus `is_active`, `original_ai_json`, `source_revisions_json`. **No numeric field of any kind.** |
| `change_events` | brief columns plus `series` (`administered`, `ai_estimate`, `domain`, `prediction`), `created_at` |
| `plan_followups` | brief columns |
| `audit_events` (existing, **altered in 0008**, approved Q1) | add nullable `patient_id` (FK patients, indexed); `session_id` becomes nullable via `batch_alter_table`. Patient-level actions (session-less scores, predictions, change acknowledgements) set `patient_id`, and `session_id` too when one applies (`as_of_session_id` for predictions). Existing rows are unchanged; `downgrade()` restores NOT NULL only if no null rows exist (otherwise it raises). |
| **`progress_evidence_links`** (new) | segment_id (indexed), owner_kind (`domain_observation`, `instrument_score`, `prediction`, `criterion_finding`, `risk_statement`, `plan_followup`), owner_id, revision_at_link. This gives exact staleness lookup without scanning JSON. |

Rules enforced in `store.py` (tests in Phase 1/3):
1. Every cited segment id must resolve to a `transcript_segments` row whose session belongs to the same patient. Session-level rows (observations, AI scores, risk, plan follow-ups) must cite **their own session**. Any violation gives 422 `INVALID_EVIDENCE_REFERENCE`.
2. Decided-note lock: if the session's note draft `workflow_status != 'DRAFT'`, create/review/edit/reject on that session's observations, scores and risk statements returns 409 `NOTE_TERMINAL`. Predictions, change acknowledgements and session-less scores are not locked.
3. Rejected rows: `review_status='rejected'`, removed from the active series, kept. Edited rows: the edited value is active, and `original_ai_json` stays unchanged (immutable after insert; checked in code and by test).
4. Every create/review/edit/reject/acknowledge writes an `audit_events` row (with `patient_id`; `session_id` when applicable). A new helper `store.audit(db, *, patient_id, session_id, event_type, actor, metadata)` is used; `main._audit` stays as it is for existing callers.
5. "Pending reviews do not block note approval": `approve_note` is not modified.

### 4.1 Contracts (`services/api/schemas.py`, additive)
`ProgressReviewStatus` (`pending|accepted|edited|rejected|stale`), `DomainStatus`, `PredictionLabel`, `CriterionStatus`, `RiskCategory`, `CitedEvidence` (segment_id, session_id, start_ms, end_ms, speaker_role, basis ≤ 300 chars), `DomainObservation`/`DomainObservationInput`, `InstrumentItemScore`/`InstrumentScore`/`InstrumentScoreInput`, `CriterionFinding`, `ConditionPrediction`, `RiskStatement` (no numeric field; contract test checks that), `ChangeEvent`, `PlanFollowup`, `ProgressReview` (actor, action `accept|edit|reject`, value / comment / reason, `expected_updated_at` for optimistic concurrency like `FormulationReview`), `PatientProgress` (the dashboard payload). Existing models are not changed, **except the approved Q1 change**: `AuditEvent.session_id: str | None` plus a new optional `patient_id: str | None = None`. `/sessions/{id}/audit-events` keeps returning only that session's rows; a new `GET /patients/{id}/audit-events` returns patient-level rows. The frontend `AuditEvent` type is updated to match. Tests go in `tests/contracts/test_progress_schemas.py`.

---

## 5. Trend and change engine (`services/progress/trends.py`)

Pure functions over `SeriesInput = [SessionSlot(session_id, index, date, values: list[Point])]`. A `Point` carries value or label, provenance, review_status and refs.

- **Active filter:** include `pending`, `accepted`, `edited`, and `MANUAL` values; exclude `rejected` and `stale`. Review status never changes the math.
- **Series split:** instruments → `administered` (MANUAL) and `ai_estimate` (AI_GENERATED), computed independently. Domains → one series per domain. Predictions → one per condition (+ subtype).
- **Gaps:** a session without an active value is `None`, never 0, never interpolated or carried forward. `insufficient_evidence` is a gap with a reason. `explicit_denial` is plotted as a distinct state, not as 0 (see **Q8**).
- **Per measure:** delta vs previous assessed value, delta vs baseline (first assessed), `sessions_assessed / total`.
- **Change rules** (rule ids are configurable in `trends.py` constants plus the registry):
  - `instrument.reliable_change`: |Δ| ≥ threshold between consecutive *assessed* values within one series. Threshold `null` produces no events.
  - `domain.rating_jump`: |Δrating| ≥ 2 between consecutive sessions that are both `rated`.
  - `domain.emergence`: `rated` after ≥ 2 consecutive `insufficient_evidence` sessions.
  - `prediction.label_change`: label or subtype differs between consecutive `as_of` sessions.
  - `prediction.probability_shift`: |Δp| ≥ 0.2 (only when both p are non-null).
  - Each event records `provenance_of_inputs` (`ai`, `reviewed`, `mixed`). Events are de-duplicated on (measure, from, to, rule, series), so recomputation is idempotent.
- **Trajectory** (≥ 3 assessed values, threshold T): endpoint change ≤ −T → `decreasing`; ≥ +T → `increasing`; else internal range ≥ T → `fluctuating`; else `stable`. T defaults to the instrument's reliable-change threshold. This is the brief's "LongCounsel-8 rule", **UNVERIFIED against the paper text** (to be checked in Phase 5).
- Tests cover gaps, no carry-forward, T and T−1 boundaries, null threshold, coexisting series, rejected/stale exclusion and pending inclusion.

---

## 6. AI pipeline

Versions: `progress-extract-v1`, `condition-predict-v1`. Output uses the llama.cpp `json_schema` grammar, with `refs` items constrained to an enum of supplied aliases. Temperature 0, seed 42 (Runtime defaults). Truncated output (`OutputLimitError`) is discarded and the group is split; output is never spliced.

### 6.1 Jobs (`services/progress/jobs.py`)
- Storage pattern copied from note-generation: `LOCAL_DATA_DIR/progress-jobs/<32-hex>/job.json` (+ `result.json`, `request-metrics.json`, `runtime.log`), with checkpoint cache `LOCAL_DATA_DIR/progress-jobs/cache-v4/`.
- Concurrency: acquire the note-generation `LOCK` without blocking (409 if busy), then `memory_slot(cancel)`.
- **Resume / retry:** `POST /progress-jobs/{id}/retry` starts a new job with the same request. Completed groups come from the checkpoint cache (same transcript revision, model sha, prompt version and payload digest), so work resumes where it stopped. Cancel uses the same events as note-generation.
- On completion the result is re-checked against the current transcript revision. If it changed, the result is withheld, as in note-generation.

### 6.2 Stage A — per-session extraction (`extraction.py`, `validation.py`, `evidence.py`)
1. **Snapshot**: segments (id, role, start/end, revision, text), `digest()` revision. Requires `transcript_reviewed: true`, no active transcription or meeting, an undecided note, and not an LC8 test-split session or an `independent_final_reserved` session.
2. **Evidence packet**: if the full transcript fits the budget for a group call (`Runtime.fits` with reserved output), it is sent as is. Otherwise a retrieval packet per domain group: registry queries (e.g. sleep: `sleep OR slept OR night OR insomnia OR neend`) → BM25 top-k with ±2 context turns. Segments are aliased `T0…`. **Stage A uses only the current session** (no prior-session values), so its output depends only on the evidence setting. The history experiment applies to Stage B (§8.5), which lets the benchmark reuse Stage A results across history settings.
3. **Four group calls** (each ≤ ~900 output tokens; split further on output limit):
   - G1 depressive domains + PHQ items 1–8
   - G2 manic/hypomanic domains
   - G3 anxiety domains + GAD-7 items 1–7
   - G4 functioning domains + risk statements + plan follow-ups for the previous session's plan items (from that session's note `plan` field `generated_items` of type proposed/agreed_plan, or plan text lines)
4. **Output schema** (G1 example; others analogous):
```json
{"domains": {"sleep": {"status": "rated|explicit_denial|conflicting|insufficient_evidence",
   "rating": 0, "direction": "reduced|increased|reduced_need_without_fatigue|disrupted|null",
   "timeframe": "current|past|unclear", "frequency": "", "intensity": "", "duration": "",
   "refs": ["T3","T4"], "who": "client report|clinician observation", "basis": "paraphrase"}},
 "phq_items": {"1": {"score": 0, "status": "rated|insufficient_evidence", "refs": ["T3"], "basis": ""}},
 "limitations": []}
```
   G4 adds `"risk_statements":[{"category":"self_harm|suicidal_ideation|harm_to_others|other_safety","refs":[],"basis":""}]` and `"plan_followups":[{"plan_id":"P0","status":"discussed_done|discussed_partial|discussed_not_done|not_discussed","refs":[],"basis":""}]`.
5. **Deterministic validation** (each failed item is withheld with a visible limitation; the whole call is never silently dropped):
   - Aliases map back to original ids. A foreign or unknown id causes the item to be rejected.
   - `rated` needs at least one CLIENT ref, or a CLINICIAN ref when `who` = clinician observation. `explicit_denial` needs a CLIENT ref.
   - **Other-person guard** (lexical, conservative): if every cited CLIENT segment mentions a third-party subject (`my (sister|brother|mother|father|friend|partner|son|daughter|…)`, `he|she|they` + domain keyword) and has no first-person marker near the keyword, the item is withheld. A test covers "my sister can't sleep".
   - Quotations: any run of more than 15 consecutive words copied from a source, or quote characters, causes the item to be withheld. The patient's name or preferred name from intake in `basis` causes rejection.
   - PHQ item 9 never comes from the model (it is not in the schema; the contract also rejects it).
   - `timeframe=past` observations are stored (Stage B uses them for episode history) and shown with a "past" marker. **They are not plotted as the session's current rating** (see **Q8**).
6. **Persistence**: active `AI_GENERATED`, `review_status=pending`, `original_ai_json` = validated item. An existing MANUAL or accepted/edited value is **never** overwritten; the new suggestion is stored as `domain_observation_versions.role=alternative`. An existing pending AI value is replaced (old one goes to history). Evidence links are written and change events recomputed.

### 6.3 Stage B — patient-level condition prediction (`prediction.py`, `postprocess.py`)
1. Runs automatically after Stage A completes for a session, and on demand. History setting `history=prior` (clinical default) uses all of the patient's sessions up to `as_of_session_id`. `history=current` (benchmark arm only) uses just the `as_of` session. Input with `history=prior`: active domain observations and instrument scores with refs (compact rows), plus per-criterion retrieval over every session (registry `retrieval_queries`, e.g. "decreased need for sleep", "felt on top of the world", "spending spree", "worry about everything", "afraid of being judged", "avoid presentations"). Segments use patient-wide aliases `A0…` with session index and timestamps.
2. **One call per condition** (4 calls) within 4,096 tokens. If a packet does not fit, the oldest sessions' raw segments are dropped first while their compact observation rows are kept, and a coverage limitation is recorded. Nothing is truncated mid-segment.
3. **Output schema:**
```json
{"criteria":[{"id":"mde.depressed_mood","status":"met|not_met|insufficient_evidence|conflicting",
   "evidence":[{"ref":"A12","basis":""}],"counter_evidence":[{"ref":"A3","basis":""}],
   "timeframe":"current|past|both|unclear","duration_note":""}],
 "duration_status":"met|not_met|unknown","impairment_status":"met|not_met|unknown",
 "rule_outs":[{"consideration":"substance_or_medication","status":"not_assessed|evidence_present|evidence_absent","refs":[]}],
 "differentials":[{"condition":"","why_considered":"","distinguishing_present":[],"distinguishing_missing":[]}],
 "missing_information":["question for next session"],
 "prediction":"likely|possible|unlikely|insufficient_evidence","subtype":null,
 "rationale":[{"sentence":"","refs":["A12"]}]}
```
4. **Deterministic post-processing** (in code, recorded in `postprocess_json`, shown in the UI):
   - Recount `criteria_met_count` from findings whose status is `met` and which have ≥ 1 valid evidence ref.
   - `likely` requires `min_met`, the `must_include_any_of` core criterion, `duration_status=met` with a cited `duration_note`, and `impairment_status=met` when required. Otherwise it is downgraded to `possible` (≥ 1 core criterion met with evidence) or `insufficient_evidence`.
   - `unlikely` requires cited counter-evidence for the core criteria; otherwise it becomes `insufficient_evidence`. Absence of evidence is never "unlikely".
   - Bipolar: `bipolar_i_pattern` only with a met `manic_episode` group (cited, any timeframe); `bipolar_ii_pattern` only with met `hypomanic_episode` + `depressive_episode`, without manic; `cyclothymic_pattern` only with a cited ≥ 2-year duration note; depressive features without manic/hypomanic evidence give `depressive_features_only_bipolarity_not_assessable`. A prediction without any cited evidence is not stored (principle 2).
   - Rationale: at most 5 sentences. A sentence without valid refs is removed, and the removal is listed.
5. Saved as active `AI_GENERATED`, `pending`. The previous active prediction for the same (patient, as_of, condition) is superseded and kept.
6. **Probability**: `null` unless a calibration mapping applies (see §8.4 and **Q3**).

### 6.4 Prompting rules (both stages)
Transcript text is untrusted data, never instructions (same wording as `prompts.POLICY`). Preserve speaker and subject, negation, time references (past vs current, recorded in `timeframe`/`duration_note`), uncertainty and contradictions (`conflicting`). No quotation over 15 words, no names, no hidden chain-of-thought requested or stored. Criteria are described with the registry's paraphrased summaries. The prompts state that outputs are hypotheses for clinician review.

### 6.5 Staleness (`staleness.py`)
`mark_stale(db, segment_id)` is called in `update_transcript_segment` before the commit. It looks up `progress_evidence_links` by segment and sets `review_status='stale'` on dependent observations, scores, risk statements, plan follow-ups and predictions (a finding makes its prediction stale). The prior status is kept in a history row. Stale rows leave the active series. The badge reads "Sources changed — regenerate or review", and the actions are *regenerate* (new job) or *re-review* (accept/edit against current text; this updates `revision_at_link`).

---

## 7. API (`/api/v1`, `services/api/progress_routes.py`)

| Method & path | Purpose |
|---|---|
| `POST /sessions/{id}/progress-extraction` → 202 `{job_id}` | Stage A (+ automatic Stage B). Body: `transcript_reviewed`, optional `evidence_mode` (`auto` default: full transcript when it fits, else retrieval) |
| `GET /progress-jobs/{job_id}` | Status, stage, limitations, metrics (no patient text) |
| `POST /progress-jobs/{job_id}/cancel`, `POST /progress-jobs/{job_id}/retry` | Cancel, or resume via the cache |
| `POST /patients/{id}/condition-predictions` → 202 | Body: `as_of_session_id`, optional `condition_keys` |
| `GET /sessions/{id}/domain-observations` | Active rows + alternatives + history |
| `PUT /sessions/{id}/domain-observations/{domain_key}` | Manual entry (MANUAL, accepted) |
| `POST /sessions/{id}/domain-observations/{obs_id}/review` | accept / edit / reject |
| `GET, POST /patients/{id}/instrument-scores` | List; clinician administration item by item |
| `POST /patients/{id}/instrument-scores/{score_id}/review` | accept / edit / reject |
| `GET /patients/{id}/condition-predictions` | Latest per condition + history |
| `GET /patients/{id}/condition-predictions/{prediction_id}` | Findings with resolved evidence (text, role, timestamps, recording id) |
| `POST /patients/{id}/condition-predictions/{prediction_id}/review` | accept / edit label + comment / reject with reason |
| `GET /patients/{id}/risk-statements`, `POST …/{id}/review` | Flagged statements; review |
| `GET /patients/{id}/progress` | `PatientProgress`: series, gaps, deltas, change events, trajectories, latest predictions, plan follow-ups, pending-review counts, versions |
| `POST /patients/{id}/change-events/{id}/acknowledge` | Acknowledge with optional comment |

Errors use the existing `{"error":{"code","message"}}` envelope.

---

## 8. Evaluation (`services/progress/evaluation/`)

### 8.1 LongCounsel-8 preparation (`longcounsel.py`)
- `python -m services.progress.evaluation.longcounsel prepare --edition longcounsel-8-gpt-5.4-mini-low`: verify `manifest.json` sha256 for archive and split file; record sizes and hashes in `prepared/manifest.json`; require README + LICENSE. Stream from the tar (no extraction into `raw/`). Write `prepared/<edition>/{transcripts,labels}/<run_id>.json`.
- **Leakage guard:** transcript allowlist {`session_id`, `turn_id`, `speaker`, `text`, `message_id`}. Any other key is dropped and counted. Tests cover a fixture with injected `phq`, `score`, `band`, `trend`, `state` fields, assert that labels never reach transcript files or prompt payloads, and scan text for questionnaire terms (`PHQ`, `GAD`, `score`, `questionnaire`, `severity`) with counts reported.
- Mapping: `Counselor` → `CLINICIAN`, `Client` → `CLIENT`. Synthetic timestamps: row k (0-based within a session, counselor before client) gets `start_ms = k·10 000`, `end_ms = start_ms + 9 000`. Segment id = `message_id`.
- `.gitignore`: add an explicit `data/research/datasets/longcounsel-8/` line (already covered by `/data/research/datasets/*`; the explicit line documents intent).
- Fixture for tests: `tests/fixtures/longcounsel_mini/` (2 fictional trajectories × 5 sessions × 4 exchanges, hand-written, with hand-computed expected metrics).

### 8.2 Demo import
`python -m services.api.import_longcounsel_demo --split validation --limit 10`: SQLite backup → patients `lc8-demo-patient-<run_id>` named "LongCounsel demo · <run_id>", sessions `lc8-demo-<run_id>-s<n>` (consent `{research_demo, dataset:'longcounsel-8', source_split, source_run_id}`, `created_at` = fixed base date + 7·n days so ordering is deterministic), segments `lc8-demo-<message_id>`. **No labels imported.** `--split test` is refused, and progress jobs refuse sessions with `dataset='longcounsel-8' and source_split='test'`.

### 8.3 Metrics (`metrics.py`, `bootstrap.py`)
95% intervals from a trajectory-level bootstrap (2,000 resamples, seed 42).
- PHQ-8: current-score MAE; item MAE and exact match (GT floats are rounded half-up for exact match; reported); adjacent-session change MAE; large-change direction accuracy for |ΔGT| ≥ 5 (a predicted tie counts as wrong); MAE by trajectory type (see **Q6**); coverage (insufficient rate; totals with insufficient → 0 **and** MAE on rated items only).
- Depression vs GT PHQ-8 ≥ 10: score = AI PHQ-8 total for AUROC/AUPRC; sensitivity, specificity, PPV, NPV and F1 at the `likely` and `possible` label thresholds; Brier score and reliability curve after calibration.
- Trajectory agreement (AI descriptor vs GT descriptor).
- Citation quality: export 100 random cited findings to `citation_rating.csv` (empty rating column; never auto-filled).
- Runtime: per-session wall time, calls, tokens, peak RSS/VRAM, model and runtime hashes (from `Runtime.metrics`).
- Baselines fitted on train **self-reports** only: global median total; per-session-index mean.

### 8.4 Calibration (`calibration.py`)
Isotonic regression (sklearn), with Platt as an option, mapping AI PHQ-8 total to P(GT PHQ-8 ≥ 10). It is fitted on the **chosen arm's validation-subset outputs** (12 trajectories, 60 sessions), and the reported reliability uses 5-fold cross-fitting over trajectories, because the same subset selects the config (see **Q5**). The runbook includes an *optional* user-run step that applies the frozen config to the remaining 25 validation trajectories to enlarge the calibration set. The mapping file stores the dataset hash, subset manifest hash, config hash and fit date. Bipolar, GAD and SAD probabilities stay `null`. **Clinical use (Q3 default):** the calibrated probability is shown only in the research view and for `lc8-demo-` patients; clinical patients get `probability = null` with the note "calibration is research-only (dataset terms)".

### 8.5 Execution model (revised by the user, 7 November 2026)
**Claude runs only the 2-session timing pilot.** Every other long job is a script plus exact commands that the user runs. Claude never runs benchmark arms, calibration runs or the final run.

1. **Pilot** (`python -m services.progress.evaluation.benchmark pilot`): 2 validation sessions (fixed choice, seed 42), Stage A for both evidence settings + Stage B (depression) for both history settings. It records per-call and per-session wall time, tokens, RSS/VRAM and backend into `data/research/progress-runs/pilot/timings.json`. The runbook's hour estimates are computed from this file. Claude states the expected runtime before starting it.
2. **Subset selection** (`python -m services.progress.evaluation.subsets select --edition longcounsel-8-gpt-5.4-mini-low --seed 42`): writes fixed manifests to `data/research/progress-subsets/`:
   - `val-arms-12.json`: 12 validation trajectories, stratified by trajectory type.
   - `test-final-37.json`: 37 test trajectories (50% of 74), stratified by trajectory type. The other 37 test trajectories stay untouched as a reserve and are never listed.

   Each manifest stores the run_ids, seed, edition, split-file sha256, source label sha256, the stratification rule and its sha256. Stratification uses the **derived** GT trajectory type (Q6: §5 descriptor applied to GT PHQ-8 totals, T = 5) with proportional allocation (largest remainder, seed-42 shuffle within each stratum). The command prints each subset's trajectory-type counts and its number of large-change transitions (adjacent GT |Δ PHQ-8| ≥ 5). It refuses to overwrite existing manifests. **The manifests are committed before any run**; `.gitignore` gets `!/data/research/progress-subsets/` exceptions because `data/` is otherwise ignored. Labels are used only for stratification and reporting; they never reach prompts.
3. **Benchmark CLI** (`python -m services.progress.evaluation.benchmark run --subset <manifest> --arm <name> --run-id <id>`):
   - Arms: `retrieval-current`, `retrieval-prior`, `full-current`, `full-prior` (evidence × history); `--arm frozen` reads `frozen-progress-config.json`.
   - Run directory `data/research/progress-runs/<run-id>/`: `config.json` (arm, subset manifest sha256, prompt/registry/post-processing/code sha256, model sha256, runtime version, NOTE_* settings). **On rerun, any config difference makes the command refuse** (different run-ids are required for different configs).
   - Checkpoint after every session: `sessions/<run_id>__s<n>.json` (Stage A result reference + Stage B depression prediction + timings), written atomically (temp + replace). On rerun, completed sessions are skipped automatically, so resume needs no flags.
   - **Stage A cache per evidence setting**: `data/research/progress-runs/_stage-a/<evidence>/<config-sha>/<run_id>__s<n>.json`, shared by the `-current` and `-prior` arms (and by the frozen run when the config matches). On top of that, the Recovery checkpoint cache resumes inside a session.
   - **Stage B predicts depression only** in benchmark runs.
   - **Ctrl+C / SIGTERM**: the handler sets the runtime cancel event. The in-flight session is abandoned (no partial checkpoint), llama-server is terminated through `Runtime.close()`, the memory lock is released, a final log line is written, and the process exits with 130. A second Ctrl+C forces exit.
   - `run.log`: one line per session with completed/total, session seconds, rolling mean and ETA (mean × remaining), plus start/stop/resume/error lines. No patient text, at any `NOTE_LOG_LEVEL`. `status` subcommand: `benchmark status --run-id <id>`.
   - Refuses splits other than the manifest's, and refuses `test` manifests unless invoked through `run_final`.
4. **Report** (`python -m services.progress.evaluation.report --run-id <id> [--run-id <id> …] [--output <dir>]`): all §8.3 metrics with 2,000-resample trajectory bootstrap CIs (seed 42), plus the baselines. It works on partial runs: a run is marked `incomplete` with `n_sessions_completed/expected`, and metrics use only complete trajectories for change and trajectory metrics, and all completed sessions for current-score metrics (both n reported). Writes `report.json` + `report.md`, which the results view reads.
5. **Freeze** (`python -m services.progress.evaluation.benchmark freeze --run-id <chosen> --reason "…"`): writes `frozen-progress-config.json` with config and code hashes plus the calibration mapping. It refuses if the run is incomplete.
6. **Final half-test run** (`python -m services.progress.evaluation.run_final --subset data/research/progress-subsets/test-final-37.json`): following `services/retrieval/run_final.py`, it checks that the frozen config and code hashes match, refuses if `final-results.json` exists, records `evaluation-started.json`, then runs the benchmark with `--arm frozen` and the report. **Run by the user only.**
7. **Runbook** `docs/PROGRESS_BENCHMARK_RUNBOOK.md`: copy-paste commands in order (pilot → subset selection → commit manifests → each arm inside `tmux` with `systemd-inhibit --what=idle:sleep --why="pshynox benchmark" …` → check progress (`tail -f run.log`, `benchmark status`) → resume after a crash (rerun the same command) → reports → freeze → optional calibration enlargement → final run). It includes estimated hours per step, generated from the pilot timings (`benchmark estimate` prints the table).

### 8.6 Results views
`/workspace/research-progress`: metrics table with CIs, per-trajectory GT vs estimate plots, stratified bars, ROC and reliability curves, per-session drill-down with citations, labelled "Synthetic benchmark (LongCounsel-8): depression track only."

### 8.7 Vignette set (structure only, Phase 6)
`data/research/vignettes/<case_id>/{case.json, sessions/*.json, labels.json}` with a Pydantic schema. Labels (conditions, subtype, criterion-level ground truth, session domain ratings) are set by the consultant, blind to model output. Import, evaluation (per-condition sensitivity/specificity/F1, criterion agreement, citation quality) and a results view reuse §8.3. `docs/VIGNETTE_AUTHORING_GUIDE.md` describes the case mix. Only one tiny fictional test fixture is created; no labels or case content beyond that.

---

## 9. Clinical dashboard UI (`apps/web/src/features/progress/`)

- **Dependency:** `recharts` (pre-approved), installed through npm and bundled (no CDN). The version is chosen in Phase 2 for React 19 compatibility.
- **Routes:** `main.tsx` adds `parts[1]==="patients" && parts[3]==="progress"` → `ProgressPage`. `PatientPage` gains a **Progress** tab (link to the route). `/workspace/research-progress` → `ResearchProgressPage`.
- **ReviewBadge** (one component everywhere, including chart tooltips, grid cells, cards and print): `AI-generated · human review pending` (amber outline), `Reviewed by <name>` (solid), `Edited by <name>`, `Rejected by <name>` (history only), `Sources changed`. The text label is always present, so colour is never the only signal.
- **Progress page** sections follow brief §7.2, items 1–11, in this order: header (counts, versions, run button) → `HypothesesPanel` (cards → criteria checklist → cited turns with play and open-in-transcript; rule-outs, differentials, "Information still needed"; accept / edit / reject) → `RiskStatementsPanel` (empty state "No statements flagged by the model — this is not a risk assessment") → `InstrumentChart` ×2 (Administered and AI-estimate series, band shading, gaps, change markers, item tooltip, numeric table fallback, "Add administration" form) → `DomainGrid` (grouped rows × session columns, distinct states) + `EvidenceDrawer` → `PredictionTimeline` → `ChangesList` (acknowledge) → `SessionComparison` → `PlanThread` → print (`printProgress.ts`, as in `printNote.ts`) → empty states for 0, 1 and 2 sessions.
- **Two-click citation:** a citation row has ▶ (plays the source recording at `start_ms`, when a recording exists) and "Open in transcript" (`/workspace/sessions/{sid}#segment-{id}`). `SessionDetailPage` will open `#session-sources` and scroll to the hashed segment on load (small additive change). Prediction card (click 1) → citation ▶ (click 2).
- **Session Observations panel** (`ObservationsPanel.tsx`, beside the note composer in `SessionDetailPage`): "Analyse this session" (needs transcript-review acknowledgement; disabled for decided notes), job progress and limitations, one card per domain and instrument item, manual entry, stale banner, pending count. It does not block approval.
- **Playwright:** `e2e/progress.spec.ts` + `playwright.progress.config.ts` (same pattern as `playwright.notes.config.ts`, generation mocked with `page.route`).

---

## 10. File list by phase

| Phase | New / changed files |
|---|---|
| 1 | `services/progress/{__init__,trends,store}.py`; `services/progress/registry/{__init__,loader}.py`, `{instruments,domains,conditions}.json`; `alembic/versions/0008_progress_monitoring.py`; `services/api/db.py` (+ORM); `services/api/schemas.py` (+contracts); `services/api/progress_routes.py` (manual entry, GETs, reviews, `/progress`, acknowledge); `services/api/main.py` (include router); tests: `tests/progress/{test_registry,test_trends}.py`, `tests/contracts/test_progress_schemas.py`, `tests/api/test_progress_manual.py` |
| 2 | `apps/web/package.json`/lock (+recharts); `apps/web/src/features/progress/{api.ts, ReviewBadge, ProgressPage, InstrumentChart, DomainGrid, EvidenceDrawer, RiskStatementsPanel, ChangesList, SessionComparison, ObservationsPanel, progress.css}`; `main.tsx`, `PatientPage.tsx`, `SessionDetailPage.tsx` (hash scroll + panel); `apps/web/e2e/progress.spec.ts`, `apps/web/playwright.progress.config.ts` |
| 3 | `services/progress/{prompts,evidence,extraction,validation,jobs,staleness}.py`; hook in `main.update_transcript_segment`; job routes; tests `tests/progress/{test_extraction_validation,test_jobs,test_staleness,test_lock}.py` |
| 4 | `services/progress/{prediction,postprocess}.py`; prediction routes; `HypothesesPanel`, `PredictionTimeline`; tests `tests/progress/{test_postprocess,test_prediction}.py`; e2e additions |
| 5 | `services/progress/evaluation/{__init__,longcounsel,subsets,metrics,bootstrap,calibration,benchmark,report,run_final}.py`; `services/api/import_longcounsel_demo.py`; research routes; `ResearchProgressPage`; `tests/fixtures/longcounsel_mini/`; `tests/progress/evaluation/*` (subset determinism and stratification, config-mismatch refusal, resume skipping, SIGINT leaves no partial checkpoint, Stage A cache reuse across history arms, partial-run report); `.gitignore`; `docs/PROGRESS_BENCHMARK_RUNBOOK.md`; committed `data/research/progress-subsets/{val-arms-12,test-final-37}.json`; pilot timings (`data/research/progress-runs/pilot/`, not committed) |
| 6 | `services/progress/evaluation/vignettes.py`; `data/research/vignettes/README.md` + one fixture under `tests/fixtures/vignettes_mini/`; `docs/VIGNETTE_AUTHORING_GUIDE.md`; `PlanThread`, `printProgress.ts`; `docs/PROGRESS_MONITORING.md`; updates to `README.md`, `docs/PROJECT_STATUS.md`, `docs/MODULE_INTEGRATION_GUIDE.md` |

No existing migration is edited. No existing contract is changed non-additively (except possibly **Q1**, which needs approval). No files are deleted.

---

## 11. Tests (minimum, by phase)
Brief §8 in full. Mapping: trend engine, contracts and lock in Phase 1; badges and manual entry e2e in Phase 2; extraction validation (other-person, denial, conflict, past vs current, foreign ids, truncation discarded, manual preserved) and staleness in Phase 3; post-processing (likely downgrade, bipolar subtype rules, rationale stripping, recount) and hypothesis e2e in Phase 4; importer leakage, split blocking, metrics vs hand-computed values, AUROC, calibration and bootstrap determinism in Phase 5; vignette schema/import in Phase 6. The suite must stay at ≥ 201 passing with the frontend build passing.

---

## 12. Runtime estimates (to be replaced by the Phase 5 pilot)
Measured references: GPU (GTX 1650) about 30 generated tok/s and about 150 tok/s prefill; CPU notes about 120–200 s per short note (docs/NOTE_GENERATION.md, NOTE_DRAFTING_OPTIMIZATION.md).
- Stage A: 4 calls × (~2.5k input + ~700 output) ≈ **1.5–3 min per session on GPU**, about 5–10 min on CPU.
- Stage B: 4 calls per `as_of` session ≈ 1.5–3 min on GPU.
- Benchmark Stage B is depression only: about 1 call per `as_of` session (~0.5 min on GPU).
- Arm comparison on `val-arms-12` (60 sessions): Stage A × 2 evidence settings (120 session extractions, reused across history arms) + Stage B × 4 arms (240 calls) ≈ **5–8 h GPU in total** (preliminary).
- Final run on `test-final-37` (185 sessions) ≈ **6–10 h GPU** (preliminary). Optional calibration enlargement (25 val trajectories, 125 sessions) ≈ 4–7 h.
- These are rough figures; the runbook replaces them with numbers derived from the pilot. Claude runs only the pilot (≈ 10–20 min GPU, announced before starting).

---

## 13. Open questions

### Resolved (7 November 2026)
- **Q1 — Audit rows: APPROVED option (a).** 0008 adds nullable `audit_events.patient_id` and makes `session_id` nullable. `AuditEvent.session_id` becomes `str | None` (approved non-additive contract change), plus `patient_id`. Details in §4 and §4.1.
- **Q2 — Registry format: JSON** (no new dependency). The user also authorised installing whatever dependencies the module needs; each new dependency is still declared in `pyproject.toml` / `package.json` and listed in the phase's progress-log entry.
- **Q3 — Calibration in clinical use: not answered; the recommended default applies** (research view and `lc8-demo-` patients only; clinical `probability = null`). The user can override this at any time.
- **Phase 5 execution model changed by the user**: Claude runs only the 2-session pilot. See §8.5.

### Methodology (can be settled during the phases)
- **Q4** — `possible`/`likely`/`unlikely` label definitions as written in §6.3.4 (consultant to verify).
- **Q5** — Calibration and config selection share `val-arms-12` (60 sessions, small). Plan: cross-fitted reliability; optional user-run enlargement on the other 25 validation trajectories (§8.5).
- **Q6** — No trajectory-type label in the release. Plan: derive the GT trajectory type by applying the §5 descriptor rule to the GT totals (T = 5), and report it as derived. **This now also drives subset stratification.** Check whether the paper defines the types differently before running subset selection; if it does, update the rule *before* the manifests are committed.
- **Q7** — AI estimates cover PHQ items 1–8 only. The plan charts **PHQ-8 subtotals** for both series on one axis (administered PHQ-9 also shows its full 0–27 total and item 9 in the table and tooltip). This is so different scales are not presented as equivalent (proposal §8). Applying reliable-change threshold 5 to PHQ-8 is an assumption.
- **Q8** — Past-timeframe observations are stored but not plotted as current ratings. `explicit_denial` is plotted as a distinct "denied" state, not as 0. Confirm both.
- ~~Q9~~ — Superseded by the user's Phase 5 plan (12 validation trajectories; 37 test trajectories for the final run).

### For the clinical consultant
Criterion summaries and the DSM-5-TR mapping; domain list and descriptors; the GAD-7 reliable-change threshold; the domain change rule (Δ ≥ 2); prediction label definitions; differentials and rule-outs; vignette case mix (bipolar I with manic period, bipolar II with hypomania, unipolar depression as a bipolar differential, GAD, SAD, GAD + SAD, subthreshold/no-disorder controls); agreement with, or restrictions on, the scope change in §1.

---

## 14. Limitations to carry into all docs
LongCounsel-8 is synthetic and depression-only. Bipolar, GAD and SAD predictions are unvalidated until the clinician-labelled vignette set exists. Criterion summaries are pending consultant verification. Prompts are in English and Urdu is untested. The model is a 4B local model. Reviewer names are self-reported (no authentication). Structural reference checks do not establish semantic support.

---

## 15. Progress log

### Phase 0 — 7 November 2026
- **Done:** read-only exploration of the docs listed in the brief, the proposal PDF, `schemas.py`, `db.py`, `main.py`, `import_annomi.py`, `patient_routes.py`, `services/note_generation/*`, the retrieval provider and `run_final.py`, the frontend router and pages, the e2e configs, and the LongCounsel-8 release structure (listing plus streamed schema inspection of one train trajectory; nothing extracted). Wrote this plan and the `CLAUDE.md` rules.
- **Tests:** backend 201 passed (26.7 s); frontend build passed. No code changed.
- **Deviations:** branched from `main` (equals `annotation` plus merge); plan-mode tool not used (it blocks writing this file); LC8 transcripts carry no hidden metadata (guard kept); no trajectory-type field (Q6); registries in JSON (Q2).
- **Next:** wait for approval and answers to Q1–Q3, then Phase 1.

### Phase 0 addendum — approval, 7 November 2026
- The user approved the plan. Q1 → option (a) (`audit_events` gets `patient_id`, `session_id` nullable, `AuditEvent` contract updated). Q2 → JSON, with blanket authorisation to install the dependencies the module needs. Q3 unanswered → recommended default applied.
- Phase 5 rewritten (§8.5): Claude runs only the 2-session pilot. The user runs subset selection, arms, reports, freeze and the final 37-trajectory test run from `docs/PROGRESS_BENCHMARK_RUNBOOK.md`. Stage A no longer uses prior sessions, so history is a Stage B setting and Stage A is cached per evidence setting. Benchmark Stage B predicts depression only.
- Runtime estimates in §12 updated (preliminary until the pilot).
- **Next:** Phase 1 in a fresh session: registries, migration 0008 (including the audit change), contracts, trend engine, manual entry APIs and tests. (Superseded: the user then asked for Phases 1–5 in one session.)

### Phase 1 — 7 November 2026 (registries, migration, contracts, trend engine, manual APIs)
- **Built:** `services/progress/registry/` (JSON instruments/domains/conditions + Pydantic loader with cross-checks and `<kind>@<version>+<sha8>` tags), migration `0008_progress_monitoring` (all §4 tables + `progress_evidence_links`; approved `audit_events` change), ORM models, additive contracts (+ approved `AuditEvent` change), pure `trends.py`, `store.py` (evidence resolution, decided-note lock, review state machine, audit, dashboard assembly, change-event sync), `services/api/progress_routes.py` (all §7 non-job endpoints + `/progress/registry`, `/patients/{id}/plan-followups`, `/patients/{id}/audit-events`).
- **Tests:** 225 passed (201 baseline + 24 new: trend engine incl. T/T−1, null threshold, gaps, pending included/rejected-stale excluded, coexisting series; registry; contracts incl. no numeric risk field and PHQ-9 item 9; API manual entry, evidence ownership, review states, decided-note lock, change acknowledgement, Q3 probability masking). Migration upgrade/downgrade/re-upgrade verified on a temp DB and a copy of the local DB (34 audit rows preserved). Frontend build passes.
- **Deviations:** `locked` is derived (decided note), not stored. Instrument scores store `subtotals_json` (PHQ-8) instead of a separate column. Rejected rows stay `is_active` and are excluded by status. MANUAL domain entries carry `review_status=accepted` with the entering clinician as reviewer.
- **Next:** Phase 2, the dashboard and Observations panel.

### Phase 2 — 7 November 2026 (dashboard, Observations panel, badge system)
- **Built:** `apps/web/src/features/progress/` (`api.ts`, `common.tsx` with `ReviewBadge`, two-click `CitationRow` and `ReviewActions`; `InstrumentChart.tsx` in Recharts with band shading, explicit gaps, change markers, item tooltips and a table fallback; `panels.tsx` with the domain grid and evidence drawer, risk panel, changes, prediction timeline, session comparison and plan thread; `HypothesesPanel.tsx`; `ProgressPage.tsx`; `ObservationsPanel.tsx`; `jobs.tsx`; `printProgress.ts`; `progress.css`). Route `/workspace/patients/{id}/progress` (lazy-loaded so Recharts ships only in that chunk), a Progress tab on the patient page, the Observations panel on the session page, and `#segment-<id>` deep links that open and focus the transcript turn.
- **Dependency:** `recharts@^3.10.1` (pre-approved). Palette: categorical slots 1–2 (#2a78d6 administered, solid and filled; #eb6834 AI estimate, dashed and hollow) validated on the #fffaf0 surface (CVD ΔE 24.7, contrast ≥ 3:1). Status is always text plus shape.
- **Tests:** backend 225 passed; frontend build passed (main bundle 394 kB, progress chunk 404 kB); new Playwright suite `npx playwright test -c playwright.progress.config.ts` → 4 passed (render plus pending badges and gaps; hypothesis card → citation → transcript turn focused; accept/edit/reject badge changes plus risk-rejection history; add PHQ-9 administration plus acknowledge change). Seed data: `tests/progress/e2e_seed.py` (fictional).
- **Deviations:** hypotheses panel, prediction timeline, plan thread and print view were built now (planned for Phases 4/6) because they only render existing payload fields. Phases 4/6 add the data producers and tests. The workspace footer still says "No diagnosis, risk assessment…" and is updated in the docs phase.
- **Next:** Phase 3, Stage A extraction, risk statements and staleness.

### Phase 3 — 7 November 2026 (Stage A extraction, risk statements, staleness)
- **Built:** `prompts.py` (`progress-extract-v1`), `evidence.py` (stable `T<n>` aliases; BM25 ±2-turn retrieval with English/Roman-Urdu/Urdu registry queries plus risk and plan queries), `validation.py` (alias → id mapping, foreign-id rejection, client/clinician attribution, explicit denial needs a client turn, other-person lexical guard, >15-word copy guard against all supplied turns, personal-name guard, item 9 never requested), `extraction.py` (4 group calls; grammar `json_schema` with refs enum-restricted to supplied aliases; reuses `Recovery` with tasks as the split unit, so output-limit responses are discarded and the task group is split, with checkpoint-cache resume; `auto`/`full`/`retrieval` evidence modes), `jobs.py` (shares the note-generation `LOCK` and `memory_slot`; job files under `LOCAL_DATA_DIR/progress-jobs/`; cancel/retry/interrupted detection; test-split and reserved-session blocking), `store.persist_extraction` (manual/accepted/edited values never overwritten, with new suggestions kept as `alternative` versions or inactive scores; pending AI values replaced with history; risk flags replace unreviewed AI flags only), `store.mark_stale` hooked into `update_transcript_segment` next to the note-excerpt staleness logic. Routes: `POST /sessions/{id}/progress-extraction`, `POST /patients/{id}/condition-predictions` (Stage B in Phase 4), `GET /progress-jobs/{id}`, `GET /sessions/{id}/progress-jobs`, cancel, retry.
- **Tests:** 243 passed (+18: validation with mocked outputs for other-person, denial, conflict, past vs current, foreign ids, quotation/name guards, item estimates and risk flags, truncation split, cache resume, retrieval mode; jobs end to end with a scripted runtime for review acknowledgement, pending persistence, manual/reviewed preservation, rerun supersession, decided-note/test-split/busy-lock blocking, retry; staleness including re-review restoring the value). Build and the 4 Playwright tests pass.
- **Deviations:** A rerun with an unchanged transcript and model reuses checkpointed answers, which is deterministic by design. Failed groups are recorded as job limitations while the other groups still save. GAD-7 is estimated in the anxiety group, and its item 6 (irritability) uses the manic-group domain's retrieval queries.
- **Next:** Phase 4, Stage B condition prediction.

### Phase 4 — 7 November 2026 (Stage B condition prediction, hypotheses panel, timeline)
- **Built:** `prediction.py` (`condition-predict-v1`: one grammar-constrained call per condition over patient-wide `A<n>` aliases; packet = condition brief, compact observations and questionnaire values of relevant domains, observation-cited turns plus per-session BM25 criterion retrieval; oldest raw sources dropped first to fit 4,096 tokens, with a visible note; `history` prior|current; DB-free `predict_condition` shared with the benchmark; `predict_patient` for clinical jobs, which runs automatically after Stage A and on demand) and `postprocess.py` (recount from cited findings; met without evidence, or not_met without counter-evidence, becomes insufficient; duration needs a cited duration note; impairment from findings; labels only downgraded; `unlikely` needs counter-evidence; bipolar I/II/cyclothymic subtypes set in code from cited periods with duration notes; "irritable only" needs 4 associated symptoms; depressive features without an elevated period give `depressive_features_only` with the label held at insufficient evidence; uncited rationale sentences removed and listed). `store.persist_prediction` (supersedes earlier versions, criterion findings, evidence links for staleness, audit; probability hook: depression only, `lc8-demo-` patients only, null until a calibration mapping exists). The UI (hypotheses panel, prediction timeline) from Phase 2 now receives real data; deep-link scrolling was made robust with a ResizeObserver while the page settles.
- **Tests:** 258 passed (+15: post-processing rules incl. no upgrades, bipolar subtype rules, irritable-only rule, rationale stripping; prediction core with citations across sessions, other-person citation dropping, history=current, budget-driven source dropping; jobs end to end for Stage A→B with only cited hypotheses stored, on-demand runs, supersession history, validation of conditions and session ownership). Build passes; Playwright 4/4 passed in three consecutive runs.
- **Deviations:** Conditions whose model output cites nothing are not stored (principle 2) and are reported as job limitations. The UI then shows "No hypothesis yet" rather than an evidence-free "insufficient evidence" card. A bipolar `likely` is allowed only for a bipolar I/II pattern.
- **Next:** Phase 5 tooling (user-run): LongCounsel-8 preparation, subsets, benchmark, report, freeze, run_final, runbook; Claude runs only the 2-session pilot.

### Phase 5 — 7 November 2026 (LongCounsel-8 tooling; user-run execution model)
- **Built:** `services/progress/evaluation/longcounsel.py` (manifest SHA-256 verification, licence and README check, streaming `.tar.zst` reader with no extraction into `raw/`, transcript allowlist leakage guard plus a questionnaire-term scan, `Counselor→CLINICIAN`/`Client→CLIENT`, synthetic timestamps `k·10 s`, separate label files, prepared manifest with hashes); `subsets.py` (`select`: fixed seed-42 stratified manifests `val-arms-12` and `test-final-37` by **derived** trajectory type, printing counts and large-change transitions and refusing to overwrite; `complement`: `val-rest-25` for optional calibration); `benchmark.py` (`pilot`, `estimate`, `run`, `status`, `freeze`, `calibrate`; checkpoint per session with atomic writes; automatic resume; run-id bound to config (code hashes, model SHA-256, NOTE_* settings, subset hash) with a refusal listing the differences; per-run process lock; safe SIGINT/SIGTERM with no partial checkpoint and no cancelled Stage A cached, and a second signal forcing exit; `run.log` with ETA and no transcript text; Stage A cache per evidence setting shared across history arms; Stage B depression only; test manifests only via `run_final`); `report.py` (all §8.3 metrics with 2,000-resample trajectory bootstrap, partial runs marked incomplete, cross-fitted calibration, baselines, citation-rating CSV with empty rating columns, per-session drill-down); `calibration.py` (isotonic/Platt, cross-fitting by trajectory, Brier, reliability; active mapping used only for `lc8-demo-` patients); `run_final.py` (frozen config and code-hash checks, no overwrite, resumable); `services/api/import_longcounsel_demo.py` (backup, validation/train only, transcripts only, idempotent); research API `GET /research/progress/runs[/{id}/report]` and `/workspace/research-progress` (metrics with CIs, threshold table, ROC, reliability, MAE by type, per-trajectory GT vs AI plots, per-session cited findings); `docs/PROGRESS_BENCHMARK_RUNBOOK.md`; `.gitignore` rules (subset manifests and frozen config committable; raw/prepared/runs/calibration local); fictional fixture `tests/fixtures/longcounsel_mini/`.
- **Fix found by the pilot:** a LongCounsel-8 session prompt is about 3.1k tokens, so the fixed 1,100-token output reservation made the full-transcript arm ineligible on every session. Stage A now plans each group into chunks whose output reservation scales with their tasks (`plan_chunks`), with `Recovery` as the truncation fallback.
- **Data prepared (no GPU, 3 s):** 369 trajectories, 258/37/74, no hidden fields present (`dropped_fields: {}`), 36 dialogue rows using words such as "scale"/"score".
- **Pilot:** three runs by Claude, the only GPU job allowed. Run 1 exposed the full-transcript ineligibility (fixed with `plan_chunks`) and was stopped. Run 2 exposed Stage B truncation: a whole-condition answer exceeded 1,250 tokens next to a 2.7k-token packet, so all three calls were discarded. **Fix:** Stage B is now planned criteria chunks followed by one synthesis call over the cited findings, with per-task output reservations raised to the observed sizes. Run 3 passed: Stage A full 194–246 s per session (18–30 calls), retrieval 170–180 s (6–7 calls), Stage B 70–85 s (3 calls), peak VRAM 3.0 GiB. All four arms are estimated at ≈ 11.7 GPU-hours and the final half-test run at 12.6–14.9 h (runbook table). **Quality signal (not tuned):** 29 of 32 PHQ item estimates abstained on a low-severity trajectory (GT PHQ-8 = 4); the model's "possible" labels were downgraded to insufficient evidence by post-processing, as designed. Changing the item prompt is left to the user (development decision). The clinical job deadline is now reset per stage and per condition (NOTE_JOB_TIMEOUT per stage), because Stage B is several calls per condition.
- **Tests:** 281 passed (fixture-based: preparation, leakage guard, checksum and licence checks, subset determinism, stratification and no-overwrite, hand-computed MAE, change MAE, tie-wrong direction accuracy, stratification, AUROC 18/24, threshold metrics, item coverage, bootstrap determinism, calibration; benchmark checkpoint and resume, labels never in prompts, Stage A cache reuse, config-mix refusal, test-manifest refusal, stop with no partial checkpoint and no cached cancelled Stage A, partial and complete reports, freeze and run_final guards, estimate arithmetic; demo import; full-transcript chunk planning; criteria chunking). Build passes; Playwright 4/4 passed.
- **Not run (user runs them):** subset selection, the four arms, reports, freeze, calibration enlargement, final half-test run.
- **Next:** the user runs the benchmark from `docs/PROGRESS_BENCHMARK_RUNBOOK.md`. Phase 6 (vignette infrastructure and authoring guide, `docs/PROGRESS_MONITORING.md`, PROJECT_STATUS and integration-guide updates, footer and API-description wording for the scope change) is still to do.

### Stage A prompt v2 — 7 November 2026
- Subsets locked and committed before any prompt work (`1c04d55`). Diagnosis of v1: 29/32 PHQ items abstained by the model, 0 withheld by validation; cause is the explicit-statement requirement (not retrieval, not token limits). `progress-extract-v2` (indirect behavioural evidence, three-case rule, frequency anchors, `evidence_strength`, item-specific definitions with exclusions) is now the default; v1 and `v2-attempt1` are kept reproducible via `PROGRESS_EXTRACT_PROMPT`. The benchmark config and Stage A cache key record the prompt version.
- Pre-registered pilot (`docs/PROGRESS_PROMPT_V2_PILOT.md`, 6 `val-rest-25` sessions by GT band): attempt 1 failed 4/5 criteria; revision 1 (attempt 2) passed 5/5: coverage 62%, low errors 3.0/0.0, direction 4.0 < 8.0 < 12.0, 0 invalid/withheld, 205 s/session. Item-level caveats recorded (items 6–8 over-rated, items 1/3/5 under-covered; totals partly agree through offsetting errors).
- Full-transcript Stage A with v2 is estimated ineligible on all 60 arm sessions at 4,096 context; the runbook runs only the two retrieval arms (`val12-v2-retrieval-current`, `val12-v2-retrieval-prior`) and lists the options for the evidence comparison. The run log warns per session when groups are not extracted.
- Tests: 286 passed.

### Demo data, chart styling and setup — 7 November 2026
- The user ran both v2 retrieval arms on `val-arms-12` (60/60 sessions each). No reports, freeze or calibration yet.
- `import_longcounsel_demo --with-results RUN_ID` attaches a validation run's Stage A cache and Stage B checkpoints to demo patients as AI-generated values pending review (`14bbf97`). The consent field `source_session_index` is now stored as a string, which fixes a 500 on the session list (`f39a40f`).
- Progress charts moved to a neutral clinical palette independent of the app theme (`43a976a`).
- `scripts/setup.sh` performs a pinned, SHA-256-checked setup of a fresh laptop: system packages, venv, npm, whisper.cpp, the speech model, the llama.cpp b12079 CUDA/CPU release, Qwen3-4B, LongCounsel-8 primary edition and prepare, migrations, transcript-only demo patients, tests. `--check` reports missing parts. README rewritten around it; PROJECT_STATUS has a 7 November update.
- No new dependencies.

### Phase 6 — 8 November 2026 (vignette infrastructure, print view, docs, scope wording)
- **User decision:** the full-transcript arms (`full-current`, `full-prior`) are not run; v2 Stage A is estimated ineligible on them at 4,096 context. Reports, freeze, calibration and the final half-test run stay optional user-run steps (runbook updated).
- **Built:**
  - `services/progress/evaluation/vignettes.py`:
    - Pydantic schema for `case.json`, `sessions/s<n>.json` and `labels.json`. Transcript turns allow only `turn_id`, `speaker` and `text`, so any extra field is rejected as possible label leakage.
    - Registry cross-checks: enabled conditions, criteria per condition, subtypes, domains and 0–3 ratings. `blind_to_model_output` is required.
    - `validate` prints the case mix.
    - `run`: resumable, with a per-case checkpoint, config binding (code, model, prompt and case-content hashes) and safe stop. It runs Stage A on every session, then Stage B for all four enabled conditions as of the last session with prior history. It never reads `labels.json`.
    - `report`: per-condition sensitivity, specificity, PPV, NPV and F1 at the `possible` and `likely` thresholds, with case-bootstrap CIs; subtype agreement; criterion confusion, agreement, abstention and κ; session domain MAE, exact and within-1 agreement and abstention; `citation_rating.csv` with empty rating columns. Cases edited after the run are excluded and listed.
  - Research API: `GET /research/progress/vignette-runs[/{id}/report]`.
  - `VignetteResults` section on `/workspace/research-progress`.
  - Fixture `tests/fixtures/vignettes_mini/fx-worry-001` (fictional, test-only).
  - `data/research/vignettes/README.md`, with a `.gitignore` exception for the README only.
  - Docs: `docs/VIGNETTE_AUTHORING_GUIDE.md` (roles and blinding, case mix, writing rules, formats, label definitions, commands, runtime estimate, interpretation) and `docs/PROGRESS_MONITORING.md` (module guide).
  - The print view now includes the criterion checklist with session/time references, "information still needed", flagged transcript text, and plans and follow-up.
  - Scope wording updated in `main.py` (API description), `AppShell` footer, `LandingPage`, README, PROJECT_STATUS (dated addendum) and MODULE_INTEGRATION_GUIDE §6a.
- **Tests:** 302 passed (+15: fixture validates; ten invalid-case rejections; scripted run checkpoints all four conditions, sends no label text, resumes with no calls and refuses edited cases; report metrics by hand on the fixture; binary metrics and κ by hand; research API list and report). Frontend build passes; Playwright progress suite 4/4. The research page vignette section and the print view were checked by screenshot.
- **Deviations:**
  - The plans and follow-up thread and the print view already existed from Phase 2; this phase completed the print content.
  - No vignette demo import into the clinical DB was built. The plan does not require one, and labels must stay out of the DB.
  - No real vignette cases or labels were written (plan §8.7: structure only).
- **No new dependencies.** No migration or contract changes.
- **Next (outside the plan):**
  - consultant verification of the §13 items;
  - writing and labelling the vignette set, then `vignettes run`/`report`;
  - optionally the LongCounsel-8 reports, freeze and final run.

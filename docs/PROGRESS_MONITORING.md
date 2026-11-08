# Progress monitoring and condition hypotheses

This module tracks a patient's symptoms and functioning across sessions and produces AI-generated, evidence-cited
hypotheses for four conditions:

- bipolar disorder (manic, hypomanic and depressive episode features);
- generalized anxiety disorder (GAD);
- social anxiety disorder (SAD);
- major depressive episode.

Everything runs locally on the existing llama.cpp runtime and Qwen3-4B model. Design, decisions and the full progress
log are in [PROGRESS_MONITORING_PLAN.md](PROGRESS_MONITORING_PLAN.md).

> **Scope change.** Earlier project documents say Pshynox does not diagnose. This module adds automatic,
> criterion-level condition hypotheses. They are labelled as AI hypotheses for clinician review, not diagnoses. The
> supervisor and the clinical consultant must agree to the scope change before the final report; see plan §1.

## 1. What the clinician sees

On a patient's **Progress** tab (`/workspace/patients/<id>/progress`):

| Section | Content |
|---|---|
| Header | Session count, values pending review, registry versions, **Re-run hypotheses**, **Print / Save as PDF** |
| Clinical hypotheses | One card per condition: label (`likely`, `possible`, `unlikely`, `insufficient evidence`), bipolar subtype pattern, criteria met / required, duration and impairment. **Why?** opens the criterion checklist with cited turns. Also rule-outs, differentials and "information still needed". Accept, edit or reject. |
| Flagged statements | Statements about harm to self or others, each shown as a transcript excerpt with the flagged turn highlighted. Never scored. "No statements flagged" is not a risk assessment. |
| PHQ-9 / GAD-7 charts | Clinician-administered totals (solid blue) and AI estimates (dashed orange), severity bands, gaps for unassessed sessions, change markers, item tooltip, table view, **Add administration**. AI PHQ estimates cover items 1–8 (PHQ-8); item 9 is clinician-only. |
| Symptom and functioning domains | 20 domains × sessions, rated 0–3. Cells show denied, conflicting, insufficient and not-assessed states distinctly. Clicking a cell opens the cited turns. |
| Prediction timeline, changes, session comparison | Label history per condition. Detected changes (reliable change, rating jumps, emergence, label changes) to acknowledge. Two-session comparison. |
| Plans and follow-up | Each session's plan items and whether the next session discussed them (done, partly done, not done, not discussed). |

Inside a session, the **Observations** panel has **Analyse this session**. It runs Stage A, and then Stage B for the
patient. The panel shows that session's domain cards, questionnaire item estimates and flagged statements, and it
allows manual entry.

**Two clicks to the source.** From a hypothesis card, **Why?** shows the criteria. Each citation then gives **Play**
(audio at that timestamp, when a recording exists) or **Open in transcript** (scrolls to and highlights the turn).

## 2. Rules the module keeps

1. **Explainable.** Every prediction is built from criterion findings that cite transcript turns. A prediction with no
   cited evidence is not stored.
2. **AI results count immediately.** AI values enter charts, trends and change detection at full weight. Review status is
   a badge, not a filter.
3. **Review badges.** Every AI value shows *AI-generated · human review pending* until a named reviewer acts:
   - accepted values read *Reviewed by …*;
   - edited values read *Edited by …*: the edit replaces the value and the original AI output is kept;
   - rejected values read *Rejected by …*: the value leaves the active series but stays in history.

   Every action writes an audit event.
4. **Abstention is explicit.** `insufficient_evidence` is shown as such. Missing data are gaps, never zeros, never
   interpolated.
5. **Risk is surfaced, not scored.** There is no numeric risk field anywhere. The manual risk-review gate on notes is
   unchanged.
6. **Staleness.** Editing a cited transcript turn marks the dependent values *Sources changed — regenerate or review*,
   and they leave the active series until regenerated or re-reviewed.
7. **Decided notes lock their session's values.** Pending reviews never block note approval.
8. **Local only.** No hosted inference and no network at runtime.

## 3. How the AI pipeline works

**Stage A** (per session; `services/progress/extraction.py`, `validation.py`, `evidence.py`):

- Four group calls: depressive domains with PHQ items 1–8; manic and hypomanic domains; anxiety domains with GAD-7;
  functioning with risk statements and plan follow-ups.
- **Evidence:** the full transcript when it fits 4,096 tokens, otherwise BM25 retrieval with ±2 context turns.
- **Prompt:** `progress-extract-v2` by default. It accepts indirect behavioural evidence and uses frequency anchors.
  `PROGRESS_EXTRACT_PROMPT=v1` reproduces the earlier prompt.
- **Deterministic validation**, before anything is saved:
  - aliases map back to known turns, and foreign ids are rejected;
  - a client turn is required for a rating or a denial;
  - statements about other people are guarded;
  - quotations over 15 words and personal names are rejected;
  - timeframe is kept, so past episodes are not plotted as the current rating.

**Stage B** (per patient, per condition; `prediction.py`, `postprocess.py`):

- **Input:** all of the patient's sessions up to the chosen one, as compact observations plus criterion retrieval over
  every session. Sources carry patient-wide aliases.
- **Calls:** criteria are assessed in chunks, followed by one synthesis call over the cited findings.
- **Post-processing in code:**
  - recount met criteria;
  - downgrade `likely` unless count, core criterion, duration and impairment are all cited;
  - allow `unlikely` only with cited counter-evidence;
  - apply the bipolar subtype rules (I needs a manic period; II needs hypomanic plus depressive; cyclothymic needs a
    two-year course);
  - strip uncited rationale sentences.
- Every change is recorded and shown on the card.

**Jobs** (`jobs.py`):

- They share the note-generation lock and memory slot, so only one model job runs at a time.
- They checkpoint each group, so **Retry** resumes.
- A result is withheld if the transcript changed while the job ran.

Criterion summaries are paraphrased in `services/progress/registry/conditions.json`, citing DSM-5-TR sections, and are
marked *pending clinical consultant verification*. Domains and instruments are in the same folder.

## 4. API (`/api/v1`)

| Purpose | Endpoints |
|---|---|
| Dashboard | `GET /patients/{id}/progress` |
| Analysis jobs | `POST /sessions/{id}/progress-extraction`, `POST /patients/{id}/condition-predictions`, `GET /progress-jobs/{id}`, `GET /sessions/{id}/progress-jobs`, `POST /progress-jobs/{id}/cancel`, `POST /progress-jobs/{id}/retry` |
| Observations | `GET /sessions/{id}/domain-observations`, `PUT /sessions/{id}/domain-observations/{domain}` (manual), `POST …/{obs_id}/review` |
| Questionnaires | `GET, POST /patients/{id}/instrument-scores`, `POST …/{score_id}/review` |
| Hypotheses | `GET /patients/{id}/condition-predictions[/{pid}]`, `POST …/{pid}/review` |
| Flags, plans, changes | `GET /patients/{id}/risk-statements`, `GET /patients/{id}/plan-followups` (each with `…/review`), `POST /patients/{id}/change-events/{eid}/acknowledge` |
| Audit, registry | `GET /patients/{id}/audit-events`, `GET /progress/registry` |
| Research results | `GET /research/progress/runs[/{id}/report]`, `GET /research/progress/vignette-runs[/{id}/report]` |

Errors use the usual `{"error": {"code", "message"}}` envelope. Contracts are in `services/api/schemas.py`;
migration `0008_progress_monitoring` holds the tables.

## 5. Evaluation status (8 November 2026)

| Evaluation | Status |
|---|---|
| LongCounsel-8 tooling (preparation, subsets, benchmark, report, freeze, calibration, `run_final`) | Built and tested on fixtures |
| Stage A prompt v2 pilot (6 `val-rest-25` sessions) | Passed all 5 pre-registered criteria |
| Benchmark arms on `val-arms-12` | `retrieval-current` and `retrieval-prior` completed (60/60 sessions each, run by the user). **The full-transcript arms were not run (user decision, 8 November):** v2 Stage A is estimated ineligible at 4,096 context. |
| Reports, freeze, calibration, final half-test run (`test-final-37`) | Not run. The commands are ready in [the runbook](PROGRESS_BENCHMARK_RUNBOOK.md). |
| Vignette set (bipolar, GAD, SAD, depression) | Tooling built ([authoring guide](VIGNETTE_AUTHORING_GUIDE.md)); **no cases written yet** |

Development observations from the arm checkpoints, with no report generated yet:

- In most sessions the model marked the core depression criteria as insufficient evidence.
- Post-processing therefore downgraded most depression labels, so label metrics are not yet informative.
- With prior-session history at 4,096 context, Stage B failed on later sessions.

## 6. Settings

| Variable | Default | Meaning |
|---|---|---|
| `NOTE_LLAMA_SERVER`, `NOTE_LIBRARY_PATH`, `NOTE_GPU_LAYERS`, `NOTE_CONTEXT` | see `.env.example` | Shared LLM runtime (also used by note drafting) |
| `NOTE_JOB_TIMEOUT` | `1800` | Seconds per stage, and per condition in Stage B |
| `PROGRESS_EXTRACT_PROMPT` | `v2` | Stage A prompt (`v1` and `v2-attempt1` kept for reproducibility) |
| `PROGRESS_RUNS_DIR`, `PROGRESS_SESSION_TIMEOUT` | `data/research/progress-runs`, `1800` | Benchmark runs |
| `PROGRESS_VIGNETTES_DIR`, `PROGRESS_VIGNETTE_RUNS_DIR` | `data/research/vignettes`, `data/research/vignette-runs` | Vignette cases and runs |
| `PROGRESS_CALIBRATION_DIR` | `data/research/progress-calibration` | Calibration mappings |

Calibrated probabilities (LongCounsel-8, depression only) appear only in the research view and for `lc8-demo-`
patients. Clinical patients get `probability = null`, because of the dataset's terms (plan Q3).

## 7. Limitations

- **Evaluation coverage:** LongCounsel-8 is synthetic and depression-only. Bipolar, GAD and SAD hypotheses are
  unvalidated until the clinician-labelled vignette set is written and evaluated.
- **Clinical content:** criterion summaries, the domain list, the GAD-7 reliable-change threshold, the domain change
  rule (Δ ≥ 2) and the label definitions await the consultant's verification (plan §13).
- **Model and language:** the model is a 4B local model with a 4,096-token context. Prompts are English; Urdu is
  untested.
- **Reviewer identity:** names are self-reported, with no authentication.
- **Citations:** structural reference checks confirm a cited turn exists, not that it supports the claim. Citation
  rating sheets are exported for human judgement.
- **Not production-ready:** this is a prototype for a final-year project, not a clinical system.

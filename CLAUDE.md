# CLAUDE.md

Repository guidance for Claude Code sessions in Pshynox. Read `README.md` and
`docs/PROJECT_STATUS.md` for general context. Tests: `.venv/bin/python -m pytest -q`.
Frontend build: `npm run build --prefix apps/web`.

## Progress monitoring module

**Plan and handover document:** [`docs/PROGRESS_MONITORING_PLAN.md`](docs/PROGRESS_MONITORING_PLAN.md).
Read all of it, including its "Open questions" and "Progress log", before starting any phase.
Work on branch `progress-monitoring` (it replaced `feat/progress-monitoring`). Do one phase per session.

### Principles (must hold in every phase)

1. **Target conditions (from the proposal).** Primary: **bipolar disorder** (manic, hypomanic and depressive episode features). Secondary: **generalized anxiety disorder (GAD)** and **social anxiety disorder (SAD)**. Also include **major depressive episode / depression**, because it is a component of bipolar assessment and is the only condition the available dataset can evaluate. Do not add conditions the proposal excludes (bulimia is explicitly excluded). Keep the condition list configurable.
2. **Every prediction is explainable.** Each prediction is built from criterion-level findings. Each finding cites segment IDs from the transcripts (any session of that patient), with timestamps, speaker roles and a one-sentence paraphrased basis. A prediction with no cited evidence cannot be produced. The UI must let the clinician go from prediction → criterion → source turn → audio playback in two clicks.
3. **AI results are first-class results.** AI-generated observations, scores and predictions appear in charts, trends, change detection and the dashboard immediately, with full weight. The computation does not hide, exclude, down-weight or treat them as provisional.
4. **Human review status is a label, not a filter.** Every AI-generated value carries a visible status badge, **"AI-generated · human review pending"**, until a named reviewer accepts, edits or rejects it. After review the badge changes ("Reviewed by …", "Edited by …"). Rejected values are removed from the active series but kept in history. Edited values replace the AI value in the active series, and the original AI output is retained immutably.
5. **Provenance is always recorded.** Use the existing `Provenance` enum (`MANUAL`, `AI_GENERATED`, …). Keep the original AI output and the model/prompt/schema/registry versions, and write audit events for every review action.
6. **Abstention is allowed and explicit.** The model may return `insufficient_evidence` for a criterion or condition. This is shown as such and never silently converted into "absent". Missing data are gaps: never zeros, never interpolated.
7. **Risk content is surfaced, not scored.** The model flags any transcript statements about self-harm, suicide or harm to others as **risk statements for clinician review**, with citations, in a dedicated panel. It does not produce a numeric risk score or a "no risk" conclusion. Absence of flagged statements is displayed as "no statements flagged", never as "safe". The existing manual risk-review gate on notes is unchanged.
8. **Staleness.** If a cited segment's revision changes, the dependent observation/finding/prediction is marked `stale` (badge "sources changed — regenerate or review") and is regenerated or re-reviewed. Hook into the same path that marks note excerpts stale.
9. **Local only.** No hosted inference, no CDN assets, no network at runtime. Reuse the existing llama-server runtime, memory lock, token budgeting, recovery and checkpoint code. Do not create a second LLM runtime.
10. **Research data never enters the clinical DB**, except as clearly labelled demo patients via an explicit import command (same pattern as `import_annomi.py`). Ground-truth labels never enter the clinical DB.
11. **Criteria text.** Write short paraphrased criterion summaries in the registry in your own words. Do not paste verbatim DSM-5-TR or ICD-11 text. Each condition definition cites its source (DSM-5-TR section) and is marked "pending clinical consultant verification".

### Working style

- At the end of each phase: run the backend tests and the frontend build, commit with a descriptive message, update the "Progress log" in the plan doc (what was done, test results, deviations, next phase), then stop and summarise.
- Use a task list for the steps within a phase. Add tests in every phase, and keep the frontend build and backend tests passing.
- **Ask the user before:** changing an existing migration, changing an existing public contract in `services/api/schemas.py` non-additively, or deleting files. The one approved exception (7 Nov 2026) is that `AuditEvent.session_id` becomes optional and gains `patient_id` (plan §4, Q1).
- **Dependencies:** the user authorised (7 Nov 2026) installing whatever this module needs. Declare each one in `pyproject.toml` / `apps/web/package.json` and list it in the phase's progress-log entry. Registries are JSON.
- Subagents may do read-only exploration only. Make the edits yourself.
- Never start long GPU jobs (benchmarks, full extraction runs) without first telling the user the expected runtime. Run them in the background with checkpointing and resume support.
- **Phase 5 execution model (user decision, 7 Nov 2026):** Claude runs **only the 2-session timing pilot**. For subset selection, benchmark arms, reports, freeze, calibration runs and the final 37-trajectory test run, write scripts and exact commands in `docs/PROGRESS_BENCHMARK_RUNBOOK.md` for the user to run (plan §8.5).
- Calibrated probabilities from LongCounsel-8 are shown only in the research view and for `lc8-demo-` patients. Clinical patients get `probability = null` (plan Q3 default, until the user says otherwise).
- Never stage or modify `PSHYNOX_PROJECT_REPORT.md` (the user's untracked file).
- Do not download LongCounsel-8 (it is already under `data/research/datasets/longcounsel-8/raw/`). Never fabricate results. Never run the held-out test split.

### Phases (stop after each)

1. Phase 0: plan mode; plan doc with scope-change section and `CLAUDE.md` rules; wait for approval.
2. Phase 1: registries, migration, contracts, trend engine, manual entry APIs, tests.
3. Phase 2: dashboard and Observations panel with manual data and the badge system.
4. Phase 3: Stage A extraction, risk statements, staleness integration.
5. Phase 4: Stage B condition prediction, hypotheses panel, prediction timeline.
6. Phase 5: LongCounsel-8 importer, demo import, subset selection, benchmark/report/freeze/run_final CLIs, calibration, results view, `docs/PROGRESS_BENCHMARK_RUNBOOK.md`; Claude runs only the 2-session pilot (plan §8.5).
7. Phase 6: vignette-set infrastructure and authoring guide, plans/follow-up thread, print view, docs.
8. Claude does **not** run the held-out test split. The user runs the final half-test run (`test-final-37`) via `run_final`; the other 37 test trajectories stay unused. Stop and report the frozen config.

Final report (after Phase 6): what was built per phase, test/build results, UNVERIFIED steps, development results with caveats, and open questions for the clinical consultant (criteria summaries, domain list, thresholds, prediction label definitions, vignette case mix).

## Internet meetings module

**Plan and handover document:** [`docs/INTERNET_MEETINGS_PLAN.md`](docs/INTERNET_MEETINGS_PLAN.md). Read all of it,
including its open questions and progress log, before starting a phase. Work on branch `feat/internet-meetings`. Do one
phase per session: commit, update the progress log, stop. Ask before adding dependencies.

### Rules (must hold in every phase)

1. **No clinical content on third-party infrastructure.** Audio, video, chat, transcripts and notes never pass through
   any server in readable form. Signaling may carry metadata only (SDP, ICE candidates, meeting id, hashed tokens), never
   patient names, session titles or transcript text.
2. **The public surface is a separate server.** Funnel traffic reaches the laptop from `localhost`, and the existing API
   treats loopback as the trusted clinician, so the clinician API must **never** be exposed.
   - A separate ASGI app on its own port serves only:
     - the patient meeting page (production build);
     - invitation validation;
     - patient consent;
     - signaling for that meeting;
     - TURN credentials for a valid invitation.
   - Every request to it is untrusted.
   - It shares state with the clinician API only through a narrow internal interface.
   - Tests must prove that clinician routes (patients, sessions, transcripts, notes, research, progress, uploads) are
     unreachable through it.
3. **Same-Wi-Fi mode keeps working.** `scripts/run_lan.py` and its tests must still pass. Internet mode is a new
   launcher/mode, not a replacement.
4. **Don't touch the progress benchmark.**
   - Do not edit anything under `services/progress/`, its tests or `data/research/progress-*`.
   - Do not run anything that loads the LLM or the Whisper model: a benchmark run may be in progress, and its code hash
     must not change.
   - If a change there seems necessary, stop and ask.
5. **Secrets.**
   - TURN API keys live only in `.env`, which is git-ignored; a test fails if `.env` is ever tracked or staged.
   - The keys are read by the backend only. The browser receives only short-lived TURN credentials.
   - `.env.example` gets placeholders, never real values.
   - Never print secrets in logs, test output or messages. Report a value only as "set" or "missing".
6. **Local only for processing.** No hosted transcription or inference. No CDN assets in the patient page.
7. Commit messages carry no co-author trailer (user preference).

## DSM registry (progress v2)

**Plan and handover document:** [`docs/PROGRESS_DSM_REGISTRY_PLAN.md`](docs/PROGRESS_DSM_REGISTRY_PLAN.md). Read all of
it, including its open questions and progress log, before starting a phase. Work on branch `feat/progress-dsm`. Do one
phase per session: commit, update the progress log, stop. Ask before adding dependencies. The progress-module
principles above still apply.

1. **No verbatim DSM-5-TR text.** Store DSM ids, ≤ 20-word paraphrases in our own words, chapter names and ICD codes.
   Never copy criteria wording, tables or text. `docs/DSM*.pdf` is git-ignored and must never be committed. Use it only
   to check criteria structure.
2. **Verification.** Every criterion item and condition rule has `verification` (`pending|verified|changed`). All start
   `pending`. Clinician decisions are stored in `registry_verifications`, bound to the item's content hash. The import
   never changes ids.
3. **Protect the benchmark.** Never edit the 14 files listed in `STAGE_A_FILES`/`STAGE_B_FILES` of
   `services/progress/evaluation/benchmark.py`, and never edit the v1 registry. A test pins their hashes to the
   `val12` run configs. The DSM pipeline lives in new files (`services/progress/dsm/`,
   `services/progress/registry/dsm/`) and may import v1 helpers but not change them. Nothing under
   `services/progress/evaluation/` imports the DSM code. If a change would alter a bound hash, stop and ask.
4. **Code computes labels.** The model proposes criterion statuses with citations; `dsm/rules.py` computes every label,
   specifier and checklist line. A cited client denial makes a criterion `conflicting`. Labels: likely, possible,
   insufficient evidence or not met, always with the footer "Hypothesis for clinician review; not a diagnosis."
5. **Selection.** `PROGRESS_REGISTRY=v1|dsm`. The clinical default becomes `dsm` only after Phase 3 passes. The
   benchmark always uses v1. v1 rows are never converted; they are shown as "Earlier (non-DSM) tracking".
6. **Prompt versions:** `progress-extract-dsm-v1`, `condition-predict-dsm-v1`. No instrument is enabled until its
   usage terms are confirmed (WHODAS 2.0, the Level 1 Cross-Cutting measure and mania scales are extension points).
7. Commit messages carry no co-author trailer (user preference).

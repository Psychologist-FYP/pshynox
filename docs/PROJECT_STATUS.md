# Pshynox — Current Project Status

## Update, 8 November 2026 (evening): internet meetings (branch `feat/internet-meetings`)

Two-person meetings now also work over the internet; same-Wi-Fi meetings are unchanged for users. See
[INTERNET_MEETINGS.md](INTERNET_MEETINGS.md).

| Item | Status |
|---|---|
| Public meeting-only server | Built. A separate app on its own loopback port, reached through Tailscale Funnel, serves only the patient page, consent, signaling and ICE. The clinician API is never exposed: it also refuses public-host and forwarded requests in internet mode. |
| NAT traversal | Cloudflare TURN credentials issued server side (short-lived, TTL ≤ invitation, revoked at the end); optional Metered; STUN-only fallback with a warning; audio-only option; connection type and monthly relayed-traffic estimate. |
| Encrypted data channel | Chat, media state and recording control moved off signaling. A 6-digit verification code from both DTLS fingerprints; the clinician's confirmation is audited. |
| Patient-side clean recording | The patient's browser records its own microphone and sends 60 s chunks over the data channel: encrypted at rest until acknowledged, SHA-256-verified, clock-aligned. Network-received audio is a labelled fallback. |
| Pre-existing chunk-gap bug | Fixed: skipped audio render quanta are padded in the worklet. |
| Single-recording limit | Raised from 5 to **45 minutes** (and 100 MB to 600 MB) for browser recordings and imported files; the panel stops and saves automatically at the limit. Online meetings were never limited to 5 minutes (they upload 1-minute chunks). Research clips stay at 5 minutes. |
| Verification | Backend 337 passed; unit 14; LAN meeting Playwright 4/4; internet-mode Playwright 1/1 (two browsers, HTTPS public server, mocked ICE). |
| **Unverified** | Real networks: Funnel WebSockets, mobile data, TURN relaying, 30-minute calls, recovery from a network drop, transcript quality by source. See [the manual checklist](INTERNET_MEETINGS_TEST_CHECKLIST.md). |

---


## Update, 8 November 2026: progress-monitoring plan complete (Phase 6)

All implementation phases of the [progress-monitoring plan](PROGRESS_MONITORING_PLAN.md) are now built. User
documentation is in [PROGRESS_MONITORING.md](PROGRESS_MONITORING.md).

| Item | Status |
|---|---|
| Vignette-set infrastructure | Built: schema and validator, resumable run (Stage A + all four conditions), report, results view on `/workspace/research-progress`, one fictional test fixture, [authoring guide](VIGNETTE_AUTHORING_GUIDE.md). **No clinical cases or consultant labels exist yet.** |
| Print view | Completed: hypotheses with criterion checklist, cited references and "information still needed"; flagged statements with transcript text; questionnaires; domains; changes; plans and follow-up |
| Plans and follow-up thread | Built (Phase 2 UI, Phase 3 producer) |
| Scope-change wording | API description, workspace footer, landing page and README now describe AI condition hypotheses for clinician review, not diagnoses (draft wording; see below) |
| Benchmark | Both retrieval arms on `val-arms-12` complete. **Full-transcript arms not run (user decision, 8 November).** Reports, freeze, calibration and the final `test-final-37` run are optional user-run steps, not done. |

**Scope-change addendum.** §1 below says the system "does not implement automated diagnosis". That is no longer true
in the same sense. The progress module automatically produces criterion-level condition hypotheses (bipolar, GAD,
social anxiety, depressive episode) from transcripts:

- each hypothesis cites transcript turns;
- each is labelled *AI-generated · human review pending*;
- the clinician remains the decision-maker;
- risk is flagged, never scored.

The new wording is a draft. It needs agreement from the supervisor and the clinical consultant before the final report
(plan §1).

**Verification (8 November 2026):** backend `pytest` 302 passed; frontend build passes; progress Playwright tests 4/4
pass.

**Still open:**

- the consultant's verification of criteria, domains, thresholds and label definitions (plan §13);
- writing and labelling the vignette set, then running it;
- the optional LongCounsel-8 reports, freeze and final run;
- authentication and encryption at rest;
- Urdu evaluation.

---

## Update, 7 November 2026 (branch `progress-monitoring`)

This section summarises the current state. The sections below it keep the October baseline assessment and the earlier
dated updates unchanged. Setup on a new laptop is now one command, `./scripts/setup.sh`; see the [README](../README.md).

| Area | Status | Notes |
|---|---|---|
| Patient workspace, sessions, consent | Implemented | Unchanged since October. |
| Recording, WAV import, local transcription (Module A) | Implemented | whisper.cpp v1.8.3, CPU, multilingual base model. Urdu quality not evaluated. |
| Evidence retrieval (Module B) | Implemented: 5 methods | BM25 (default), TF-IDF, optional E5 and hybrid, plus a research annotation workspace. Human relevance annotation and real-dataset quality numbers are outstanding. |
| AI note drafts | Implemented | Qwen3-4B-Instruct-2507 Q4_K_M via llama.cpp. GPU verified on a GTX 1650 (37/37 layers, about 3 GiB VRAM). Structured v3 note with explicit review. Clinical quality is not evaluated. |
| Same-Wi-Fi meetings | Implemented for LAN demo | Unchanged. |
| Progress monitoring (dashboard, trends, review badges) | Implemented (phases 1–2) | PHQ-9/PHQ-8 and GAD-7 trend charts, domain heatmap, change markers, manual entry, accept/edit/reject review with audit, staleness. Charts restyled in a neutral clinical palette (7 Nov). |
| Stage A extraction (per-session observations) | Implemented (phase 3) | Domains, questionnaire item estimates, risk statements, plan follow-ups; every value cites segment IDs. Prompt `progress-extract-v2` is the default and passed its pre-registered pilot (5/5 criteria). |
| Stage B condition hypotheses | Implemented (phase 4) | Bipolar features, GAD, social anxiety, major depressive episode. Criterion-level findings with citations; explicit abstention. Bipolar, GAD and SAD are **unvalidated**. |
| LongCounsel-8 benchmark tooling | Implemented (phase 5) | Preparation, fixed subsets, resumable benchmark arms, reports with bootstrap CIs, calibration, freeze, `run_final`, research results page, labelled demo-patient import (optionally with a run's AI outputs). |
| Benchmark execution | In progress (user-run) | Both v2 retrieval arms on `val-arms-12` completed 60/60 sessions. Reports, freeze, calibration and the final half-test run are **not done yet**. |
| Phase 6 | Not started | Vignette set and authoring guide, plans/follow-up thread, print view, final docs. |
| Clinician authentication, encryption at rest | Not implemented | Reviewer names are self-reported. |
| Jetson deployment | Unverified | Laptop only. |

**Preliminary benchmark observations.** These come from inspecting the run checkpoints; no official report has been
generated yet.

- In most arm sessions the model marked the depression core criteria (depressed mood, anhedonia) as insufficient
  evidence.
- Post-processing therefore downgraded most Stage B depression labels, as designed. That makes the label metrics
  uninformative for now.
- The PHQ-8 AUROC is computed from the Stage A item total, not from the label.
- With prior-session history at 4,096 context, Stage B in the `retrieval-prior` arm failed on the later sessions.
- Treat these as development findings until the reports exist (`docs/PROGRESS_BENCHMARK_RUNBOOK.md`, steps 4–5).

**Verification on 7 November 2026:**

- backend `pytest`: **287 passed** on the development machine. On a fresh clone: 282 passed, 5 skipped; the skipped
  tests need the local retrieval starter pack and review-experiment files, which are not in Git;
- frontend build passes;
- progress Playwright tests 4/4 pass;
- `scripts/setup.sh` ran end to end in a fresh clone (CPU runtime).

**Next:** generate the two arm reports, then apply the pre-declared freeze rule ([PROGRESS_FREEZE_RULE.md](PROGRESS_FREEZE_RULE.md)),
then calibration, then the final `test-final-37` run (all user-run). Phase 6 follows. Questions for the clinical
consultant are listed in [PROGRESS_MONITORING_PLAN.md §13](PROGRESS_MONITORING_PLAN.md).

---

**Retrieval update, 2 November 2026:** The assessment below records the October
baseline. Module B now includes five retrieval methods and a separate research
annotation/evaluation workspace. Datasets are present and verified; human relevance
annotation and real-dataset quality measurements remain outstanding. See
[the current retrieval status](RETRIEVAL_IMPLEMENTATION_STATUS.md) and
[the research walkthrough](RESEARCH_EVALUATION.md) for the later implementation.

**Assessment date:** 26 October 2026  
**Repository baseline:** `b3bfaf1` (25 October 2026)  
**Project stage:** Integrated laptop prototype / FYP demonstration stage

## 1. Where the project stands

Pshynox has progressed beyond planning and isolated module scaffolding. The repository contains an integrated psychological session documentation application with patient intake, session history, audio capture/import, local speech transcription, editable transcripts, source-linked note drafts, and clinician review decisions. A two-person audio/video meeting workflow has also been implemented for devices on the same Wi-Fi network.

The application is designed to keep transcription and saved records local after initial installation. It uses a React frontend, a FastAPI backend, SQLite, local recording files, and a CPU-based whisper.cpp worker.

The main remaining functional integration is **Module B: the planned BM25/Boolean information retrieval engine**. The current application searches transcripts with a simple local keyword fallback. Language-quality evaluation, sustained meeting performance, physical-device acceptance testing, and Jetson deployment also remain unverified by this assessment.

This is a documentation-support prototype. It does not implement automated diagnosis or treatment recommendations, and its current local access controls do not make it a production clinical or telehealth system.

This report describes the code present in this checkout. “Implemented” means the relevant code and integration exist; verification results and their limits are recorded separately in section 7. No overall completion percentage is assigned because the repository does not define a measurable acceptance checklist for the complete FYP.

## 2. Feature status at a glance

| Area | Current status | What exists / what remains |
|---|---|---|
| Patient workspace | Implemented | Create/edit intake records, directory, patient details, and patient-scoped session history. |
| Session management | Implemented | Initial assessment/follow-up sessions, consent, generated identifiers, and linking legacy unassigned sessions. |
| Local recording and import | Implemented | Browser microphone selection/recording, WAV import, original-file storage, and validation. |
| Offline transcription | Implemented | FFmpeg preparation, multilingual Whisper via whisper.cpp, persistent jobs, retries, and saved segments. |
| Transcript review | Implemented | Text edits, manual speaker roles, revision tracking, and playback of source audio. |
| Evidence search | Partial | Session-scoped keyword search and source navigation; full BM25/Boolean retrieval is pending. |
| Note drafting | Implemented, limited scope | Editable structured sections and deterministic copying of client excerpts into subjective. No generated clinical conclusions. |
| Review and decisions | Implemented | Manual clinician review, approve/reject actions, stale-source checks, and workflow audit events. |
| Online meeting | Implemented for LAN demo | Two participants, audio/video, camera controls, chat, separate audio tracks, and background chunk transcription. |
| Database persistence | Implemented | SQLAlchemy models and seven Alembic migrations covering patient/session records, notes, audio/jobs, and meetings. |
| Jetson deployment | Unverified | Laptop setup exists; target-board installation, compatibility, resource use, and offline acceptance need testing. |
| Production access/security | Incomplete | Meeting invitations and LAN isolation exist; clinician authentication, verified identities, and encrypted record storage are not implemented. |
| Formal quality/performance evaluation | Pending evidence | Runtime metrics exist; no completed language-quality or target-hardware benchmark report was identified in the reviewed files. |

## 3. What has been developed

### 3.1 Patient records and workspace

The interface includes a landing page, application navigation, a patient directory, intake forms, patient pages, and session workspaces. The current theme includes custom imagery and a Dr. Katz-inspired visual presentation.

Patient records support demographic and contact details, emergency contacts, referral information, presenting concerns, previous care, medical history, medication, allergies, family/social history, substance use, goals, and additional notes. Only the name is required. Age can be derived from date of birth or recorded as age at intake.

Sessions belong to a patient when one is selected. Existing unassigned sessions can be linked without replacing their transcripts. The API prevents moving an already-linked session to a different patient. Recording consent is collected per session rather than inferred from patient registration.

**Evidence:** [patient routes](../services/api/patient_routes.py), [schemas](../services/api/schemas.py), [patient UI](../apps/web/src/features/patients/), [workspace documentation](PATIENT_WORKSPACE.md).

### 3.2 Audio capture, storage, and local transcription

The application supports recording through the browser or importing a WAV file. Ordinary recordings are limited to five minutes and 100 MiB (`100 × 1024 × 1024` bytes). Uploaded audio is checked for supported media, duration, and channel count. Original files are retained; FFmpeg prepares mono, 16 kHz signed 16-bit PCM for transcription.

The speech implementation runs a local whisper.cpp executable with a configurable multilingual model, language, thread count, and timeout. The current adapter explicitly uses CPU inference. It records runtime/model hashes, elapsed time, and real-time factor in job metrics.

The API persists recordings and jobs, and a single Linux worker processes queued jobs sequentially. Results are validated and committed with source links. Failed jobs expose errors and can be retried. Interrupted running jobs are marked failed for explicit retry. Additional successful recordings append transcript text without replacing earlier corrections.

Digital silence is detected before inference when the runtime/model are available. This is a basic silence check, not a completed voice-activity detection or hallucination-prevention evaluation.

Ordinary stereo uploads are mixed for ASR while retaining their originals. They are **not automatically separated by speaker**. Their transcript roles initially use `UNKNOWN`, and the clinician assigns `CLIENT` or `CLINICIAN` manually.

**Evidence:** [recording UI](../apps/web/src/features/sessions/RecordingPanel.tsx), [speech API](../services/api/speech_routes.py), [audio preparation](../services/speech/audio.py), [transcriber](../services/speech/transcriber.py), [worker](../services/speech/worker.py).

### 3.3 Transcript editing and evidence links

Transcript segments have stable identifiers, source timestamps, speaker roles, and revisions. Users can edit saved text and speaker roles, and play the associated recording at the relevant timestamp. Each persisted ASR segment is linked to its recording and job.

The session interface also provides evidence search and navigation to matching source segments. The active provider lowercases and splits a query, counts substring matches in each segment, and orders results by match count and timestamp. This is a working fallback, not BM25 ranking, a Boolean query parser, or a clinical risk detector.

**Evidence:** [session UI](../apps/web/src/features/sessions/SessionDetailPage.tsx), [retrieval provider](../services/api/retrieval.py), [API implementation](../services/api/main.py).

### 3.4 Structured notes and clinician review

Notes contain subjective, objective, assessment, plan, and functioning sections. Users can save manual text and source references. The populate action copies confirmed `CLIENT` transcript excerpts, with timestamps and segment references, into the subjective section. Other empty sections receive “Not documented.” Manual subjective content is protected from automatic replacement.

Source edits increment transcript revisions and invalidate affected excerpt review. Users must refresh or explicitly confirm changed sources before completing review. Review and approval are also blocked while transcription is queued/running or a meeting is active.

The workflow supports explicit manual risk-review confirmation, approval, and rejection with a reason. Decided notes and their transcripts become read-only. Workflow events record the supplied actor name and time. These names are self-reported local identifiers; they are not authenticated clinician identities. The manual review gate is not an automated risk assessment.

**Evidence:** [note/review API](../services/api/main.py), [note and audit models](../services/api/db.py), [review tests](../tests/api/test_review_workflow.py).

### 3.5 Same-Wi-Fi online meetings

A clinician can create a meeting from a session and share a patient invitation with another device on the same local network. The meeting supports one clinician and one patient, optional cameras, independent camera toggles, microphone controls, and chat. Signaling uses WebSockets; media uses browser-to-browser WebRTC.

The clinician browser captures its microphone and received patient audio as separate mono tracks. AudioWorklet produces standalone WAV chunks after 60 seconds of samples and flushes the final partial chunk when stopped. Pending chunks are stored in IndexedDB, and upload identifiers prevent duplicate recordings/jobs during retries. Uploaded chunks are persisted and queued for transcription while capture continues.

Meeting transcripts receive roles from the assigned audio track and meeting-relative timestamps. This provides track-based attribution, not automatic voice identification. Packet loss, echo, cross-talk, and chunk-boundary recognition errors still require review.

Invitations use role-specific tokens with a four-hour lifetime. Patient invitation pages do not mount the clinician workspace. The backend restricts LAN clients to meeting access under the documented development proxy setup. The launcher prepares local HTTPS certificates and starts the demo services.

Current limits:

- The documented setup is for the same Wi-Fi; reliable calls across different networks require additional networking configuration and validation.
- Signaling rooms are held in one API process; multiple API workers do not share live room state.
- The worker processes both incoming tracks sequentially, so transcription can fall behind capture.
- An abrupt browser shutdown can lose the current partial minute; browser suspension is not guaranteed to preserve capture.
- Video and chat history are not persisted.
- Physical-device connectivity and certificate trust must be checked on the intended demo network.

**Evidence:** [meeting backend](../services/api/meeting_routes.py), [meeting frontend](../apps/web/src/features/meetings/), [PCM capture](../apps/web/public/meeting-pcm.js), [LAN launcher](../scripts/run_lan.py), [meeting guide](ONLINE_MEETINGS.md).

## 4. Architecture and repository structure

```text
React / TypeScript / Vite frontend
    | HTTP API + meeting WebSocket signaling
FastAPI application
    |-- SQLite: patients, sessions, transcripts, notes, jobs, audit, meetings
    |-- Local files: original recordings and meeting audio chunks
    `-- Single local speech worker
            `-- FFmpeg preparation -> whisper.cpp CPU inference

Meeting media: clinician browser <-> patient browser through WebRTC
```

| Location | Purpose |
|---|---|
| `apps/web/` | React interface, meeting capture, frontend build, and Playwright tests. |
| `services/api/` | API routes, validation contracts, ORM models, review logic, and fallback retrieval. |
| `services/speech/` | Audio validation/preparation, Whisper adapter, CLI, and job worker. |
| `alembic/versions/` | Seven migrations, from `0001_initial` through `0007_meetings`. |
| `packages/contracts/fixtures/` | Fictional session, transcript, and evidence examples. |
| `tests/` | Backend, contract, speech, persistence, and meeting tests. |
| `scripts/` | LAN launcher, real-model integration smoke script, and environment snapshot. |
| `docs/` | Plans, integration guidance, patient/meeting guides, and this status report. |
| `data/`, `models/` | Ignored local database, recordings, runtime/build files, certificates, and model weights. |

The default database is `data/pshynox.db`; recordings live under `data/recordings/`. Database and recording files must be preserved together to retain playable source links. Backend startup applies migrations and, on Linux when enabled, starts the speech worker.

The API reports version `0.2.0`, while Python package metadata and the frontend package still use `0.1.0`. These are current metadata values, not evidence of separate release milestones.

## 5. Module progress and ownership

The following ownership reflects the current README and integration guide; it does not attribute each implementation commit to an individual.

| Module | Documented owner | Current position | Remaining work |
|---|---|---|---|
| A — Speech Processing | Anas Faiz | Local audio preparation, real Whisper adapter, CLI, worker integration, and timestamp validation exist. | Evaluate English/Urdu/code-switching quality, compare models, measure resources, and validate target hardware/dual-mic capture. |
| B — Information Retrieval | Muhammad Ayyan | Shared evidence contracts, API/UI integration point, and keyword fallback exist. | Deliver the BM25/Boolean engine, index refresh behavior, agreed filters, and retrieval evaluation. No `services/retrieval/` implementation is present. |
| C — Clinical Application | Muhammad Talha | Patient/session UI, persistence, recording/job orchestration, note review, audit events, and LAN meetings exist. | Integrate Module B, validate complete demonstrations, resolve documentation drift, and add deployment/access capabilities required by the final scope. |

The abstract classes in `services/api/adapters.py` still contain placeholders. Actual speech execution is implemented separately in `services/speech/`; those old placeholders do not mean the working speech pipeline is missing. Module B remains a real integration gap because the application currently instantiates `FixtureRetrievalProvider`.

## 6. Remaining work and practical limits

### Functional work

1. Integrate real BM25 retrieval and the agreed Boolean/filter behavior behind the existing evidence interface.
2. Verify retrieval uses current transcript revisions and preserves session/source boundaries after edits.
3. Implement dedicated synchronized two-microphone in-person capture if it remains part of the final scope. Meeting track capture does not complete that hardware workflow.
4. Define and validate the final deployment path. Current instructions use Vite and Uvicorn; the API does not currently serve the built frontend as a packaged application.

### Evaluation and deployment evidence

1. Evaluate English, Urdu, and code-switched fictional recordings against checked reference transcripts, including negations, numbers, speaker labels, and timestamps.
2. Measure recognition quality and whole-application resource use for baseline and quantized models. A multilingual model being available does not establish acceptable Urdu accuracy.
3. Test sustained meetings, ASR backlog, interrupted connections, upload recovery, and browser/device behavior.
4. Confirm the exact Jetson board and RAM, build the native runtime for it, install compatible dependencies, and run the full offline save/restart workflow on-board.

### Access and data handling

The current clinician workspace has no login or role-based user account system, and stored reviewer names are not verified. SQLite and audio files are not encrypted by the application. Meeting token checks and LAN restrictions provide scoped demo access, but broader deployment needs an explicit access/storage design. A managed backup/restore process is not implemented in the reviewed application.

### Documentation drift

- [The October 23 plan](Plan.txt) still says implementation has not started and specifies one successful transcript per session. The current code implements speech and supports multiple recordings per session.
- [The integration guide](MODULE_INTEGRATION_GUIDE.md) describes some speech files/endpoints as proposed although they now exist.
- The root README links its implementation plan as `Plan.txt`; the checked-in file is `docs/Plan.txt`.

Use this assessment and the current code for present status; retain older plans as historical scope and acceptance guidance.

## 7. Verification during this assessment

The assessment reviewed implementation files, schemas, migrations, tests, configuration, recent Git history, and the existing Markdown/text guides. It did not inspect personal patient records or recordings, and did not independently review the two PDF reports.

| Check | Result | Meaning |
|---|---|---|
| `npm run build --prefix apps/web` | Passed | TypeScript compilation and Vite production bundling completed. This does not verify browser workflows. |
| Backend suite | Incomplete: stalled on first request | A bounded diagnostic run collected 44 tests, then timed out after 25 seconds in the first health-check test. No full-suite pass is claimed. |
| Meeting Playwright suite | Not run for this documentation assessment | Tests exist for independent cameras, participant connection, chat/mute, minute chunks, and ending a call. |
| Real-model integration smoke | Not run for this documentation assessment | `scripts/smoke_speech.py` exists and uses a temporary database; no new ASR accuracy or performance result is claimed. |
| Physical Wi-Fi call / Jetson test | Not performed | These remain separate acceptance gates. |

The initial `.venv/bin/python -m pytest -q` run produced no completed results and was interrupted. A diagnostic rerun with `timeout 25s .venv/bin/python -m pytest -vv -o faulthandler_timeout=15` collected 44 tests and stalled in `test_health_is_local_and_explicit_about_fixture_mode`. Its stack trace showed the request waiting through Starlette TestClient/AnyIO; the cause was not established. This assessment did not change application code to investigate it. Reproduce and resolve the test stall in the intended development environment before claiming the backend suite passes.

The checkout contains the local whisper.cpp executable, a bundled public sample WAV, and both baseline and Q5_0 model files. Their presence was checked; their hashes and inference behavior were not revalidated here. These ignored assets will not be available automatically in a fresh clone.

Backend tests cover API contracts, patient/session isolation, persistence, transcript edits, review decisions, recording validation, worker failure/retry behavior, meeting invitations, chunk idempotency, and role/timestamp conversion. Many integration tests use a fake transcriber or mocked runtime output, so passing them would not establish real-model language quality.

## 8. Recommended next milestones

| Priority | Milestone | Completion evidence |
|---|---|---|
| 1 | Establish a reproducible laptop demo | Backend tests/build pass in the intended environment; demonstrate patient -> consent -> audio -> real ASR -> edits -> note -> review -> restart/reopen. |
| 2 | Integrate Module B | Actual BM25/Boolean implementation is wired to evidence search, tested after transcript edits, and evaluated on the agreed fictional query set. |
| 3 | Validate the LAN meeting demo | Complete a two-device call on the target Wi-Fi, verify both tracks/final chunk, review timestamp playback, and record backlog/recovery behavior. |
| 4 | Produce an evaluation report | Publish measured language quality, timestamp/speaker errors, latency, and memory results with model/runtime configuration. |
| 5 | Validate target hardware | Record exact Jetson hardware/software and pass an on-board offline persistence and performance gate. |
| 6 | Align final handover | Update stale guides and version metadata, document deployment and recovery, and complete access/storage work appropriate to the agreed deployment scope. |

## 9. Demonstrable workflow

For a local demonstration, the intended implemented path is:

1. Open the workspace and create a fictional patient.
2. Create an initial-assessment or follow-up session and confirm recording consent.
3. Record/import a short clip, or use the documented same-Wi-Fi meeting setup.
4. Process audio locally and review the saved transcript and source playback.
5. Correct text and speaker roles; search the session for source excerpts.
6. Populate the subjective note from confirmed client excerpts and edit the remaining sections manually.
7. Complete manual review and approve or reject the note.
8. Restart and reopen the saved patient, session, audio, transcript, note, and audit history.

Installation and ordinary startup commands are in the [root README](../README.md). Meeting setup, certificate trust, networking, and recovery details are in [ONLINE_MEETINGS.md](ONLINE_MEETINGS.md).

## 4 November 2026: local English note-generation milestone

A laptop CPU llama.cpp workflow now adds versioned source-linked Qwen drafts alongside
existing manual notes, with separate research development UI, bounded retrieval planning,
local evidence/cache files and explicit human review. A real fictional smoke completed;
GPU acceleration, model semantic reliability and human quality evaluation remain unverified.
See [implementation/verification details](NOTE_GENERATION.md) and
[the NVIDIA Secure Boot diagnosis](NVIDIA_LOCAL_FIX.md). Existing labels and held-out
retrieval evaluation artifacts were not changed.

# Pshynox — Complete Project Report (context file for Claude)

**Prepared:** 6 November 2026, from a full read of the repository (code, docs, migrations, tests, git history, local evaluation artifacts and the two FYP PDFs).
**Repository:** `https://github.com/Psychologist-FYP/pshynox` — checked-out branch `annotation` at commit `ec7807c`.
**Purpose of this file:** Give an AI assistant (Claude chat / a Claude Project) everything it needs to understand, discuss, plan, write about and help code this project without access to the repository.

---

## 0. How to use this file (instructions for Claude)

You are helping the Pshynox Final Year Project team. Treat this document as the authoritative project background. When helping:

1. **Keep three things separate**: (a) what is *implemented* in code, (b) what has been *measured* (engineering numbers on small/fictional data), and (c) what is *clinically validated* (nothing yet). Never turn a planned feature into a completed one, an AI-generated label into human evidence, or a single timing into a performance guarantee.
2. The system is **documentation support only**. It does not diagnose, predict risk, detect mood, or recommend treatment. Avoid wording that implies it does.
3. Use only **fictional or properly licensed research data**. No real patient data is used.
4. When asked to write reports, chapters, slides or viva answers, use the numbers in §13 with their caveats.
5. When asked for code help, respect the module boundaries and contracts in §6–§9 (stable segment IDs, revisions, source-linked evidence, review gates).

**Suggested Claude Project custom instructions** (paste into the Project's instructions box):

> You are assisting a 3-person BS Computer Science FYP team at UET Lahore building "Pshynox", an offline, clinician-reviewed psychological session documentation system (React + FastAPI + SQLite + local whisper.cpp ASR + BM25/E5 retrieval + local Qwen3-4B note drafting via llama.cpp). The attached PSHYNOX_PROJECT_REPORT.md is the project background. Always distinguish implemented features, measured engineering results, and clinical validation (none yet). Never overclaim accuracy, never describe the system as diagnostic, and keep AI-generated labels distinct from human annotations.

---

## 1. TL;DR

Pshynox is an **offline-first assistant for psychologists**. A clinician creates a patient, starts a session, records (or imports) audio or holds a same-Wi-Fi video meeting, and the system:

1. **Transcribes locally** (multilingual Whisper `base` through whisper.cpp, CPU) into timestamped, editable transcript segments.
2. Lets the clinician **correct text and speaker roles** (CLIENT / CLINICIAN / UNKNOWN) with playback of source audio.
3. **Searches the session** for evidence with BM25 (default), Boolean/phrase queries and filters; TF-IDF, multilingual E5, hybrid RRF and the old keyword fallback exist for comparison.
4. **Drafts a 7-section structured note** (Subjective, Objective, Assessment, Functioning, Progress, Interventions, Plan) with a **local LLM (Qwen3-4B-Instruct-2507 Q4_K_M via llama.cpp, GPU-offloaded on a GTX 1650)**. Every generated statement carries source turn IDs. AI "formulations" stay pending until a clinician accepts/edits/rejects them.
5. Enforces **clinician review**: manual risk-review gate, approval/rejection, stale-source detection when transcripts change, audit events, read-only after decision, print/Save-as-PDF.
6. Has a separate **research workspace** for retrieval evaluation on the AnnoMI and PriMock57 datasets (annotation UI, benchmark runner, results viewer).

**Stage:** integrated laptop prototype (FYP demonstration stage), roughly Week 3 of a 22-week plan (19 Oct 2026 – 21 Mar 2027). Core pipeline works end-to-end; not yet done: longitudinal progress dashboard, speech-trend features, encryption/auth, Jetson deployment, human-validated evaluations, clinician feedback.

---

## 2. Academic context

| Item | Detail |
|---|---|
| University / degree | University of Engineering and Technology (UET) Lahore — BS Computer Science, Final Year Project |
| Official title | *Offline Intelligent Support System for Psychological Documentation and Longitudinal Progress Monitoring* (revised proposal subtitle: *With Exploratory Speech-Trend Monitoring for Bipolar Disorder*) |
| Product name | **Pshynox** (UI branded as a "Dr. Katz-inspired" workspace, with a light/sarcastic theme and custom mascot imagery) |
| Earlier proposal title | *Intelligent System to Help Psychologist for Acoustic Mood Monitoring and Longitudinal Progress Tracking* (scope was later narrowed; automatic mood labelling dropped) |
| Team | **Anas Faiz** (2023-CS-26), **Muhammad Talha** (2023-CS-169), **Muhammad Ayyan** (2023-CS-05) |
| Clinical consultant | **Ms. Kinza Nazir**, Clinical Psychologist, PsychCura — consulted 5 Oct 2026; willing to act as clinical co-supervisor subject to university approval |
| Proposal revision | 6 Oct 2026 (16-page revised proposal, `docs/FYP_progresss1.pdf`) |
| Implementation plan | v2.0, 5-month/22-week plan (`docs/FYP_Implementation_plan.pdf`) |
| Planned period | 19 Oct 2026 – 21 Mar 2027. Implementation Weeks 1–12, evaluation/optimization/pilot Weeks 13–22 |

### Module ownership (as documented in README / integration guide)

| Module | Owner | Area |
|---|---|---|
| A — Speech Processing | Anas Faiz | Audio validation/decoding, whisper.cpp ASR adapter, timestamps, worker |
| B — Information Retrieval | Muhammad Ayyan | Evidence search engine, comparison methods, evaluation |
| C — Clinical Application | Muhammad Talha | UI, API, persistence, sessions, review workflow, integration |

The implementation-plan PDF assigns roles slightly differently (Anas: AI + integration, ASR, note generation, model selection; Talha: API + security, migrations, consent/approval, audit, packaging; Ayyan: React UI, editors, charts, evaluation cases, usability). Git history: Talha authored the initial base (18 Oct: base features, evidence-linked note drafts, clinician review workflow, session IDs, Dr. Katz rebrand, integration guide); Anas authored all later commits (speech, patient UI, meetings, retrieval, annotation, AI note generation). Much of the recent implementation was done with AI coding assistance (docs refer to "Codex sessions"), which matters for the provenance of AI-authored annotation labels (§12).

### Planned milestones (implementation plan v2.0)

| Weeks | Dates | Deliverable | Gate |
|---|---|---|---|
| 1–2 | 19 Oct–1 Nov | Contracts, UI/API/SQLite, WAV import, offline ASR, transcript edit, source-based draft, save/reopen | **M1 Functional MVP** (professor demo) |
| 3–4 | 2–15 Nov | Consent states, error handling, audio checks, excerpt links, speaker labels, note template config, repeatable install, start recording fictional scenarios | Internal integration |
| 5–6 | 16–29 Nov | Optional local LLM drafting (strict JSON + source checks), corrections, objective fields, review status, approval blocked until risk review | **M2 Clinical workflow** (professor + consultant) |
| 7–9 | 30 Nov–20 Dec | Patient history, reviewed symptoms/functioning, interventions, missing-data rules, charts/timeline, speaker-attribution baseline | **M3 Monitoring beta** |
| 10–12 | 21 Dec–10 Jan | Complete flow, exploratory speech-rate/pause panel, basic Urdu–English eval, audit/privacy controls, tests, packaging, code freeze | **M4 Feature complete** |
| 13–16 | 11 Jan–7 Feb | Held-out fictional scenarios; EN/UR/code-switch ASR; speaker errors; invented/omitted facts; reviewer effort | **M5 Quality review** |
| 17–19 | 8–28 Feb | 45–60 min benchmarks, RAM/VRAM, latency, quantization, Jetson decision | **M6 Jetson go/no-go** |
| 20–22 | 1–21 Mar | Supervised industry-workflow pilot (fictional cases), feedback, final report | **M7 Final sign-off** |

**Status vs plan:** the project is *ahead* on M1/M2 items (LLM drafting, review gating and meetings already exist) and *not started* on M3 items (longitudinal dashboard/charts) and exploratory speech-trend features.

---

## 3. Problem, objectives and scope

### Problem
A psychological session must be turned into a useful clinical record (concerns, observations, assessment, interventions, homework, responses, plans). Documentation is time-consuming and finding a specific statement in a long recording is slow. Ms. Nazir emphasised documentation burden and the value of monitoring change across sessions (symptom frequency/intensity/duration, sleep, appetite, academics, social functioning).

### Central objective
Reduce the effort of preparing and reviewing session records while helping a psychologist follow changes over time — **with local processing** (no cloud dependence) and **mandatory clinician review**.

### Project objectives (proposal §5)
1. Complete local workflow from recording to approved session note.
2. Separate client-reported information, clinician observations and generated draft content.
3. Preserve supporting transcript and timestamps for generated statements.
4. Compare reviewed symptom/functioning records over time.
5. Make risk-review status visible and require clinician sign-off.
6. Measure transcription quality, note accuracy, review effort, latency and memory.
7. Evaluate whether a Jetson Orin Nano Super 8GB can support the workload.

### Research questions
- Can source-linked drafts reduce documentation effort while preserving clinically relevant information?
- Can reviewed records make session-to-session changes easier to inspect?
- What quality/runtime trade-offs arise moving to compact hardware?
- Are speech-trend displays understandable/useful enough to justify further research?

### Clinical priorities
- **Primary:** bipolar disorder *monitoring* (supporting a clinician who already knows the context; no detection/classification of episodes), with exploratory within-person speech-rate/pause trends.
- **Secondary:** generalized anxiety and social anxiety tracking.
- **Deferred:** depression, illness-anxiety and other disorder-specific modules. Bulimia explicitly excluded on consultant advice.

### Explicitly out of scope
Autonomous diagnosis, treatment recommendations, crisis management/emergency alerts, facial emotion recognition, camera-based mental-status assessment, automatic mood/emotion labels, continuous between-session surveillance, EMR/billing integration. Fine-tuning only if a clear limitation and suitable data exist.

### Key consultation-derived design decisions
- Longitudinal symptom/functioning records as a core feature.
- One configurable note template agreed with the consultant (SOAP-style plus functioning/progress/interventions).
- Prominent risk-review section with mandatory review state before approval; "no flagged statement" never means "safe".
- Preserve unclear client accounts as-is; mark uncertainty; don't invent a coherent narrative.
- Sessions are typically 45–60 minutes → must benchmark realistic lengths.
- Clinician-entered observations for both in-person and online settings.

---

## 4. Proposed scope vs. what is actually built

| Proposed core deliverable | Status (6 Nov 2026) |
|---|---|
| Consent-controlled audio capture / import | **Implemented** (per-session recording consent; browser mic + WAV import) |
| Timestamped transcription with speaker attribution | **Implemented** (timestamps); speaker roles are **manual** for single-track audio, **track-based** in meetings; no automatic diarization |
| Editable, source-linked note drafts | **Implemented** (exact-excerpt population + local LLM drafts with source IDs) |
| Clinician-entered objective observations & assessment | **Implemented** (editable sections; AI formulations need explicit review) |
| Explicit review of risk-related content | **Partial**: manual risk-review confirmation gate exists; no risk-excerpt surfacing/extraction feature |
| Longitudinal symptom & functioning dashboards | **Not started** (patient session history list exists; no structured records/charts) |
| Local encrypted storage & controlled export | **Not implemented** (SQLite + plain audio files; print/PDF export only; no auth) |
| Performance & quality evaluation on laptop | **Partial**: engineering benchmarks for retrieval and note drafting; no ASR WER study, no clinician quality study |
| Exploratory speech-rate/pause trends | **Not started** |
| Urdu–English evaluation | **Not started** (multilingual model in place; only literal Urdu/Roman-Urdu retrieval plumbing tests) |
| Jetson Orin Nano Super deployment | **Not started / unverified** (laptop only) |
| Two-microphone in-person capture | **Not implemented** (meeting mode gives two tracks over WebRTC instead) |
| *Extra beyond plan* | Same-Wi-Fi two-person WebRTC meetings with live chunked transcription; full retrieval research/annotation workspace; AnnoMI demo patients |

---

## 5. Architecture and technology

```text
React 19 / TypeScript / Vite  (apps/web)          ← clinician workspace, research workspace, meeting room
        |  HTTP /api/v1  (+ WebSocket for meeting signaling)
FastAPI orchestrator (services/api)               ← routes, validation (Pydantic), review rules, audit
   |-- SQLite (SQLAlchemy + Alembic, 7 migrations): patients, sessions, transcripts, notes, jobs, audit, meetings
   |-- Local files: original recordings, meeting audio chunks, note-generation artifacts/caches
   |-- Module A: services/speech   → single sequential worker → FFmpeg → whisper.cpp CLI (CPU)
   |-- Module B: services/retrieval → BM25 / TF-IDF / E5 / hybrid RRF / fixture, in-memory LRU index
   |-- Note generation: services/note_generation → API-owned loopback llama-server (Qwen3-4B GGUF, CUDA)
   `-- Research: services/retrieval research workspace (separate files under data/research, never the clinical DB)

Meeting media: clinician browser <—WebRTC (host ICE, no STUN/TURN)—> patient browser
```

**Design rule:** a local *modular monolith*, not microservices. Modules A/B are Python capabilities called by the API; they never write SQLite directly or talk to React. Heavy AI stages run **sequentially** under a shared cross-process memory lock (ASR worker and note jobs).

| Layer | Technology |
|---|---|
| Frontend | React 19, TypeScript 5.6, Vite 6, Playwright e2e tests (Chrome). Hand-rolled router in `main.tsx`; no UI framework |
| Backend | Python ≥3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, Uvicorn |
| Database | SQLite (`data/pshynox.db`) |
| ASR | whisper.cpp **v1.8.3** (commit `2eeeba56…`), CPU build; multilingual `ggml-base.bin` (SHA-256 `60ed5bc3…`, ~142 MiB); optional Q5_0 quantized variant |
| Audio | FFmpeg/ffprobe → mono 16 kHz s16 PCM for ASR; originals retained |
| Retrieval | `rank_bm25`, scikit-learn TF-IDF, optional `sentence-transformers` + `intfloat/multilingual-e5-small` (pinned revision `614241f6…`) |
| LLM notes | llama.cpp `llama-server` — official **b12079 CUDA 12.8** binary (earlier CPU build at commit `7071ba37…`); **Qwen3-4B-Instruct-2507 Q4_K_M** GGUF (2.5 GB, SHA-256 `3605803b…`); optional "Omi" GGUF failed to load (vocab error) |
| Meetings | WebRTC, WebSocket signaling, AudioWorklet PCM capture → 60 s WAV chunks, IndexedDB upload queue, self-generated local HTTPS CA |
| Launchers | `scripts/run.py` (one terminal, backend+frontend, auto-reload, Ctrl+C stops both); `scripts/run_lan.py` (HTTPS LAN meeting demo) |

### Development hardware (HP Victus laptop)
Intel Core i5-12450H (12 threads), 16 GB RAM (~15 GiB usable), NVIDIA GTX 1650 Mobile/Max-Q 4 GB VRAM, Fedora 44 KDE, kernel 7.2.13. NVIDIA driver 615.78.08 (initially blocked by Secure Boot MOK key enrollment — resolved 4 Nov; see `docs/NVIDIA_LOCAL_FIX.md`).

### Repository layout

| Path | Contents |
|---|---|
| `apps/web/src/features/` | `landing/`, `patients/` (directory, form, page), `sessions/` (workspace, session detail, recording panel), `notes/` (local AI draft panel, formulation review, print), `meetings/` (entry, room, audio, api), `research/` (research page, results viewer, AI drafts) |
| `apps/web/public/meeting-pcm.js` | AudioWorklet PCM capture processor |
| `apps/web/e2e/` | Playwright: `meeting.spec.ts`, `notes.spec.ts`, `research.spec.ts`, `retrieval.spec.ts` |
| `services/api/` | `main.py` (core routes), `schemas.py` (canonical contracts), `db.py` (ORM), `patient_routes.py`, `speech_routes.py`, `meeting_routes.py`, `research_routes.py`, `note_generation_routes.py`, `retrieval.py` (provider seam), `import_annomi.py`, `adapters.py` (old placeholders) |
| `services/speech/` | `audio.py` (validate/decode), `transcriber.py` (whisper.cpp adapter), `worker.py` (job loop), CLI `__main__.py` |
| `services/retrieval/` | `engine.py`, `query.py` (Boolean parser), `config.py`, `benchmark.py`, `thresholds.py`, `workspace.py` (research storage), `import_data.py`, `prepare.py`, `prepare_audio.py`, `ai_suggestions.py`, `ai_evaluation.py`, `bulk_review.py`, `final_packet.py`, `run_final.py`, `blind_final.html`, `share_annotations.py`, `cache_model.py` |
| `services/note_generation/` | `workflow.py` (pipeline, `local-notes-v5`), `grouped.py` (`grouped-draft-v6` drafter), `recovery.py` (token-limit recovery), `runtime.py` (llama-server lifecycle), `composer.py` (apply to note), `prompts.py`, `storage.py`, `terminal.py` (live tracing), `experiment.py` |
| `alembic/versions/` | `0001_initial` … `0007_meetings` |
| `packages/contracts/fixtures/` | Fictional `session.json`, `transcript.json`, `evidence.json` |
| `tests/` | `api/`, `contracts/`, `speech/`, `retrieval/`, `note_generation/` — 163 test functions |
| `scripts/` | `run.py`, `run_lan.py`, `smoke_speech.py`, `seed_retrieval_demo.py`, `verify_retrieval_demo.py`, `benchmark_note_drafting.py`, `verify_note_recovery.py` |
| `docs/` | ~18 Markdown guides + 2 PDFs (see §19) |
| `data/`, `models/` | Git-ignored: DB, recordings, tools, certificates, model weights, generated artifacts (except shared `data/research/` datasets/annotations on the `annotation` branch) |

---

## 6. Feature walkthrough (what a user can do)

### 6.1 Patient workspace
- Patient directory (search, paging of 18, "Show → AnnoMI demos" filter), add/edit patient, patient page with session history (newest first) and **Start new session**.
- Intake fields (only `name` required): preferred name, date of birth / age at intake, sex, gender, pronouns, phone, email, address, emergency contact/relationship/phone, guardian, occupation, education, marital status, language, referred by, presenting concerns, previous care, medical history, medications, allergies, family/social history, substance use, goals, additional notes.
- Legacy sessions appear under **Unassigned sessions** and can be linked once to a patient (API blocks re-linking to a different patient).
- Patient names are not unique identifiers.

### 6.2 Sessions and consent
- Session types: `INITIAL_ASSESSMENT` or `FOLLOW_UP`; sequential human-readable session IDs; pseudonym ID.
- Recording consent is collected **per session** (not implied by patient registration).

### 6.3 Recording and local transcription (Module A)
- Browser microphone selection + record, or WAV import. Limits: **5 minutes and 100 MiB per recording**. Multiple recordings per session; each has its own job/status.
- Server validates media type, decoded duration and channel count; keeps the original; FFmpeg derives mono 16 kHz PCM. Stereo is mixed to mono for ASR (no speaker separation).
- Single sequential Linux worker with a persisted SQLite job queue (`QUEUED/RUNNING/SUCCEEDED/FAILED`), idempotency keys, atomic claim, interrupted RUNNING jobs marked failed on restart with explicit **Retry transcription**.
- whisper.cpp run as a subprocess with configurable model, language (`auto`/`en`/`ur`), threads, timeout. Metrics recorded: runtime/model hashes, elapsed time, real-time factor. Basic digital-silence check before inference (not full VAD).
- Results validated (`0 ≤ start_ms ≤ end_ms ≤ duration`) and committed with source links (`segment_sources`: segment → recording + job + channel). New recordings **append** segments; earlier edits are never overwritten.
- CLI: `python -m services.speech <wav>`; integration smoke `scripts/smoke_speech.py` (temp DB, restart persistence check).

### 6.4 Transcript review
- Edit text and speaker role (`CLIENT`, `CLINICIAN`, `UNKNOWN`); segment IDs stay stable and `revision` increments.
- Timestamp buttons play the source recording at that point (meeting chunks subtract their offsets).
- Edits invalidate the retrieval index and mark dependent note excerpts stale.

### 6.5 Evidence search (Module B)
- "Search this session" beside the transcript; results show excerpt, speaker role, timestamps, expandable **surrounding conversation**, and jump-to-source.
- Details in §8.

### 6.6 Notes
Two generations of note features coexist:

1. **Deterministic excerpt population** (original MVP): copies confirmed CLIENT excerpts with timestamps/segment IDs into Subjective (`provenance=TEMPLATE`, `review_status=PENDING`), other empty sections "Not documented", manual text protected. The UI button for this was later removed from the patient-session page in favour of AI drafts, but the API endpoint remains.
2. **Local AI draft** (current): see §9. Fills one editable structured note on the session page; generated fields carry `AI_GENERATED` provenance and pending review; previous saved fields stored in an `AI_DRAFT_APPLIED` audit event; optimistic note token prevents overwriting edits made during generation; existing content needs explicit replacement confirmation.

Note sections (v3 schema): **Subjective, Objective, Assessment, Functioning, Progress, Interventions, Plan**. Old five-section notes (subjective/objective/assessment/plan/functioning) remain readable.

- **AI-proposed formulations** are shown in a separate review panel (proposal, basis, missing information, sources, neighbouring dialogue). A named reviewer can **accept**, **edit/adopt** or **reject**; accepted/revised text is added to Assessment; history retained. Final approval is blocked while any formulation is pending.
- **Print / Save as PDF**: clean SOAP layout with functioning/progress/interventions supplements, identifiers, dates, status, reviewer identity; pending AI interpretations clearly marked; text inserted as text (no HTML injection); no invented signature.

### 6.7 Clinician review workflow
- Manual **risk-review confirmation** (explicit; not automated risk assessment), then **approve** or **reject with reason**.
- Approval blocked while: risk review pending, sources stale (must refresh or confirm changed sources), transcription queued/running, a meeting active, or AI formulations pending.
- Decided notes and their transcripts become **read-only**.
- Audit events record actor name and time (self-reported names — **not authenticated**).

### 6.8 Same-Wi-Fi online meetings
- From a session, clinician creates a meeting and shares a patient invitation link. Exactly two participants (clinician + patient). Optional cameras with independent toggles, mic controls, chat.
- Signaling over WebSocket; media peer-to-peer WebRTC with host ICE candidates only (no STUN/TURN → same LAN only).
- Clinician browser captures its own mic and the received patient audio as **separate mono tracks**; AudioWorklet emits standalone WAV chunks every 60 s (final partial chunk flushed on stop); chunks queued in IndexedDB, uploaded idempotently (client UUID), saved as recording + role + meeting offset + ASR job; transcribed while the call continues.
- Roles come from the track (not voice ID); timestamps are meeting-relative.
- Invitation tokens are role-specific, hashed in DB, expire after 4 hours; patient pages never mount the clinician workspace; API rejects LAN clients from non-meeting endpoints (trusts `X-Pshynox-Client-IP` only from the loopback Vite proxy).
- `scripts/run_lan.py` detects the Wi-Fi IPv4, generates a local CA + server cert in `data/lan/`, runs API on loopback and Vite on LAN with HTTPS (needed for mic/camera/AudioWorklet). Each device must trust `ca.pem`.
- Limits: single API process holds rooms; worker can fall behind two incoming streams; abrupt tab close can lose the current partial minute; video/chat not persisted; physical-device acceptance not yet done.

### 6.9 Research workspace (`/workspace/research`)
- Dataset/conversation browser, **annotation without rankings** (graded 0/1/2 relevance per turn with reviewer + rationale, keyboard shortcuts 0/1/2, J/K, Ctrl+Enter), unreviewed vs explicit zero, full-conversation **no-answer confirmation**, revision-conflict protection, separate **Explore without labels** mode (records exposure; development only).
- AI suggestions panel ("AI suggestions · human review pending") with per-turn accept/edit.
- **Evaluation results**: run the benchmark in isolated worker processes, compare methods, inspect per-query rankings with grades/rationales/context, charts, JSON/CSV export, import results, frozen development configuration for test runs, threshold experiments.
- **Open independent blind final annotation** — standalone HTML form for the human-only final set.
- `/workspace/research-notes`: research-only AI note generation on development conversations.

### 6.10 AnnoMI demo patients
`python -m services.api.import_annomi` backs up SQLite then copies all **133 AnnoMI conversations (9,699 turns)** into 133 clearly labelled "AnnoMI demo" patients (one session each, `demo-` ID prefix, blank demographics, recording consent false). Idempotent. Held-out "independent final" conversations are blocked from AI generation. Demo 3 or 24 recommended for short development examples.

There is also a fictional **Retrieval Demo — Fictional Patient** (`patient-retrieval-demo`) with two sessions (22 and 6 turns) seeded by `scripts/seed_retrieval_demo.py`, used to demo search behaviour (see §8.5).

---

## 7. Data model and contracts

### 7.1 SQLite tables (SQLAlchemy, `services/api/db.py`)

| Table | Key columns |
|---|---|
| `patients` | id, name, intake_json, created_at, updated_at |
| `sessions` | id, patient_id (nullable FK, indexed), pseudonym_id, session_type, consent_json, workflow_status, created_at |
| `transcript_segments` | id, session_id, start_ms, end_ms, speaker_role, text, revision |
| `note_drafts` | id, session_id (unique), fields_json, source_revisions_json, sources_stale, workflow_status (DRAFT/…), risk_review_status (PENDING/…), reviewer_name, risk_review_completed_at, review_note, decision_actor_name, decision_at, rejection_reason, timestamps |
| `audit_events` | id, session_id, note_draft_id, event_type, actor_name, occurred_at, metadata_json |
| `recordings` | id, session_id, path, sha256, mime, duration_ms, sample_rate, channels, created_at |
| `transcription_jobs` | id, session_id, recording_id (unique), idempotency_key, status, error_code/message, metrics_json, attempt, timestamps |
| `segment_sources` | segment_id → recording_id, job_id, channel |
| `meetings` | id, session_id, host_hash, guest_hash, status, created/expires/started ms, guest_consented |
| `meeting_chunks` | id (client UUID), meeting_id, recording_id (unique), speaker_role, offset_ms |

Migrations: `0001_initial`, `0002_note_drafts`, `0003_review_audit`, `0004_speech`, `0005_recording_jobs`, `0006_patients`, `0007_meetings`. Applied automatically at backend startup.

Note-generation jobs, versions, evidence packets, checkpoints and caches are stored as **files** under `data/note-generation/` (not in SQLite).

### 7.2 Canonical Pydantic contracts (`services/api/schemas.py`)

```json
TranscriptSegment: {"id":"seg-001","session_id":"session-001","start_ms":0,"end_ms":1200,
                    "speaker_role":"UNKNOWN","text":"Fictional example only.","revision":1}
EvidenceHit: {"segment_id","session_id","start_ms","end_ms","excerpt","score","section","query",
              + additive: window_id, rank, method, patient/recording id, central speaker role,
                source_segment_ids, matched_source_segment_ids, sources[] (text, role, timestamps,
                revision, recording_id, matched), transcript_revision (SHA-256 snapshot), diagnostics}
```

Enums: `SpeakerRole` (CLIENT/CLINICIAN/UNKNOWN), `SessionType`, `WorkflowStatus`, `ReviewStatus`, `RiskReviewStatus`, `NoteWorkflowStatus`, `Provenance` (MANUAL, RETRIEVAL_CANDIDATE, TEMPLATE, AI_GENERATED).

`NoteField`: section, text, provenance, evidence_ids (must be segment IDs in the same session — validated), review_status, plus `generated_items[]`.

`GeneratedNoteItem.type` (8 types): `client_report`, `clinician_observation`, `documented_clinician_assessment`, `ai_proposed_formulation`, `intervention_delivered_or_discussed`, `proposed_plan`, `agreed_plan`, `unknown_or_unresolved`. `status`: supported / explicit_denial / conflicting. `review_status`: pending / accepted / edited / rejected. Formulations carry `basis` and `missing` information.

Invariants: stable segment IDs; `end_ms ≥ start_ms`; revision > 0; evidence must resolve to the active session; empty search results are never evidence of absence; scores are retrieval scores, never clinical probabilities. All JSON is snake_case under `/api/v1`.

---

## 8. Module B — Retrieval in detail

### 8.1 Providers (`RETRIEVAL_METHOD`)

| Method | Implementation | Network at search time |
|---|---|---|
| `fixture` | Original lowercase substring/count ranking (baseline) | none |
| `bm25` (**default**) | `rank_bm25.BM25Okapi`, k1=1.5, b=0.75, **positive Robertson IDF** `log(1+(N−df+0.5)/(df+0.5))` (library IDF can go negative on tiny session corpora) | none |
| `tfidf` | scikit-learn word uni+bigrams, L2-normalised cosine | none |
| `e5` | `intfloat/multilingual-e5-small` on CPU, `query:`/`passage:` prefixes, normalised vectors, tokenizer-budget chunking (≤512 tokens, max-chunk similarity per turn) | local cache only (`RETRIEVAL_E5_LOCAL_ONLY=true`; HTTP 503 if missing) |
| `hybrid` | BM25 + E5 Reciprocal Rank Fusion, k=60, top-20 per retriever | local cache only |

Tokenizer: NFKC + casefold Unicode; keeps letters, numbers, combining marks; **no stopword removal** (keeps no/not/never/nahi). Source text never modified.

### 8.2 Query grammar and API
`GET /api/v1/sessions/{id}/evidence?query=sleep%20AND%20NOT%20medication&k=10` with optional filters `patient_id`, `speaker_role`, `recording_id`, `start_ms`, `end_ms`, `section`.
- Uppercase `AND`/`OR`/`NOT`, parentheses, `"quoted phrases"` (adjacent normalised tokens). Precedence NOT > AND > OR; adjacent words imply OR; `sleep NOT medication` = `sleep AND NOT medication`. Lowercase `not` is ordinary text.
- Explicit operators/phrases are hard filters for every method; plain queries are soft semantic queries for E5/hybrid.
- Pure exclusion queries list matches at score 0. Empty → no results. Invalid syntax → HTTP 422. Limits: 2,000 characters, 256 atoms.
- Optional `RETRIEVAL_MIN_SCORE` method-specific abstention threshold.
- Ranks **central turns**; ±1 neighbouring turn returned as context (`RETRIEVAL_CONTEXT_TURNS` 0–5) — context turns are not scored. Context never crosses independent recording timelines; tracks from one meeting share context.

### 8.3 Index lifecycle
In-memory LRU of 8 patient/session indexes keyed by full segment content, revisions, roles, timestamps, source bindings, ownership and filters; every request validates the snapshot; saved edits explicitly invalidate. Nothing persisted across restarts. Filtering (session, patient consistency, metadata) happens **before** ranking. Only transcript segments are indexed — never notes, dataset labels or judgments.

Seam: `create_retrieval_provider().search(query, session_id, current_segments, k=10, scope=SearchScope(...))`. Module C loads segments; Module B never reads the DB.

### 8.4 Benchmark tooling
`python -m services.retrieval.benchmark --dataset <json> --output <json> [--split dev|test] [--methods …] [--require-all] [--repeats N] [--min-score X] [--config frozen.json]`.
Metrics: source-deduplicated Recall@5/10, Precision@5, MRR@10, nDCG@10, **Grade-2 hit@5, Grade-2 recall@5/10** (direct evidence), no-answer false-positive return & abstention rates, first-search and warm latency, isolated-process peak RSS, tracemalloc, package/model versions, dataset hash. Rejects missing/duplicate judgments and participant identities crossing splits. Supports paired `asr_segments` for WER and reference-vs-ASR retrieval deltas (not yet measured).

### 8.5 Demo searches (fictional retrieval patient)
`"four hours"` → client's sleep/night-shift turn; `anxious` → "I don't feel anxious anymore" (negation preserved); `anxious AND NOT anymore` → no match; `"sister has depression"` → other-person mention (not converted to diagnosis); `sleeping` → clinician question, context shows client's "No"; `neend AND nahi` (Roman Urdu) and `نیند` (Urdu script) literal matches; `ink` → no match with BM25 but the old substring fallback wrongly matches "drink"; `"sleep` → syntax error while transcript stays usable. Editing a turn from "four hours" to "six hours" refreshes evidence and marks the note stale.

---

## 9. Local AI note generation in detail

### 9.1 Runtime
- The API owns a **loopback `llama-server`** process: starts on demand, verifies context via `/props`, kills/reaps after each job or writer switch. No hosted inference, no silent fallback.
- Current local config: llama.cpp **b12079 CUDA 12.8**, `NOTE_GPU_LAYERS=99` → **37/37 layers offloaded**, ~3,056 MiB VRAM, context **4096**, batch 256 / ubatch 128, temperature 0 / seed 42. GPU offload is verified from the runtime log (verbosity 4) and a requested GPU run fails if no offload is confirmed.
- Shared cross-process memory lock with the ASR worker; E5 encoder released after retrieval.

### 9.2 Pipeline (`local-notes-v5`)
1. **Snapshot** transcript (IDs, roles, timestamps, revisions); clinician must acknowledge transcript review first.
2. **Overview extraction** (`compact-overview-v4.3`): transcript packed into chunks of ≤16 primary turns (plus ≤256-char neighbour context), each producing compact typed hints with source IDs. Exact token budgeting via native `/apply-template` + `/tokenize`: `input + output + 64 ≤ 4096`. Defaults: overview output 800, max 1600.
3. **Retrieval**: six predefined section searches through the existing retrieval engine (BM25 default) + up to three overview-hint searches; ±2 neighbouring turns, 4 ranked centres per search; every validated overview source reference is expanded back to original turns.
4. **Bounded follow-ups** (UI default "Extended follow-up searches"): model may request structured `search`/`expand` operations — max 2 rounds × 3 requests, 300-char queries, 5 expansion IDs; malformed/duplicate/foreign-ID requests stop with a visible trace.
5. **Drafting** (`grouped-draft-v6`, strategy `auto`): all seven sections in one JSON-schema-constrained call if it fits (output 1600, max 2304); otherwise groups (Subjective/Functioning, Objective/Assessment/Progress, Interventions/Plan) → single sections → turn/span splits. Sources sent once with aliases T0/T1…, mapped back to original IDs.
6. **Validation**: JSON grammar restricts source IDs to supplied originals; structural type/section checks (e.g. client-only items withheld from Objective; clinician observations/proposals need clinician sources; agreed plans need both proposal and client response); narrow lexical fidelity checks (rejected characterisations, blanket denials, medication-mention guard); formulations require basis + `missing` array. Invalid items withheld with a visible limitation; empty sections get a code-rendered "Not documented in the supplied evidence; this does not establish absence…" message.
7. **Recovery** (`recovery.py`): any token-limited response (`stop_type=limit`, truncation) is discarded entirely — never spliced; one larger-output retry if room exists, otherwise bisect turns then exact character spans; one schema-repair retry; bounded by depth 8, 96 total calls, 64-char minimum spans, job timeout 1800 s. Atomic checkpoints in `data/note-generation/cache-v4/` keyed by revision, spans, model hash, runtime, prompt/schema version, settings → **Retry / resume saved work** reuses completed chunks/sections.
8. **Apply** to the session note server-side (even if the tab closes), versioned; prior versions, originals, edits and audit snapshots preserved; stale versions cannot be saved/approved.

Prompts treat transcript text as **untrusted data**, forbid quotations, names, diagnoses, invented findings and AI recommendations, and require preserving who said what, negation, time, uncertainty and contradictions. No hidden chain-of-thought is requested or stored.

Live terminal tracing via `NOTE_LOG_LEVEL` = `off` / `events` (default) / `full` (prints full prompts/responses — currently `full` in the local `.env`; switch back to `events` when finished because it exposes transcript content).

### 9.3 Version history of the note pipeline
- **v1/v2** (4 Nov): first CPU Qwen smoke (3-turn fictional: 123 s, ~5 GiB RSS); client reports wrongly placed in Objective → client-only withholding check added. Then CUDA build verified (117.6 s first smoke, 33.1 s warm repeat).
- **v3**: seven sections, typed items, formulation review, richer overview (1,100-token cap).
- **v4 / v4.3**: fixed overview output-cap failures (three real jobs hit exactly 1,100 tokens) via compact hints + adaptive recovery + checkpoints.
- **v5 + grouped-draft-v6** (5 Nov): grouped drafting — big speedup (see §13.4); evidence-selection correction (overview references expanded to originals); formulation schema `oneOf` repair.

---

## 10. API endpoint reference (`/api/v1`)

**Core / notes / review (`main.py`)**: `GET /health`; `GET|POST /sessions`; `GET /sessions/{id}`; `GET /sessions/{id}/transcript`; `PATCH /sessions/{id}/transcript/{segment_id}`; `GET /sessions/{id}/evidence`; `GET|PUT /sessions/{id}/note-draft`; `POST /sessions/{id}/note-draft/populate`; `POST /sessions/{id}/note-draft/confirm-sources`; `POST /sessions/{id}/note-draft/formulations/{item_id}`; `GET /sessions/{id}/review-state`; `POST /sessions/{id}/risk-review/complete`; `POST /sessions/{id}/approve`; `POST /sessions/{id}/reject`; `GET /sessions/{id}/audit-events`.

**Patients**: `GET|POST /patients`; `GET|PUT /patients/{id}`; `GET /patients/{id}/sessions`; `PATCH /sessions/{id}/patient`.

**Speech**: `PATCH /sessions/{id}/recording-consent`; `GET|POST /sessions/{id}/recordings`; `GET /sessions/{id}/recordings/{rid}/audio`; `POST /sessions/{id}/transcribe` (202); `GET /sessions/{id}/transcription-job(s)`; `GET /sessions/{id}/transcript-sources`; `GET /jobs/{job_id}`.

**Meetings**: `POST /sessions/{id}/meetings`; `POST /sessions/{id}/meetings/close`; `GET /meetings/{mid}`; `POST /meetings/{mid}/end`; `POST /meetings/{mid}/chunks/{chunk_id}`; `WS /meetings/{mid}/signal`.

**Research** (prefix `/api/v1/research`): `GET /independent-final`; `GET /datasets`; `GET /datasets/{name}/conversations/{cid}`; `PUT …/queries`; `PUT …/queries/{qid}/turns/{sid}`; `POST …/ai/{qid}/turns/{sid}`; `POST …/include`; `GET /datasets/{name}/export`; `POST …/explore`; `POST /datasets/{name}/runs` (202); `GET /runs`; `POST /runs/import`; `GET /runs/{rid}`.

**Note generation** (prefix `/api/v1/note-generation`): `GET /health`; `GET /development`; `GET /development/{dataset}/{cid}`; `POST /jobs` (202); `GET /jobs`; `GET /jobs/{jid}`; `POST /jobs/{jid}/cancel`; `POST /jobs/{jid}/apply`; `PUT /jobs/{jid}/draft`.

Swagger docs at `http://127.0.0.1:8000/docs` when running.

### Frontend routes
`/` landing · `/workspace` · `/workspace/patients/new` · `/workspace/patients/{id}` · `/workspace/patients/{id}/edit` · `/workspace/sessions/{id}` · `/workspace/research` · `/workspace/research-notes` · `/meeting/{meeting-id}` (patient invitation page).

---

## 11. Setup, running and configuration

**Requirements:** Linux or WSL2 (worker uses Linux locking), Python ≥3.11, Node 22/npm, git, curl, C/C++ toolchain, CMake, FFmpeg + ffprobe, whisper.cpp v1.8.3 build, `models/ggml-base.bin`. Optional: llama.cpp server + Qwen GGUF for AI notes; CPU PyTorch + `.[dense]` for E5. No API keys; internet only for installs/downloads.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # -e .[dev]
npm ci --prefix apps/web
# build whisper.cpp into data/tools/whisper.cpp, download models/ggml-base.bin
cp .env.example .env
./scripts/run.py            # or --no-reload for note generation; --api-port/--web-port
# open http://127.0.0.1:5173/workspace
python scripts/run_lan.py   # HTTPS same-Wi-Fi meeting demo
python -m pytest -q ; npm run build --prefix apps/web
```

| Env var | Default | Meaning |
|---|---|---|
| `FIXTURE_MODE` | false | Expose checked-in demo session |
| `DATABASE_URL` | `sqlite:///./data/pshynox.db` | DB |
| `LOCAL_DATA_DIR` / `RESEARCH_DATA_DIR` | `./data` / `./data/research` | Files |
| `SPEECH_WORKER_ENABLED` | true | Background ASR worker |
| `WHISPER_BINARY/MODEL/LANGUAGE/THREADS/TIMEOUT_SECONDS` | whisper-cli / ggml-base.bin / auto / 4 / 1800 | ASR |
| `RETRIEVAL_METHOD` | bm25 | fixture/bm25/tfidf/e5/hybrid |
| `RETRIEVAL_BM25_K1/B`, `RETRIEVAL_CONTEXT_TURNS`, `RETRIEVAL_CACHE_SESSIONS`, `RETRIEVAL_MIN_SCORE` | 1.5/0.75, 1, 8, unset | Retrieval |
| `RETRIEVAL_E5_MODEL/REVISION/CACHE_DIR/LOCAL_ONLY` | multilingual-e5-small / 614241f6… / models/retrieval / true | Dense |
| `MEETING_PUBLIC_ORIGIN`, `MEETING_ICE_SERVERS` | (auto by launcher), `[]` | Meetings |
| `NOTE_LLAMA_SERVER`, `NOTE_QWEN_MODEL`, `NOTE_OMI_MODEL`, `NOTE_LIBRARY_PATH` | | LLM runtime/model paths |
| `NOTE_GPU_LAYERS`, `NOTE_CONTEXT`, `NOTE_BATCH_SIZE`, `NOTE_UBATCH_SIZE` | 0 (99 locally), 4096 | Inference |
| `NOTE_OVERVIEW_OUTPUT/MAX_OUTPUT`, `NOTE_SAFETY_MARGIN`, `NOTE_MAX_SPLIT_DEPTH`, `NOTE_MAX_GENERATION_CALLS`, `NOTE_MIN_SPAN_CHARS`, `NOTE_OVERVIEW_MAX_TURNS` | 800/1600, 64, 8, 96, 64, 16 | Token budgeting/recovery |
| `NOTE_DRAFT_STRATEGY`, `NOTE_DRAFT_OUTPUT/MAX_OUTPUT` | auto, 1600/2304 | Drafting (`sections` = legacy v4) |
| `NOTE_FOLLOWUP_ROUNDS`, `NOTE_REQUESTS_PER_ROUND`, `NOTE_SECTIONS` | 2, 3, — | Follow-up bounds |
| `NOTE_CALL_TIMEOUT`, `NOTE_JOB_TIMEOUT`, `NOTE_LOG_LEVEL` | 180, 1800, events | Timeouts, tracing |

---

## 12. Datasets and annotation

### 12.1 Datasets (research only; never mixed with clinical records)

| Dataset | What | Use in project | License / caveat |
|---|---|---|---|
| **AnnoMI** (Wu et al., ICASSP 2022) | 133 motivational-interviewing demo conversations, **9,699 utterances**, therapist/client roles, timestamps, change/sustain/neutral talk labels | Counselling-domain retrieval evaluation; AnnoMI demo patients for note generation | Offered for research; no standalone license file — resolve with creators before redistribution/product use. Talk labels are **not** diagnoses or relevance labels; no audio included |
| **PriMock57** (Babylon Health, ACL 2022) | Mock primary-care consultations; 2 included (consult 01: ~7m38s, GI, 109 turns; consult 02: 9m19s, skin, 130 turns) with separate doctor/patient WAV tracks (16 kHz mono), TextGrid transcripts, clinician notes | Audio import/ASR/timestamp/retrieval tests; 4 synchronized ≤5-min clip pairs (8 WAVs) prepared | CC BY 4.0 (attribution required). Acted GP consultations, not psychology sessions |
| **DAIC-WOZ** | 189 interview sessions with audio/transcripts | *Next dataset to request* (academic application, supervisor support) | Restricted; not included |
| MTS-Dialog | Text-only note-generation dataset | Optional future | — |

All 17 starter-pack manifest files verified by size + SHA-256 (`docs/Pshynox_Retrieval_Starter/manifest.json`). Prepared imports and per-conversation annotation files are tracked in Git on the `annotation` branch under `data/research/`.

### 12.2 Pilot and splits
Seed **20261102** selected 10 AnnoMI conversations — **development:** annomi-3, 19, 24, 40, 50, 97, 103; **test:** annomi-21, 23, 87. Source-video groups kept together; participant identity is unavailable so speaker independence is **not established**. PriMock consult 02 = dev, 01 = test.

### 12.3 Annotation history (important provenance)
1. 40 generic starter draft queries (4 per conversation) created as **unapproved drafts**.
2. A small number (**44**) of human judgments made by the team.
3. **AI-authored suggestions** (policy `retrieval-ai-v1`): an AI coding session read complete conversations and authored 4 new fully graded queries per conversation → **40 queries, 2,416 query–turn labels over 604 turns, 246 ambiguity flags**. Initially development-only; scope later explicitly extended to the 3 test conversations (so they are no longer an independent human test of this contribution).
4. **Bulk acceptance**: Anas inspected a sample and authorised bulk acceptance — **2,413 grades** recorded with reviewer "Anas", action `bulk-accepted`, AI origin. These are *not* 2,416 individually inspected labels. The 10 full-conversation no-answer confirmations remain pending.
5. **AI consistency audit**: checked all 10 no-answer questions, all 246 flagged pairs and 89 random unflagged pairs → **68 correction proposals** (52 × 1→0, 1 × 1→2, 15 × 2→1), stored separately, not applied to accepted labels.
6. **7 focused development questions** (AI-drafted) added for a descriptive comparison.
7. **Independent human final set** prepared but **not yet labelled**: annomi-101 (64 turns), annomi-79 (11), annomi-123 (27) from unused test source groups; no AI questions/labels/rankings created. Standalone blind form `data/research/review-experiments/independent-final/review.html`; final runner `python -m services.retrieval.run_final --labels <file>` verifies frozen config/transcripts/labels and refuses to overwrite.

Grading rubric: **2** = direct evidence (supplies all/substantive part of answer), **1** = partial/contextual (materially interprets or qualifies), **0** = irrelevant. All turns in a conversation are judged per query (meaningful recall denominator).

---

## 13. Measured results (with caveats — quote these carefully)

### 13.1 Retrieval — fictional engineering fixture (1 Nov 2026)
9 fictional queries / 7 source turns (7 answerable, 2 no-answer). Engineering comparison only.

| Method | Recall@5 | MRR@10 | nDCG@10 | No-answer false-return rate | Warm median (ms) | Peak RSS (MiB) |
|---|---:|---:|---:|---:|---:|---:|
| Existing fallback | 1.000 | 0.929 | 0.947 | 0.50 | 0.46 | 34.1 |
| TF-IDF | 1.000 | 1.000 | 1.000 | 0.00 | 2.14 | 176.0 |
| **BM25** | 1.000 | 1.000 | 1.000 | 0.00 | 0.59 | 54.4 |
| E5 | 1.000 | 0.905 | 0.929 | 1.00 | 30.19 | 1316.1 |
| Hybrid RRF | 1.000 | 1.000 | 1.000 | 1.00 | 28.06 | 1317.8 |

(Precision@5 = 0.20 for all because each answerable query has one relevant source.)

### 13.2 Retrieval — AI-labelled exploratory evaluation on the AnnoMI pilot (3 Nov 2026)
40 AI-authored queries (dev: 21 answerable / 7 no-answer; test: 9 / 3). Measures **agreement with AI labels**, not human-validated accuracy. Default settings, 3 warm repeats, test used frozen dev config, no tuning on test. Recall/Precision/MRR count grades 1+2 as relevant; nDCG graded.

| Split | Method | R@5 | R@10 | P@5 | MRR@10 | nDCG@10 | No-answer false returns | Warm ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| dev | bm25 | 0.367 | 0.437 | 0.524 | 0.706 | 0.415 | 1.000 | 5.61 |
| dev | tfidf | 0.363 | 0.440 | 0.514 | 0.656 | 0.406 | 1.000 | 6.82 |
| dev | **e5** | **0.485** | **0.638** | 0.590 | 0.802 | **0.624** | 1.000 | 37.45 |
| dev | hybrid | 0.441 | 0.591 | 0.600 | **0.827** | 0.551 | 1.000 | 38.54 |
| test | bm25 | 0.181 | 0.289 | 0.622 | **0.889** | 0.386 | 1.000 | 21.65 |
| test | tfidf | 0.189 | 0.295 | 0.667 | 0.870 | 0.395 | 1.000 | 22.72 |
| test | **e5** | **0.205** | **0.316** | **0.711** | 0.861 | **0.430** | 1.000 | 43.12 |
| test | hybrid | 0.194 | 0.314 | 0.667 | 0.815 | 0.403 | 1.000 | 60.29 |

Takeaways: dense E5 retrieves more relevant turns on development; differences shrink on test; **no method abstained on any no-answer question**. Broad questions have more relevant turns than fit in top-10, depressing recall.

### 13.3 Direct-evidence metrics and thresholds (bulk-accepted labels)

| Method | Baseline direct hit@5 | Focused direct hit@5 | Baseline direct recall@10 | Focused direct recall@10 |
|---|---:|---:|---:|---:|
| bm25 | 76.2% | 85.7% | 45.0% | 73.8% |
| tfidf | 76.2% | 85.7% | 44.1% | 73.8% |
| e5 | 85.7% | 85.7% | 65.6% | 81.0% |
| hybrid | 85.7% | 71.4% | 60.9% | 70.2% |

(28 mixed baseline vs 7 focused dev queries — descriptive, not a balanced causal test.)

Method-specific abstention thresholds selected on development only (objective revision 2: keep ≥80% of baseline direct hit@5, then maximise equal-weight direct-hit + abstention):

| Method | Frozen cutoff | Dev direct hit@5 | Dev no-answer false returns | Test direct hit@5 | Test no-answer false returns |
|---|---:|---:|---:|---:|---:|
| bm25 | none (baseline) | 78.6% | 100% | 55.6% | 100% |
| tfidf | none | 78.6% | 100% | 77.8% | 100% |
| e5 | 0.7856 | 78.6% | 42.9% | 77.8% | 100% |
| hybrid | 0.03126 | 71.4% | 71.4% | 33.3% | 100% |

Conclusion: **score thresholds do not give reliable abstention** (semantic similarity ≠ answerability; e.g. E5 returns pre-reduction blood-pressure talk for a question about measurements after cutting coffee). Thresholds are frozen in `frozen-method-config.json` but **not** deployed as app defaults. Only 7 dev / 3 test no-answer questions.

### 13.4 Note drafting performance (5 Nov 2026, single development case annomi-3, 16 turns, identical frozen evidence)

| Measure | v4 baseline fresh | **v6 optimized fresh** | v4 baseline warm | **v6 optimized warm** |
|---|---:|---:|---:|---:|
| Drafting wall time (excl. model load) | 173.97 s | **61.39 s** | 133.36 s | **28.34 s** |
| Model calls | 7 | 3 | 8 | 2 |
| Formatted input tokens | 14,792 | 4,694 | 16,976 | 3,219 |
| Generated tokens | 2,985 | 1,361 | 4,332 | 1,039 |
| Process peak RSS | 3,028.9 MiB | 2,787.0 MiB | 3,408.4 MiB | 2,787.0 MiB |

Improvement: **64.7 % fresh, 78.8 % warm** in this one case. Model load ≈ 7.6 s (mostly GGUF hashing). Exact checkpoint reuse: 0.0167 s / 0 calls. All references belonged to original evidence with exact 16-turn coverage per section — structural provenance, **not** semantic correctness. Remaining errors observed: a contradictory weight-gain sentence, over-specific "toxins/detox" wording, some unsupported generalisations.

Other runs:
- **Long-case token recovery** (annomi-97, 108 turns, v4.3, GPU): 38 calls (7 overview + 31 drafting), 10,624 generated tokens, **930 s (~15.5 min)**; checkpoint resume 41.7 s with 0 calls; all 108 turns covered; legacy v3 reproduced the 1,100-token failure in 115.5 s.
- **GPU verification**: 37/37 layers, ~3,056 MiB VRAM, up to 98 % utilisation, ~29–30 tokens/s generation, ~151–155 tokens/s prompt processing; fictional smoke 117.6 s first / 33.1 s warm repeat.
- **CPU baseline smoke** (before GPU): 3-turn fictional, 123.4 s, ~5.0 GiB runtime RSS.
- Observed real test-split job demo-annomi-106: 200.42 s end to end (diagnostic only; test cases are not regenerated for benchmarking).

### 13.5 Tests
- Latest reported backend run: **157 passed** (5 Nov 2026, mocked inference). 163 test functions currently defined.
- Earlier milestones: 76 passed (1 Nov), 102 passed (2 Nov), 129 passed (token recovery).
- Frontend `npm run build` passes; Playwright suites for meetings, notes (mocked generation), research, retrieval exist and passed at their milestones.
- Known quirk: inside a restricted sandbox, Starlette TestClient hangs on the first request (environment-specific loopback restriction); the same suite passes outside the sandbox.

### 13.6 Not measured yet
ASR WER/CER (English, Urdu, code-switching), speaker-attribution error, timestamp error, ASR-induced retrieval degradation, human-validated retrieval quality, clinician note-quality scores, documentation time saved, whole-application peak memory, 45–60 minute session performance, Jetson anything.

---

## 14. Safety, privacy and integrity principles built into the design

- **No diagnosis / no risk prediction / no mood labels.** Retrieval scores are relevance scores only. Empty results ≠ absence of a symptom or risk.
- **Source linking everywhere**: every evidence hit and generated statement points to stable segment IDs; revisions detect stale sources.
- **Human-in-the-loop**: AI output is always pending; formulations need explicit accept/edit/reject; approval requires manual risk review.
- **Preservation**: regeneration never overwrites human-written fields or accepted formulations; all versions and audit history retained.
- **Local-only inference** after setup; no hosted fallback when local models fail.
- **Data hygiene**: model weights, DBs, recordings, `.env`, certificates excluded from Git; only fictional/licensed research data.
- **Evaluation integrity**: dev/test splits by conversation/source group; annotation before seeing rankings; exposure recorded; frozen configs before test; held-out/blind conversations blocked from AI generation; AI labels always tagged with provenance and never presented as human labels.

**Current gaps:** no authentication/roles (reviewer names self-reported), no encryption at rest (SQLite and audio plain), no managed backup/restore (except SQLite backup before AnnoMI import), meeting security is LAN-demo grade only, full note-generation tracing currently prints transcript content to the terminal.

---

## 15. Git and team workflow

- Branches: `main` (only the 18 Oct base + docs, up to `55f18d9`), `annotation` (current; **8 commits ahead of main** — contains speech, patient UI, meetings, retrieval, research workspace, AI notes), `anas-sp` (up to retrieval commit), plus remote `docs` and `feat/module-c-evidence-notes`. **Nothing since 18 Oct has been merged to `main` yet.**
- Team annotation flow: each member owns specific conversation IDs; the app writes per-conversation JSON files under `data/research/annotations/<dataset>/`; stop app → commit own files → `git pull --rebase origin annotation` → push. Never resolve annotation conflicts by picking a whole side.
- Planned PR rules: one purpose per PR, tests + README per module, contract changes reviewed by owners of both sides, at least one teammate review.

### Commit timeline
| Date | Author | Commit |
|---|---|---|
| 18 Oct | Talha | Initial push (base features + module setup); persist evidence-linked note drafts; clinician review workflow; sequential session IDs/status labels; Dr. Katz rebrand; module integration guide (PRs #1–#4) |
| 20 Oct | Anas | Implementation plan |
| 24 Oct | Anas | Local transcription + patient management UI; Dr. Katz-inspired UI update |
| 25 Oct | Anas | Live same-Wi-Fi meeting with background transcription |
| 2 Nov | Anas | Retrieval models + annotation interface |
| 3 Nov | Anas | Shared retrieval datasets + per-conversation annotations; AI-annotated evaluation results |
| 6 Nov | Anas | Local AI draft creation (note generation v5/grouped v6), formulation review, print, docs |

---

## 16. Known limitations and documentation drift

- AI drafts still contain contradictions, unsupported/over-specific wording, attribution errors, repetition and omissions — every draft must be checked against sources.
- Speaker roles manual for ordinary recordings; meeting roles from tracks (cross-talk/echo can mislabel).
- 5-minute/100 MB recording limit (sessions are 45–60 min in practice — chunking/longer support needed).
- Worker is sequential; transcription can lag a live meeting.
- Note generation is English-focused; Urdu untested.
- Omi alternative model GGUF fails to load (`error loading model vocabulary: unordered_map::at`); Qwen is the only working writer.
- `services/api/adapters.py` abstract placeholders are historical; real code lives in `services/speech` and `services/retrieval`.
- Version metadata inconsistent: API reports `0.2.0`, Python package and frontend `0.1.0`.
- Drift: `docs/Plan.txt` still says "implementation has not started" and "one transcript per session"; `MODULE_INTEGRATION_GUIDE.md` labels some now-existing endpoints "proposed"; README links `Plan.txt` at root (actual: `docs/Plan.txt`); `PROJECT_STATUS.md` body is the 26 Oct baseline with later dated addenda.
- Deployment: Vite + Uvicorn dev servers; the API doesn't serve the built frontend as a packaged app.

---

## 17. Remaining work / suggested next steps

| Priority | Work | Evidence of completion |
|---|---|---|
| 1 | Demo to the clinician (Ms. Nazir) and collect feedback on note structure, required fields, review UI | Agreed template + prioritised backlog |
| 2 | Human review of representative transcripts and AI notes | Recorded corrections/ratings: unsupported facts, omissions, attribution, negation/time errors, editing time (CSV template exists at `data/note-generation-experiment/human-review.csv`) |
| 3 | Complete **independent human final retrieval annotation** (annomi-101/79/123), second-annotator check, run `run_final` once | Human-labelled final results with frozen settings |
| 4 | ASR evaluation: PriMock clips + team-recorded fictional EN/UR/code-switched scenarios; WER/CER, negations/numbers, timestamps; base vs Q5_0 vs tiny/small | Benchmark report by language |
| 5 | **Longitudinal progress dashboard** (M3): structured reviewed symptom/functioning records across sessions, units, missing values as missing, charts (Recharts planned) | Working monitoring beta |
| 6 | Exploratory speech-trend panel: speech rate from timestamps, pause durations, optional pitch; within-person only; no mood labels | Panel + reliability notes |
| 7 | Longer sessions (45–60 min): chunked recording/transcription, memory/latency on full pipeline | Measured sustained-run report |
| 8 | Security/privacy: clinician login/roles, encrypted storage (SQLCipher or encrypted volume), export/deletion logging, backup/restore | Implemented + tested controls |
| 9 | Merge `annotation` work into `main` via reviewed PRs; fix documentation drift and version metadata | Clean main branch |
| 10 | Jetson Orin Nano Super go/no-go: ARM64 builds, memory headroom, offline restart test | Feasibility report |
| — | Possibly request DAIC-WOZ access (supervisor-supported academic application) | — |

### Questions prepared for the clinician interview (from `DOCTOR_INTERVIEW_STATUS_REPORT.md`, 5 Nov — interview not yet conducted)
1. What must appear in an initial assessment vs. a follow-up note?
2. Are the seven sections useful; should names/order/content change?
3. Which statements must always be written or confirmed by the clinician?
4. How should client reports, observations, interpretations and agreed plans be distinguished?
5. Is the source-review interface practical; what would make AI drafts easier to check?
6. Which transcription/note errors would be most consequential?
7. Consent, confidentiality, access, retention, backup expectations for a future pilot?
8. Appropriate example sessions and evaluation criteria for professional review?
9. Most valuable first feature: transcription, evidence search, structured drafting, or session history?

---

## 18. Glossary

- **Segment / turn** — one transcript utterance with stable ID, timestamps, role, revision.
- **Revision** — counter incremented on each edit; used for staleness detection.
- **Evidence hit / window** — ranked central turn plus non-scored neighbouring context turns.
- **Provenance** — origin of note text: MANUAL, TEMPLATE (excerpt copy), RETRIEVAL_CANDIDATE, AI_GENERATED.
- **AI-proposed formulation** — model's tentative clinical interpretation; never part of Assessment until a clinician accepts/edits it.
- **Stale sources** — note references segments whose revision changed after drafting.
- **No-answer query** — question whose answer is absent in the conversation; tests abstention.
- **Grade 0/1/2** — irrelevant / partial-contextual / direct evidence.
- **RRF** — Reciprocal Rank Fusion, `Σ 1/(60 + rank)`.
- **Dev / test / independent final** — tuning split / held-out split / untouched human-only blind set.
- **Bulk-accepted** — AI labels accepted by a human after sample inspection, not per-label review.
- **Fixture mode** — exposes a checked-in fictional demo session.
- **AnnoMI demo patient** — clinical-workspace copy of a research conversation for testing (`demo-` prefix).

---

## 19. Document index (in the repo)

| File | What it covers |
|---|---|
| `README.md` | Setup, run, config, troubleshooting, architecture summary |
| `docs/FYP_progresss1.pdf` | Revised FYP proposal (6 Oct 2026): problem, consultation, scope, objectives, note structure, risk review, longitudinal monitoring, speech trends, hardware, privacy, evaluation, 9-month plan |
| `docs/FYP_Implementation_plan.pdf` | 5-month implementation & evaluation plan v2.0, milestones M1–M7, Week-2 MVP definition, roles |
| `docs/Plan.txt` | Module A two-week MVP plan (23 Oct; partly outdated) |
| `docs/PROJECT_STATUS.md` | 26 Oct status assessment + 2 Nov / 4 Nov addenda |
| `docs/DOCTOR_INTERVIEW_STATUS_REPORT.md` | 5 Nov project overview prepared for clinician interview |
| `docs/MODULE_INTEGRATION_GUIDE.md` | Module boundaries, contracts, Git workflow, acceptance checklists |
| `docs/PATIENT_WORKSPACE.md` | Patient records and routes |
| `docs/ONLINE_MEETINGS.md` | LAN meeting setup, certificates, capture, limits |
| `docs/RETRIEVAL.md`, `RETRIEVAL_IMPLEMENTATION_STATUS.md`, `RETRIEVAL_DEMO.md` | Module B design, audit, demo |
| `docs/RESEARCH_EVALUATION.md`, `TEAM_ANNOTATION.md`, `AI_ANNOTATION_REVIEW.md`, `RETRIEVAL_REVIEW_EXPERIMENTS.md` | Research workspace, annotation workflow, AI labels, experiments, final packet |
| `docs/Pshynox_Retrieval_Starter/START_HERE.md` | Dataset brief and recommended retrieval/evaluation design |
| `docs/NOTE_GENERATION.md`, `NOTE_FORMULATION_REVIEW.md`, `NOTE_TOKEN_RECOVERY_VERIFICATION.md`, `NOTE_DRAFTING_OPTIMIZATION.md` | Local LLM note pipeline, v3 review, v4 recovery, v5/v6 grouped drafting + benchmarks |
| `docs/NVIDIA_LOCAL_FIX.md` | Secure Boot / NVIDIA driver diagnosis on the laptop |
| `services/speech/README.md`, `packages/contracts/README.md` | Speech module, fixtures |

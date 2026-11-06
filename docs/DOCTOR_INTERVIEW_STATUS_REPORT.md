# Pshynox — Project Status Report

**Prepared:** 5 November 2026  
**Purpose:** Project overview for a doctor/psychologist interview and demonstration  
**Current stage:** Integrated Final Year Project prototype

## 1. Project overview

Pshynox is a psychological session documentation application designed to help clinicians manage patient records, record sessions, review transcripts, find relevant statements, and prepare structured session notes.

The project combines a clinical workspace with local speech transcription, information retrieval, and AI-assisted note drafting. The clinician reviews and corrects the output before approving a note. Clinical usefulness and accuracy still require professional evaluation.

The system is intended for documentation support. Automated diagnosis and treatment recommendation are outside its current implemented purpose.

## 2. Problem and intended benefit

Session documentation requires clinicians to remember important details, organize information, and write notes while maintaining attention to the patient. Finding a particular statement in a long recording can also take time.

Pshynox aims to support this work by keeping patient information and session history together, converting recordings into editable text, linking relevant statements to their sources, and preparing a draft for clinician review. Any reduction in documentation time or improvement in clinical work remains to be measured with users.

## 3. Current implementation

| Area | What is available now |
|---|---|
| Patient records | Create and edit intake profiles, browse patients, and view patient-specific session history. |
| Intake information | Demographics, contact information, presenting concerns, previous care, medical history, medications, allergies, family/social history, substance use, and goals. Most fields are optional. |
| Session management | Initial-assessment and follow-up sessions, recording-consent confirmation, and linking previously unassigned sessions to a patient. |
| Audio capture | Browser microphone recording and WAV import, with local file storage and validation. The current recording/import limit is five minutes and 100 MB per recording. Multiple recordings can belong to one session. |
| Local transcription | Whisper through whisper.cpp, with background jobs, progress/status, retries, timestamps, and saved transcript segments. |
| Transcript editing | Correct text, assign client/clinician/unknown speaker roles, and play corresponding source audio. Ordinary single-track recording does not automatically identify speakers. |
| Evidence retrieval | BM25 search integrated into the application, Boolean operators and phrase search, metadata filters, and surrounding dialogue context. TF-IDF, multilingual E5, hybrid retrieval, and the original fallback are also available for comparison. |
| Manual/source-excerpt notes | Editable note fields and population from confirmed client excerpts, with source links. |
| Local AI drafts | Versioned English drafts using a local Qwen model, transcript review acknowledgement, retrieved evidence, source references, and preservation of human-written fields. |
| Clinician review | Explicit review and approval/rejection, stale-source checks, and recorded review history. Pending AI formulations require a review decision before final approval. |
| Print output | Browser print / Save as PDF for notes, with pending AI interpretations identified. |
| Local meetings | Two-person audio/video meetings on the same Wi-Fi, chat, separate audio tracks, and minute-by-minute recording chunks submitted for local transcription. |
| Research workspace | Dataset browsing, relevance annotation, retrieval comparisons, saved results, and JSON/CSV exports, separate from ordinary clinical records. |

“Available” means implemented in the current working checkout. It does not mean that every feature has completed clinical or physical-device validation. Some recent note-generation work is still uncommitted locally.

## 4. Structured notes and clinician control

The current note editor supports seven sections:

1. **Subjective:** What the client reports, including concerns, symptoms, history, and barriers.
2. **Objective:** Documented clinician observations or measurements.
3. **Assessment:** Documented assessment and explicitly reviewed formulations.
4. **Functioning:** Effects on activities, work, self-care, participation, or relationships.
5. **Progress:** Supported change over time.
6. **Interventions:** Actions, education, advice, or options discussed during the session.
7. **Plan:** Proposed next steps, client intentions, and actions documented as agreed.

AI-proposed formulations are presented separately for acceptance, revision, or rejection. Pending formulations remain outside the recorded Assessment text. The system retains their original text and review history.

Generated statements have source references so the clinician can inspect the original dialogue. Source edits can make previous output stale. Regeneration preserves nonempty human-written fields and accepted formulations. These controls help review, but valid source references alone do not establish that a generated statement is accurate.

## 5. Typical demonstration workflow

1. Open the workspace and create a fictional patient.
2. Add an initial-assessment or follow-up session and confirm recording consent.
3. Record a short example or import a prepared WAV file.
4. Run local transcription, review the wording, and correct speaker labels.
5. Search for a relevant topic and inspect the matching source and surrounding conversation.
6. Review a structured AI draft or demonstrate manual/source-excerpt note preparation.
7. Inspect source references and accept, revise, or reject any proposed formulation.
8. Save the note, complete review, and demonstrate approval and print preview.

For the interview, prepare a saved draft in advance because live generation time depends on recording length, evidence volume, and hardware. Use fictional or clearly identified research/demo material.

## 6. Architecture and modules

| Component | Current implementation |
|---|---|
| Frontend | React, TypeScript, and Vite |
| Backend | Python and FastAPI |
| Record storage | SQLite plus local recording files |
| Speech recognition | Local multilingual Whisper model through whisper.cpp |
| Information retrieval | BM25, TF-IDF, optional local E5, and experimental hybrid ranking |
| Note generation | Local Qwen3-4B-Instruct-2507 Q4_K_M through llama.cpp |
| Meeting communication | Browser WebRTC with WebSocket signaling |

After dependencies and models are installed, the implemented transcription and note-generation paths use local inference. This reduces dependence on hosted AI services; local storage still requires appropriate access protection.

| Module | Documented owner | Current position |
|---|---|---|
| A — Speech Processing | Anas Faiz | Audio preparation, local transcription, timestamps, and worker integration implemented. |
| B — Information Retrieval | Muhammad Ayyan | Search engine, comparison methods, research annotation, and evaluation tools implemented. |
| C — Clinical Application | Muhammad Talha | Patient/session workspace, persistence, review, meeting integration, and note interface implemented. |

Ownership follows the project documentation and does not attribute every recent change to an individual.

## 7. Available verification and research evidence

The following distinguishes existing engineering evidence from clinical validation:

| Evidence | Current result and interpretation |
|---|---|
| Frontend build | Passed during preparation of this report. TypeScript compilation and production bundling succeeded. |
| Backend regression tests | The latest existing drafting-optimization document reports **157 passed**. A new sandbox run timed out without completed results; a fresh full-suite pass is not claimed here. |
| Retrieval engineering comparison | Five retrieval methods were measured on a small fictional fixture. These results establish an engineering comparison, not clinical retrieval accuracy. |
| Research data | Existing documentation records 133 AnnoMI conversations containing 9,699 turns, plus two PriMock consultations containing 109 and 130 turns. |
| Annotation progress | AI-derived pilot labels and exploratory comparisons exist. Bulk sample-based acceptance is recorded separately from individual human review. Independent human final annotation and evaluation remain pending. |
| Drafting optimization | An existing local comparison on one 16-turn development conversation measured drafting time of **173.97 → 61.39 seconds** with a fresh model process, and **133.36 → 28.34 seconds** with a warm process. These exclude model loading and cover the drafting stage, not the whole session workflow. |
| Longer-case recovery | An earlier workflow completed a 108-turn development conversation in approximately **15.5 minutes**, with checked source coverage and token budgets. This is a different case/workflow and is not directly comparable to the shorter drafting benchmark. |
| GPU evidence | Later recovery documentation records successful local GPU offload. Older CPU-only installation findings describe an earlier milestone; acceleration still depends on the configured runtime and hardware. |

No clinician accuracy score, established time saving in practice, or clinical outcome improvement is available. Existing timing results are limited examples rather than general performance guarantees.

## 8. Known limitations and remaining work

- **AI accuracy:** Documented examples still include contradictions, unsupported wording, attribution errors, repetition, and omissions. Every draft needs comparison with the source.
- **Clinical validation:** The seven-section format, proposed formulations, and overall workflow need professional feedback and independent evaluation.
- **Language evaluation:** A multilingual speech model is available, but acceptable English/Urdu/code-switching accuracy has not been established. Current AI note drafting focuses on English.
- **Retrieval evaluation:** Independently human-reviewed relevance judgments and a final held-out evaluation remain outstanding. Exploratory dense/hybrid methods have returned results for absent-answer questions.
- **Risk documentation:** A manual risk-review gate exists. It is not an automated risk assessment, and an undocumented topic must not be interpreted as absence of risk.
- **Access and storage:** The clinician workspace has no implemented authenticated account/role system. Reviewer names are self-reported. Application-level encryption of the database/audio and managed backup/restore are not implemented.
- **Meeting scope:** The documented meeting setup is for the same Wi-Fi. Broader-network reliability, sustained calls, recovery, and physical-device behavior need further validation. Video and chat history are not persisted.
- **Speaker attribution:** Ordinary audio requires manual role review; meeting attribution comes from separate tracks rather than voice identification.
- **Deployment:** The current setup is a laptop/Linux development prototype. Jetson deployment and dedicated synchronized two-microphone in-person capture remain unverified or incomplete.

## 9. Feedback requested from the doctor

The interview should help establish whether the implemented workflow fits actual documentation practice and what should be prioritized next.

1. What information must appear in an initial assessment and in a follow-up note?
2. Are the seven sections useful, or should their names, order, or content change?
3. Which statements should always be written or explicitly confirmed by the clinician?
4. How should client reports, observations, clinical interpretations, and agreed plans be distinguished?
5. Is the source-review interface practical, and what would make an AI draft easier to check?
6. Which transcription or note errors would be most consequential in practice?
7. What consent, confidentiality, access, retention, and backup expectations should guide any future pilot?
8. Which example sessions and evaluation criteria would be appropriate for professional review?
9. Which feature would provide the most value first: transcription, evidence search, structured drafting, or session history?

Interview findings have not yet been collected. The doctor’s endorsement, approval, or evaluation must not be assumed.

## 10. Suggested next priorities

| Priority | Next step | Expected evidence |
|---|---|---|
| 1 | Demonstrate the prototype and collect clinician feedback | Agreed note structure, required fields, and usability priorities. |
| 2 | Review representative transcripts and generated notes | Recorded corrections and ratings for accuracy, attribution, omissions, and usefulness. |
| 3 | Complete independent retrieval annotation/evaluation | Human-authored judgments and results using frozen settings. |
| 4 | Evaluate speech and full-workflow performance | Reference-based transcription checks and measured latency on intended hardware. |
| 5 | Define requirements for a future clinical pilot | Agreed access, consent, storage, backup, deployment, and acceptance requirements. |

## 11. Source basis and reuse guidance

This report summarizes the current implementation and available project records as of 5 November 2026. It does not replace a clinical evaluation. No overall completion percentage is assigned because a complete, measurable acceptance checklist is not defined.

Main supporting files:

- [Project README](../README.md)
- [Earlier project status and milestone updates](PROJECT_STATUS.md)
- [Retrieval implementation status](RETRIEVAL_IMPLEMENTATION_STATUS.md)
- [Research evaluation workspace](RESEARCH_EVALUATION.md)
- [Pilot review and independent final evaluation status](RETRIEVAL_REVIEW_EXPERIMENTS.md)
- [Local note generation](NOTE_GENERATION.md)
- [Structured formulation review](NOTE_FORMULATION_REVIEW.md)
- [Token recovery verification](NOTE_TOKEN_RECOVERY_VERIFICATION.md)
- [Latest drafting optimization and measurements](NOTE_DRAFTING_OPTIMIZATION.md)
- [Local meeting setup and limitations](ONLINE_MEETINGS.md)

**For later report generation:** Preserve the distinction between implemented features, measured engineering results, and pending clinical validation. Do not convert AI-derived labels into independent human evidence, isolated timings into guaranteed performance, or planned features into completed work. This report is self-contained; the links above provide optional supporting detail.

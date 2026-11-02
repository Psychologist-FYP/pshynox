# Pshynox: conversation data and retrieval implementation brief

Prepared 1 November 2026. This pack supports implementation and engineering evaluation, not clinical validation. No models have been trained or benchmarked in this pack.

## 1. What is included

### PriMock57: two complete audio consultations

Source: https://github.com/babylonhealth/primock57
Paper: https://aclanthology.org/2022.acl-short.65/

Creators: Alex Papadopoulos Korfiatis, Francesco Moramarco, Radmila Sarac and Aleksandar Savkov; released by Babylon Health, 2022. License: CC BY 4.0, reproduced in data/primock57/LICENSE.md. Retain attribution when sharing.

- day1_consultation01: approximately 7 minutes 38 seconds; gastrointestinal complaint.
- day1_consultation02: 9 minutes 19 seconds; skin complaint.
- Each has separate doctor and patient WAV tracks, corresponding human TextGrid transcripts, and a clinician's note JSON.
- WAVs are original mono, 16 kHz, 16-bit PCM. Doctor/patient tracks share the consultation timeline. Do not concatenate tracks end-to-end. Consultation 01 tracks differ in length by 0.06 seconds.
- Derived CSVs combine nonempty transcript intervals in timestamp order, preserving speaker, source filename and interval ID. Their text is unchanged apart from decoding TextGrid quotation escaping. The originals remain authoritative.
- These are acted primary-care consultations, NOT psychologist–patient sessions and NOT diagnostic ground truth. Use them for audio import, ASR, timestamp alignment, evidence retrieval and note-grounding tests. Do not use their voices to validate mood detection.

The complete source dataset contains 57 consultations. To obtain all audio, install Git LFS before cloning:

```bash
git lfs install
git clone https://github.com/babylonhealth/primock57.git
```

Git LFS is essential: a small text pointer is not an audio recording.

### AnnoMI: counselling dialogue text

Source: https://github.com/uccollab/AnnoMI
Paper: https://doi.org/10.1109/ICASSP43922.2022.9746035

Included: original AnnoMI-simple.csv and publisher README. Checked locally: 9,699 utterances, 133 conversations. Wu et al. provide motivational-interviewing demonstrations with therapist/client roles, timestamps, source video URLs and change/sustain/neutral client-talk labels.

Use this for counselling-domain retrieval and, separately, supervised client-talk classification. Those labels are not MDD, GAD or bipolar diagnoses and are not relevance judgments for your search queries. Do not label sustain talk as a disorder or general noncompliance.

No AnnoMI audio is included. Its CSV links to videos; permission to reuse each underlying recording must be checked separately. The official repository inspected here has no standalone license file, so do not assume a mirror's CC0/OpenRAIL label establishes the original licensing. The publisher explicitly offers the data for research; resolve licensing with the creators before further redistribution or product use. Preserve the supplied citation and README.

### DAIC-WOZ: next dataset to request

Official application: https://dcapswoz.ict.usc.edu/

189 interview sessions, participant audio and transcripts, with questionnaire-related data. Interviews use Ellie, a human-controlled virtual interviewer, rather than a psychologist conducting therapy. Access requires a signed request using an academic email and is limited to academic/nonprofit research. Ask your supervisor to support the application. No restricted data is included in this pack.

This is the more relevant next audio resource for depression-related research. Observe the agreement and official partitions. It does not establish Urdu/code-switched performance, bipolar detection, or repeated-session longitudinal validity.

MTS-Dialog remains an optional text-only note-generation dataset: https://github.com/abachaa/MTS-Dialog . It is not an audio source and is not included here.

## 2. Define the task correctly

The task is **retrieval of source-linked clinical evidence**. BM25 ranks transcript passages for a query. It neither diagnoses a disorder nor independently extracts medically valid symptoms.

Recommended flow:

1. Audio -> existing ASR -> clinician-corrected, speaker-labelled transcript.
2. Preserve original transcript; create searchable turn windows with stable source IDs.
3. Run clinician-approved evidence queries through alternative retrieval methods.
4. Return ranked passages with neighbouring turns, timestamps and speaker roles.
5. Annotate concepts and assertion context (negated, uncertain, historical, other person).
6. Give the LLM evidence grouped by note section, with source IDs; require unsupported fields to remain unspecified.
7. Clinician reviews the draft and can inspect the full transcript and original audio.

Do not reduce the transcript to disconnected keywords. Do not use one top-k list as the only input for a complete note: it can omit interventions, plans, denials or unexpected relevant content. Retrieve across note sections and evaluate omissions against a full-transcript baseline.

Acoustic features require a separate audio branch. Text retrieval cannot recover pitch, voice quality or pauses, and these datasets do not by themselves validate longitudinal progress.

## 3. Retrieval methods to compare

All methods take the SAME query, eligible transcript windows and metadata filters. All return window IDs, source segment IDs, text, timestamps, speaker roles, rank and retrieval score. Scores are relevance scores, never clinical probabilities.

| Method | Suggested implementation | Why compare it | Main limitation |
|---|---|---|---|
| Existing keyword fallback | Existing FixtureRetrievalProvider | Measures improvement over the current app | Substrings and match counts miss meaning and context |
| TF-IDF + cosine | scikit-learn TfidfVectorizer; word unigram/bigram baseline | Lightweight lexical baseline | Weak with paraphrases and language mismatch |
| Okapi BM25 | rank_bm25.BM25Okapi | Strong first implementation; weights terms with frequency saturation and length normalization | No semantic understanding or negation interpretation |
| Dense retrieval | intfloat/multilingual-e5-small through sentence-transformers | Retrieves paraphrases and offers multilingual coverage | Urdu/code-switching quality must be measured; may retrieve plausible but irrelevant text |
| Hybrid BM25 + E5 | Reciprocal Rank Fusion (RRF) | Combines lexical and semantic ranking | Benefit is a hypothesis to test, not a guaranteed improvement |

Start with BM25 plus metadata/Boolean filters, then compare TF-IDF and E5. Treat hybrid as a final experiment. No training from scratch is necessary.

BM25: try k1=1.5, b=0.75 as initial settings; tune only on development conversations. Use the same tokenizer for index and query. Boolean AND/OR/NOT and quoted phrases require separate parsing/filter logic; rank_bm25 does not supply a complete Boolean search engine. Query NOT is not linguistic negation detection.

E5-small: 384-dimensional embeddings, maximum 512-token inputs. Prefix query text with `query: ` and passages with `passage: `, including non-English text. Normalize embeddings; cosine/dot-product ranking over a small per-session matrix is sufficient initially. Do not add a vector database just for two conversations. Keep windows within the actual tokenizer limit and record any truncation. Model card: https://huggingface.co/intfloat/multilingual-e5-small . MIT license; locally cache weights for offline use.

RRF: score(d) = sum over retrievers of 1/(60 + rank(d)), using ranks starting at 1; absent documents contribute zero. Retrieve top 20 from each method initially (or all if fewer), deduplicate by stable window ID, then return top 5/10. The constant and candidate limits are experiment settings. Do not add raw BM25 and cosine scores directly.

Optional concept extraction: clinician-reviewed phrase rules using spaCy PhraseMatcher, followed by medspaCy ConText. This is a separate component from retrieval. English resources do not establish Urdu support; build and validate Urdu/Roman-Urdu negation and experiencer rules separately.

## 4. Evidence queries and context

These are proposed engineering categories, not a diagnostic checklist. Have a psychologist review the query vocabulary and labels before clinical evaluation.

| Category | Example query | What the result should preserve |
|---|---|---|
| Sleep | difficulty sleeping, waking, sleep duration | Timing, duration, tiredness, denials |
| Interest and mood | loss of enjoyment, low mood | Patient wording, duration, context |
| Worry | persistent worry, hard to stop worrying | Topic, frequency, functional effect |
| Energy/activity | energy changes, unusual activity | Context and explicit comparisons to baseline |
| Functioning | effects on work, study, relationships | Concrete examples and time period |
| Change talk | intention, ability or reasons to change | Target behaviour and surrounding dialogue |
| Treatment and plan | intervention, agreed goal, follow-up | Clinician speaker and agreed actions |

Invented examples for unit tests (NOT dataset excerpts):

- “I don't feel anxious anymore”: a mention with negation/change context, not positive evidence of current anxiety.
- “My sister has depression”: other-person history, not the patient's diagnosis.
- Clinician: “Have you been sleeping less?” Client: “No”: retain both turns; the question alone is not evidence.
- “I sleep four hours because of night shifts”: retain the explanation; do not infer mania.
- “Neend nahi aati”: proposed Roman-Urdu sleep query example requiring local validation, not a clinically validated mapping.

Preserve negation, numbers, duration, punctuation and speaker labels. Keep Unicode-normalized searchable copies without overwriting source text. Avoid blanket stopword removal: words such as no/not/never/nahi can change meaning. Include both positive and negative evidence; missing retrieval results mean “not retrieved,” not “symptom absent.”

## 5. Index and application integration

The supplied PROJECT_STATUS(1).md describes an integrated FastAPI/SQLite application with whisper.cpp transcription, stable segment IDs, manual speaker roles and revision tracking. Evidence search still uses FixtureRetrievalProvider. This brief is based on that status report, not a fresh repository audit.

- Inspect services/api/retrieval.py, adapters.py and current API contracts before changes; replace the fallback behind the existing interface.
- Start with a central turn plus one preceding/following turn as context; compare with larger windows on development data. Preserve which turn matched and which turns are context.
- Store patient_id, session_id, recording_id, transcript_revision, window_id, source_segment_ids, timestamps, role and original text.
- Filter to the authorized patient/session BEFORE ranking. Include clinician turns for note-plan queries; distinguish them from client evidence.
- Rebuild/invalidate indexes and embeddings after transcript text or role edits. Never return deleted or superseded sources. Keep existing note-staleness behaviour intact.
- Never index reference notes, diagnosis labels, AnnoMI annotation columns or test judgments as if they were spoken transcript text.
- Cache model and tokenizer revisions; record package versions, normalization, chunking and settings in benchmark output.

## 6. Evaluation plan

Two audio consultations are smoke tests, not a sufficient benchmark. Start with them, then annotate approximately 10–20 AnnoMI conversations and 30–50 queries, including queries with no relevant passage. These are suggested pilot sizes, not powered clinical study sizes.

Create relevance judgments manually: query_id, conversation_id, source_segment_id, grade (0 irrelevant, 1 contextual/partial, 2 direct evidence), reviewer and rationale. Mark the relevant source spans before viewing model rankings where feasible. Judge all turns in selected small conversations so recall has a meaningful denominator. Keep overlapping windows from counting the same evidence multiple times. Have a psychologist review a subset and resolve disagreements.

Split by conversation, and by participant/speaker identity where available; never randomly split turns from the same conversation across development and test. Keep clinician queries, vocabulary choices and thresholds fixed before final test evaluation.

| Component | Input and reference | Measurements |
|---|---|---|
| ASR | PriMock audio against checked human transcripts | WER; inspect negation, numbers and speaker attribution errors |
| Every retrieval method | Queries + transcript windows against manual relevance judgments | Recall@5/10, Precision@5, MRR@10, nDCG@10; query latency and memory |
| No-answer behaviour | Queries with no relevant evidence | False-positive return rate and abstention behaviour; report separately from recall |
| Context tagging | Annotated negation/experiencer/time examples | Precision, recall, F1 per category and false positive examples |
| Note support | Retrieved evidence and generated draft versus full transcript | Supported-claim rate, omitted relevant facts, wrong-speaker attribution; clinician review |

Run retrieval on both reference transcripts and raw ASR transcripts to isolate transcription-related degradation. Add English/Urdu/code-switched fictional recordings with checked transcripts later; do not claim Urdu validation from the English pack. Synthetic/TTS recordings can test plumbing but cannot validate real mood acoustics.

## 7. Prompt to give Codex

“Read PROJECT_STATUS(1).md and inspect the actual repository. Implement Module B behind the existing evidence interface, preserving stable source IDs, patient/session boundaries and revision invalidation. Use this starter pack for initial tests. First implement configurable BM25 and Boolean/phrase filters, then comparable TF-IDF and multilingual-e5-small providers. Add hybrid RRF as an experiment. Keep search ranking separate from optional concept/context tagging, and never output disorder diagnoses or clinical probabilities from retrieval scores. Preserve negation, speaker roles, timestamps and surrounding turns. Provide a benchmark command using manually reviewed query relevance labels; do not manufacture ground truth from model predictions or treat AnnoMI talk labels as relevance labels. Compare the existing fallback, TF-IDF, BM25, E5 and hybrid on the same fixed test cases. Report measured retrieval quality, latency, memory and ASR effects. Confirm that editing a transcript invalidates stale indexes and that one patient's search cannot return another patient's text. Keep reference notes and labels out of the retrieval index. Do not redesign the existing working speech/UI pipeline.”

## 8. Primary technical sources

- BM25 implementation and preprocessing responsibility: https://github.com/dorianbrown/rank_bm25
- TF-IDF: https://scikit-learn.org/stable/modules/feature_extraction.html#text-feature-extraction
- E5 model and required prefixes: https://huggingface.co/intfloat/multilingual-e5-small
- RRF, Cormack, Clarke and Buettcher, SIGIR 2009: https://doi.org/10.1145/1571941.1572114
- Clinical context rules: https://github.com/medspacy/medspacy

Files are supplied as research inputs. No clinical efficacy, accuracy result or endorsement is implied.

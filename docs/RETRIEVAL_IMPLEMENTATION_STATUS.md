# Retrieval implementation audit

Checked against `Pshynox_Retrieval_Starter/START_HERE.md` on 1 November 2026.
**The core retrieval implementation is ready for engineering testing. The entire
research/clinical evaluation plan is not complete.**

## Research evaluation milestone — 2 November 2026

Implemented and tested:

- All 17 manifest entries match size/SHA-256. All 133 AnnoMI conversations (9,699
  turns) and two PriMock conversations (109/130 turns) are imported under
  `data/research/`. Originals, supplied IDs, text, roles, timestamps and source
  filenames are preserved. Legacy PriMock schemas remain supported.
- `/workspace/research` provides dataset/conversation browsing, query wording and
  intended evidence, reviewer/rationale, explicit 0/1/2 judgments, unreviewed states,
  keyboard controls, atomic persisted progress, revision-conflict protection and
  no-answer confirmation. Rankings stay hidden during annotation. Development-only
  exploration is separate and exposure is recorded.
- Seed 20261102 selects 10 AnnoMI conversations: seven development and three test.
  Shared source-video/known-identity groups remain together. Supplied participant
  identities are unavailable; speaker independence is not established. Expansion
  preserves preassigned splits. There are 40 AnnoMI draft suggestions and eight
  PriMock drafts, **zero completed judgments and zero reviewer approvals**.
- Complete pilot-split exports are validated using the existing benchmark loader.
  The results viewer reuses the existing metric implementation, supports background
  runs, per-method failures/unavailability, configuration snapshots, source/grade/
  context inspection, comparison charts, JSON/CSV exports and report import.
  Threshold experiments are development-only; test can use a measured frozen
  development configuration. Missing measurements display as missing, never zero.
- PriMock audio utility produces four synchronized clip pairs (eight WAVs), all
  within five minutes, with matching clip-relative references, source offsets,
  original hashes, padding and explicit boundary exclusions. The supplied material
  needed zero excluded turns. All clips inherit their parent consultation split.
- Research imports/annotations/runs do not create clinical records. Isolation tests
  use temporary storage and verify existing clinical records are unchanged.

Actual verification on this milestone:

- Backend suite: **102 passed in 8.51 seconds** (API, retrieval, speech, contracts).
- Chrome: **3 passed in 11.7 seconds**, including annotation persistence,
  unreviewed vs zero, no-answer confirmation, validated export/benchmark round trip,
  existing fictional results, charts, missing methods, context and the existing
  clinical retrieval regression.
- Frontend production build: **passed**; `git diff --check`: **passed**.
- Strengthened clinical-content isolation check: all six research API tests pass
  in 2.23 seconds. Existing patient/session/note contents, not just counts, are
  unchanged by research annotation.
- Standard `scripts/run.py` smoke check: research UI and all 135 imported
  conversations load through the frontend proxy from outside the repository root;
  Ctrl+C closes both ports and the original clinical database SHA-256 is unchanged.
- Fresh benchmark: **all five methods measured**, on the nine-query fictional
  fixture only; `data/research/fictional-verification.json` includes the evaluated
  snapshot, configuration, package versions and checksums.
- API-stall investigation: a bounded sandbox run hangs entering Starlette's
  TestClient, before a retrieval request. The same eight retrieval API tests pass
  outside the sandbox in 0.84 seconds; the complete suite also passes there. No
  application workaround was added for this environment-specific test restriction.

Awaiting human work: refine/approve queries, grade every source turn with reviewer
and rationale, confirm actual no-answer queries, independently review a subset,
resolve disagreements, tune on development and evaluate held-out test.

Not measured: labelled real-dataset retrieval quality, actual ASR degradation,
Urdu/code-switching quality, clinical usefulness and note-generation quality. Raw
ASR output is not assumed to align to reference IDs. No diagnostic, acoustic mood,
additional retrieval model or LLM note-generation feature was added.

Start with `./scripts/run.py`; see [the research walkthrough](RESEARCH_EVALUATION.md)
for annotation, evaluation, CLI/report import and speech-pipeline clip testing.

## What is implemented

| Plan item | Status and evidence |
| --- | --- |
| Replace keyword fallback behind the existing evidence interface | Done. API defaults to BM25; original substring baseline remains available. |
| Configurable BM25 and shared normalization | Done. k1/b/context/cache settings; Unicode tokenizer; no blanket stopword removal. Positive BM25 IDF variant is documented. |
| AND / OR / NOT, phrases and parentheses | Done. Shared parser/filtering for all providers, explicit precedence and syntax errors. |
| TF-IDF and multilingual E5 comparison providers | Done. Actual dependencies installed and E5 model cached locally at pinned commit `614241f622f53c4eeff9890bdc4f31cfecc418b3`. Both ran in the benchmark. |
| Experimental hybrid RRF | Done. Top 20 candidates from BM25/E5, rank-based fusion with constant 60. |
| Stable source IDs, original text, timestamps, speaker roles and neighbors | Done. Central source link is preserved; additive source/window metadata and expandable dialogue context are returned. |
| Patient/session and metadata filtering | Done. Session-scoped database reads, patient consistency checks, role/recording/time filters before ranking. Existing local access limits still apply; user-account authorization was not added. |
| Invalidate stale indexes after changes | Done. Content/revision/source fingerprints plus explicit invalidation on saved text/role edits; append/delete/replace checked. Existing note-staleness behavior retained. |
| Keep notes and dataset labels out of the index | Done. Only transcript segments enter ranking. Research importers select spoken-text columns and do not generate relevance judgments. |
| E5 prefixes, normalization, token limits and offline cache | Done. Query/passage prefixes, L2 vectors, tokenizer-budget chunking and truncation diagnostics; local-only runtime by default. |
| Benchmark command and comparison metrics | Done. Complete external judgments required; source deduplication, Recall@5/10, Precision@5, MRR@10, nDCG@10, no-answer behavior, latency, isolated-process RSS and versions/settings. |
| ASR comparison tooling | Implemented. Aligned reference/ASR source IDs, WER and paired retrieval deltas are supported. Actual ASR effects have not been measured. |
| Fictional patient for UI testing | Done. `patient-retrieval-demo`, two sessions, 28 editable turns. See [the demo guide](RETRIEVAL_DEMO.md). |

Implementation: `services/retrieval/`, `services/api/retrieval.py`, the existing
evidence route and additive evidence schemas. Setup/API details: [RETRIEVAL.md](RETRIEVAL.md).

## What remains or differs from the proposed experiments

- All starter data is now present and verified (17 manifest entries). AnnoMI and
  both PriMock transcripts have been imported. No labelled real-dataset evaluation
  has run: the pilot awaits human query approval and relevance annotation.
- Clinician-reviewed queries, complete relevance judgments, development tuning,
  held-out evaluation, reviewer disagreement resolution and no-answer threshold
  calibration remain to be done with actual data.
- Urdu/code-switching quality, real ASR degradation, acoustic features, longitudinal
  progress and clinical efficacy remain unvalidated. Literal Unicode examples test
  plumbing only.
- Optional concept/assertion-context tagging (negated, historical, uncertain,
  other-person) is **not implemented**. Retrieval preserves the wording and speakers
  for human review; it does not automatically assign those labels.
- Current ranking scores central turns. Preceding/following turns are context,
  rather than additional scored evidence. Context radius is configurable; comparing
  larger *scored* windows remains an experiment.
- An LLM evidence-to-note pipeline, full-transcript note-support comparisons,
  omitted-fact measurements and clinician note-quality evaluation were not added.
  Existing exact-excerpt note population and review gates remain in use.

## Historical verification and measured engineering results (1 November)

- Backend suite: **76 passed**.
- Frontend production build: **passed**.
- Retrieval Chrome test: **passed**, covering source context, query errors without
  hiding the transcript, and refreshed evidence after editing.
- Seeded-patient API checks: **14 searches passed**, plus wrong-patient rejection.
- All five methods ran on the same **9 fictional queries / 7 source turns**: seven
  answerable queries and two no-answer queries. No clinical relevance claims follow.

| Method | Recall@5 | MRR@10 | nDCG@10 | No-answer false-positive return rate | Warm median (ms) | Peak process RSS (MiB) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing fallback | 1.000 | 0.929 | 0.947 | 0.50 | 0.46 | 34.1 |
| TF-IDF | 1.000 | 1.000 | 1.000 | 0.00 | 2.14 | 176.0 |
| BM25 | 1.000 | 1.000 | 1.000 | 0.00 | 0.59 | 54.4 |
| E5 | 1.000 | 0.905 | 0.929 | 1.00 | 30.19 | 1316.1 |
| Hybrid RRF | 1.000 | 1.000 | 1.000 | 1.00 | 28.06 | 1317.8 |

Precision@5 is 0.20 for each method because each answerable fixture query has just
one relevant source and the metric uses a denominator of five. Dense and hybrid
return candidates by default, including for no-answer cases; these measurements
show why development-set abstention calibration is still required. First searches
also include cold import/model loading. Memory includes the interpreter, native
libraries and model weights; warm timings include tracemalloc instrumentation.

Full local report: `data/retrieval-engineering-benchmark.json`.
Seeded patient results: `data/retrieval-demo-checks.json`.
Both are local artifacts; real patient data and model weights are excluded from Git.

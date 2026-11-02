# Module B: source-linked evidence retrieval

See [implementation status](RETRIEVAL_IMPLEMENTATION_STATUS.md) for the audit and
measured limits, or [the fictional patient guide](RETRIEVAL_DEMO.md) to test the UI.

The existing evidence endpoint now uses local BM25 by default. The speech pipeline,
source segment IDs, note-population logic and review gates retain their existing
interfaces. Search does not diagnose, infer assertion context, generate notes, or
interpret a relevance score as a clinical probability.

## Setup and providers

```bash
python -m pip install -e '.[dev]'
```

Set `RETRIEVAL_METHOD` in `.env` and restart the API. It is independent of
`FIXTURE_MODE`, which controls demonstration session availability.

| Method | Implementation | Network during search |
| --- | --- | --- |
| `fixture` | Original lowercase substring/count ranking, through the shared source/filter layer | None |
| `bm25` (default) | `rank_bm25.BM25Okapi`, k1=1.5, b=0.75, positive Robertson IDF | None |
| `tfidf` | scikit-learn word unigrams/bigrams and L2-normalized cosine | None |
| `e5` | CPU multilingual-e5-small, normalized query/passage vectors | Local cache by default |
| `hybrid` | BM25 + E5 reciprocal rank fusion, constant 60, top 20 per retriever | Local cache by default |

BM25 uses `log(1 + (N-df+0.5)/(df+0.5))` IDF, deliberately replacing the library's
potentially negative IDF on tiny session corpora. Query and index use the same
NFKC/casefold Unicode tokenizer. Letters, numbers and combining marks are retained;
stopwords are preserved, including no/not/never/nahi. Source text is never normalized
in place. Library references: [rank_bm25](https://github.com/dorianbrown/rank_bm25),
[TF-IDF](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html).

Dense setup is optional and larger than the lexical installation. Use a compatible
CPU PyTorch installation for your Python/platform, then:

```bash
python -m pip install -e '.[dense]'
python -m services.retrieval.cache_model --revision 614241f622f53c4eeff9890bdc4f31cfecc418b3
```

Set `RETRIEVAL_E5_REVISION` to that commit and `RETRIEVAL_METHOD=e5` or `hybrid`.
The default/example configuration already pins this revision. The cache command
also writes the resolved commit to `models/retrieval/model-revision.json`.
The explicit cache command downloads weights; runtime `RETRIEVAL_E5_LOCAL_ONLY=true`
uses only local files and returns HTTP 503 when dependencies/weights are missing.
No silent switch to another method occurs. Model weights and indexes are not
committed. The cache location defaults to `models/retrieval`.

E5 uses `query: ` and `passage: ` prefixes for every language and normalized
embeddings. Long central turns are split using the actual tokenizer budget of at
most 512 tokens, including prefixes/special tokens. All chunks are encoded and the
highest chunk similarity represents the turn. Returned evidence keeps the complete
original turn. Query truncation, split source IDs, token limit and resolved
model/tokenizer revisions are recorded in diagnostics. See the
[E5 model card](https://huggingface.co/intfloat/multilingual-e5-small).

## Evidence API and query grammar

```text
GET /api/v1/sessions/{session_id}/evidence?query=sleep%20AND%20NOT%20medication&k=10
```

Optional filters: `patient_id`, `speaker_role` (`CLIENT`, `CLINICIAN`, `UNKNOWN`),
`recording_id`, `start_ms`, `end_ms`, and `section` (an existing note section).
Time filters select central turns overlapping the inclusive range. Session lookup,
patient consistency and metadata filters happen before ranking. The optional
patient filter is an ownership consistency check; this prototype still has no
clinician authentication or account authorization layer.

- Uppercase `AND`, `OR`, `NOT`, parentheses, and `"quoted phrases"` are supported.
- Precedence: NOT, AND, OR. Adjacent plain words imply OR; `sleep NOT medication`
  means `sleep AND NOT medication`.
- Lowercase `not` is searchable text. Query `NOT` is a literal exclusion, not
  linguistic negation detection. Negated and other-person mentions remain evidence
  for review, without being converted into patient symptoms.
- Quoted phrases mean adjacent normalized tokens. Punctuation does not break token
  adjacency. Explicit operators/phrases are hard filters for every method. Plain
  queries are soft semantic queries for E5/hybrid, allowing paraphrases.
- A pure exclusion query lists matching sources at score 0; it has no positive
  relevance signal. Empty or punctuation-only queries return no results. Invalid
  syntax returns HTTP 422; queries are limited to 2,000 characters and 256 atoms.
- `RETRIEVAL_MIN_SCORE`, when set, is a method-specific abstention threshold. Tune
  it on development conversations. Dense/hybrid normally return ranked candidates
  even for no-answer queries; their false-positive return rate must be measured.

Every provider ranks **central turns**, with one preceding/following turn returned
as context (configurable `RETRIEVAL_CONTEXT_TURNS`, 0–5). Context-only turns do not
gain a score or become extra evidence hits. This conservative choice avoids
attributing a clinician's question to a client's denial and allows source-level
evaluation without counting overlapping evidence repeatedly. Larger *ranking*
windows are a future experiment; increasing context currently changes presentation.

The original `segment_id`, `session_id`, `start_ms`, `end_ms`, `excerpt`, `score`,
`section`, and `query` fields retain their meaning. Additive fields provide:

- Stable `window_id`, `rank`, `method`, patient/recording ID and central speaker role.
- `source_segment_ids`, `matched_source_segment_ids` (central turn), and `sources`.
  Each source contains original text, role, timestamps, revision, recording ID and
  `matched`, so callers can distinguish context from the ranked turn.
- `transcript_revision`, a SHA-256 snapshot of current source content/metadata,
  independent of the query and presentation section, plus retrieval diagnostics.

Independent recordings have separate timelines, preventing context from crossing
timestamp resets. Separate tracks belonging to one meeting share context and keep
their individual recording IDs. The UI displays source roles and expandable
surrounding dialogue while preserving jump-to-source navigation. Search errors do
not hide the transcript editor; refreshed transcripts trigger refreshed evidence.

## Index lifetime and integration

`create_retrieval_provider().search(query, session_id, current_segments, k=10,
scope=SearchScope(...))` is the implemented seam. Module C loads current segments
and source bindings from the session; Module B never reads the database or notes.
The old abstract `RetrievalAdapter` is historical scaffolding.

An in-memory LRU (8 patient/session entries by default) holds lexical indexes and
dense matrices. Full segment content, revisions, roles, timestamps, source bindings,
session ownership and filters key reuse. Every request validates the snapshot, so
append/delete/replace and speech-worker changes rebuild on the next query. Saved
text/role edits also explicitly invalidate that session's cache. Nothing persists
across API restarts. Existing note-staleness and review behavior remains enforced.

## Reproducible evaluation

```bash
python -m services.retrieval.benchmark \
  --dataset tests/retrieval/fixtures/engineering.json \
  --output data/retrieval-engineering-benchmark.json
```

This compares all five methods on exactly the same queries, sources and filters.
Unavailable optional models are reported explicitly. Add `--require-all` to return
a failure status unless every requested method runs, or use
`--methods fixture tfidf bm25` for a lexical-only run.

The supplied JSON is a **fictional engineering smoke test**, with explicit literal
match expectations. It contains no dataset excerpts or clinician-reviewed clinical
ground truth. Its measurements cannot establish clinical or Urdu retrieval quality.

For real evaluation, author a JSON dataset with these top-level arrays:

```json
{
  "conversations": [{
    "conversation_id": "consultation-01", "split": "test",
    "participant_ids": ["participant-01"],
    "segments": [{"id": "turn-01", "session_id": "consultation-01",
      "start_ms": 0, "end_ms": 1000, "speaker_role": "CLIENT",
      "text": "Original transcript text", "revision": 1}]
  }],
  "queries": [{"query_id": "q1", "conversation_id": "consultation-01", "text": "sleep"}],
  "judgments": [{"query_id": "q1", "conversation_id": "consultation-01",
    "source_segment_id": "turn-01", "grade": 0,
    "reviewer": "Reviewer name", "rationale": "No sleep evidence in this turn"}]
}
```

Queries can include `filters` matching `SearchScope`. Conversations can provide
`patient_id`, `recording_ids` and `timeline_ids` maps. Judge every source turn for
each query: 0 irrelevant, 1 contextual/partial, 2 direct. Relevance must reflect the
query's filters. The runner rejects missing/duplicate judgments and participant
identity crossing dev/test splits. Annotate before viewing rankings; have a
psychologist review a subset. Do not derive judgments from retrieval predictions,
diagnostic labels, reference notes, or AnnoMI change/sustain labels.

The report records source-deduplicated Recall@5/10, Precision@5, MRR@10, nDCG@10;
no-answer false-positive return and abstention rates; first-search and warm
latencies; package versions, dataset hash, chunking/normalization, settings and
model revisions. Each method/variant runs in a fresh process for peak RSS,
including native/model allocations. Python tracemalloc peak is recorded separately.
Warm timings include source fingerprinting, exclude startup, and are measured under
tracemalloc; compare them as instrumented engineering timings, not production SLAs.
Changing settings on the test split invalidates a fixed-test comparison.

For ASR effects, add `asr_segments` to each paired conversation with manually
aligned identical source IDs. The runner computes normalized-token WER and paired
reference-vs-ASR retrieval metric deltas. It does not pretend unaligned ASR segments
are the same sources. Without paired ASR it reports `not_measured`.

## Starter data

All 17 starter manifest entries have been verified by size and SHA-256. AnnoMI
(133 conversations / 9,699 turns), both PriMock transcript CSVs (109 / 130 turns),
and four original WAV tracks are present. No downloads are required. Real-dataset
quality and ASR effects remain unmeasured pending human relevance annotation and
checked ASR alignment.

The [research workspace guide](RESEARCH_EVALUATION.md) covers the annotation UI,
pilot, results viewer, threshold experiments and synchronized audio preparation.
Run `.venv/bin/python -m services.retrieval.prepare` to reproduce the isolated
imports and 40 unapproved AnnoMI draft query suggestions. Start with `./scripts/run.py`
and open `/workspace/research`. Research imports never create clinical patients.

For individual imports, spoken transcript text becomes an unjudged template:

```bash
python -m services.retrieval.import_data --format annomi \
  --input /path/to/AnnoMI-simple.csv --output data/annomi-retrieval.json
python -m services.retrieval.import_data --format primock \
  --input /path/to/day1_consultation01_transcript.csv --output data/primock-retrieval.json
```

AnnoMI uses transcript/utterance IDs, interlocutor, text and timestamp only. Its
start timestamps are retained as point intervals because it lacks authoritative
ends. PriMock explicitly accepts the supplied `segment_id`, `source_file`, `speaker`,
`start_seconds`, `end_seconds` and `text` columns. Supplied IDs are preserved.
The older source-file/interval-ID formats and timestamp aliases remain supported;
legacy IDs retain their original deterministic derivation. Missing fields, duplicate
IDs and invalid/reversed timestamps are rejected. Source filenames remain metadata. No note JSON, talk labels,
diagnosis columns or relevance judgments are indexed/generated. Imports do not
create application patient records. Set conversation splits before evaluation and
retain original attribution, citations and usage terms from the starter brief.

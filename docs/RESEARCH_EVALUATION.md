# Research retrieval workspace

Start the complete app from the repository root:

```bash
./scripts/run.py
```

Open <http://127.0.0.1:5173/workspace/research>, or select **Research evaluation**
in the clinical workspace navigation. The research page does not mount the clinical
patient directory. Research routes never use the clinical database.

## Prepare the supplied data

This workspace has already been prepared. To reproduce it on another checkout:

```bash
.venv/bin/python -m services.retrieval.prepare
```

The utility checks all 17 files against the starter manifest (size and SHA-256),
imports AnnoMI (133 conversations / 9,699 turns) and both PriMock consultations
(109 and 130 turns), and preserves existing research progress on repeat runs.
It does not download data or create patients. All conversations are browseable;
turn off **Pilot conversations only** to see the rest.

Files live under ignored `data/research/` (override with `RESEARCH_DATA_DIR`):

- `datasets/`: versioned transcript imports and pilot selection metadata.
- `annotations/`: query drafts, explicit judgments, reviewer/rationale, revision,
  no-answer confirmations, and exploration exposure.
- `runs/<run-id>/`: immutable dataset/config snapshots, process log and report.
- `audio-clips/`: prepared audio, offset/exclusion manifests and clip references.

Publisher README, citations and usage terms remain authoritative. No reference
notes, AnnoMI behavior/change/sustain labels, or diagnosis fields enter searchable
transcript text. PriMock `source_files` metadata is retained separately.

## Pilot and splits

Seed **20261102** selects these AnnoMI conversations:

| Development | Held-out test |
| --- | --- |
| annomi-24, annomi-97, annomi-103, annomi-19, annomi-3, annomi-40, annomi-50 | annomi-23, annomi-87, annomi-21 |

The manifest records the seed, IDs, source groups and split of every conversation.
Known participant IDs, when supplied, and shared source-video groups stay together.
The supplied CSVs do not identify participants across videos: **speaker independence
is not established**. PriMock consultation 02 is development, 01 is test. Every
clip inherits its parent consultation's split.

There are **40 AnnoMI draft suggestions**, four per conversation, intended to help
prepare literal, paraphrase, short-reply/context and no-answer questions. They are
not approved queries or ground truth. Rewrite the paraphrase prompts in your own
words and verify the absent-topic candidates against the full conversation. There
are no completed judgments or clinician approvals. Pilot sizes are engineering
choices, not powered clinical-study sample sizes.

To expand, browse a nonpilot conversation and choose **Add this conversation and
its source group to pilot**. Its preassigned split stays fixed. Add queries manually.
A different seed/initial size requires a new output directory (`--output`), so
existing annotations cannot silently be reassigned or overwritten.

## Annotate your first conversation

1. Select **annomi**, **annomi-24**, and **Annotate without rankings**.
2. Read the full transcript. Select a draft or create a query. Enter its wording
   and the evidence you intend it to retrieve. Approve the wording and save it.
3. Enter your reviewer name. For each turn, select **0 irrelevant**, **1 partial
   evidence/useful context**, or **2 direct evidence**, enter a rationale, and choose
   **Save judgment & next**. Grade contextual turns when they help answer the query;
   do not judge only literal word matches. Each saved turn retains its own reviewer.
4. A turn without a judgment remains **Unreviewed**, including after restarting.
   Grade 0 counts as reviewed. **Mark unreviewed** explicitly removes a judgment.
5. For a query with all turns graded 0, additionally confirm that you reviewed the
   **full conversation** and found no relevant evidence. This is a retrieval label,
   not a conclusion that a clinical condition is absent.
6. Repeat for every query in the pilot split. Review counts, draft approval, and
   no-answer confirmation must all be complete before exporting/running that split.

Focus the grading panel: **0/1/2** selects a grade, **J/K** or arrows changes turns.
**Ctrl+Enter** saves and advances, including while editing the rationale. Rationale
reuse requires an explicit checkbox and each judgment still needs a save.
Changing saved query wording or intent clears its prior judgments and confirmation.
Concurrent-tab revision conflicts are rejected; reload saved progress before retrying.

Rankings are hidden during annotation. **Explore without labels** is a separate
mode, with BM25 selected initially. It records exposure and allows development
conversations only. Avoid exploring conversations before their initial annotation;
use an independent reviewer where practical if rankings have already been seen.

## Run and inspect evaluation

Open **Evaluation results**, choose the dataset, split and methods, and press
**Run benchmark**. Incomplete annotations block the run with specific query counts.
The job runs the existing `services.retrieval.benchmark` in isolated worker processes;
there is no separate metrics implementation. Only one benchmark runs at a time.
You may leave the results tab while it runs; restarting the API interrupts an active
job, retains its snapshot, and lets you start a new run.

The viewer shows method status (measured/failed/unavailable), Recall@5/10,
Precision@5, MRR@10, nDCG@10, no-answer false-positive return/abstention rates,
comparison bars, first/warm latency and process memory. Missing results show **—**.
Individual queries show ranked original passages, grades, reviewer rationales,
speaker roles and neighboring context. Scores describe retrieval relevance.

Export the full report JSON (including source/judgment snapshots) or metrics CSV.
**Fictional smoke test** is explicitly labelled; its nine queries do not demonstrate
dataset or clinical accuracy. Clinical/Urdu/ASR efficacy remains unmeasured.

For command-line evaluation, first use **Export validated dataset** to download your
completed pilot split, then run (adjust the downloaded file path):

```bash
.venv/bin/python -m services.retrieval.benchmark \
  --dataset /path/to/annomi-dev.json --split dev \
  --output data/research/annomi-dev-results.json --require-all
```

Use **Import a benchmark results JSON** to view that output in the app. New reports
embed their evaluated dataset so future annotation edits cannot change old results.
Legacy reports without an embedded dataset cannot be imported directly; the existing
fictional benchmark has an explicit compatibility entry with its original fixture.

For a threshold experiment select **one method** and the **development** split,
then enter a minimum score. Compare development runs and choose a configuration.
On test, select that measured development run under **Frozen development configuration**
and use the same methods. The API verifies dataset/version and method provenance.
The CLI equivalent uses `--min-score` on development only; export a frozen config
from the viewer and pass `--config /path/to/config.json` on test. Do not iterate
settings on held-out results; declare any subsequent tuning as a new experiment.

Measurements record source dataset version/hash, annotation revision, evaluated
snapshot, package/model revisions and configuration. First-search totals may include
cold model/library loading but are not one isolated startup latency. Warm measurements
include source fingerprinting and tracemalloc overhead. RSS includes interpreter,
native libraries and model weights; Python allocations exclude most native memory.
Each method has a bounded worker timeout; a failure does not erase other results.

## PriMock audio preparation and speech testing

The upload limit stays five minutes / 100 MB. Generate synchronized clips with:

```bash
.venv/bin/python -m services.retrieval.prepare_audio
```

Already prepared here: **two clip pairs per consultation**, eight WAV files in total,
with **zero boundary-excluded transcript turns**. Existing output folders are preserved;
choose a new `--output` directory when changing preparation settings.

Each clip's doctor and patient tracks use the same original time interval and frame
count. Where the original tracks differ in length, the shorter tail is explicitly
zero-padded and recorded. Near the five-minute limit the utility prefers a boundary
that does not cross any annotated turn. Unavoidable crossing turns are excluded from
clip references and listed in `excluded_boundary_source_ids` / `excluded_source_ids`.
No original recording is changed. The manifest records original hashes, source offsets,
shared clip boundaries, padding, split and reference paths. Clip reference timestamps
are relative to the clip, while source IDs and text are preserved.

To test with the existing speech pipeline:

1. Use a separate development database, for example start with
   `DATABASE_URL=sqlite:///./data/research/speech-demo.db LOCAL_DATA_DIR=./data/research/speech-demo ./scripts/run.py`.
   Stop an existing launcher first so the ports are free.
2. Manually create a clearly labelled research/demo patient and session. Enable recording
   consent. Use one session for each clip **and track** so their independent timelines
   cannot be mistaken for one consecutive recording.
3. Import the appropriate clipped WAV under `audio-clips/<consultation>/` and choose
   **Transcribe locally**. The existing configured Whisper worker handles it.
4. Save the resulting session/recording IDs alongside the clip manifest. Use the doctor
   track as clinician speech and the patient track as client speech; verify roles.
   Compare with the matching role's turns in the clip reference JSON.
5. Inspect and align raw ASR words/spans to the original reference turn IDs. Splits and
   merges are common; identical IDs cannot be assumed from timestamps alone. Keep the
   original raw output as well as any checked alignment. Reference-versus-ASR retrieval
   remains **not measured** until that alignment is checked and paired `asr_segments`
   are supplied to the benchmark. Do not fabricate aligned text or ASR measurements.

## Human work still required

Refine/approve the 40 draft AnnoMI queries, grade every turn for each query with
reviewer and rationale, explicitly confirm true no-answer queries, and have a
psychologist review a subset and resolve disagreements. Then tune on development,
freeze settings, and evaluate test. PriMock needs independently reviewed questions
and judgments too. Audio quality requires actual ASR runs and checked alignment.
No diagnosis, acoustic mood prediction or LLM note generation is added here.

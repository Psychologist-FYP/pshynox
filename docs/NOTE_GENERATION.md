# Local hybrid note drafts (laptop milestone)

**Current note schema: v3.** See [structured formulation review](NOTE_FORMULATION_REVIEW.md)
for the seven-section editor, explicit AI interpretation review, preservation rules and
verification limits. Earlier milestone measurements below describe v1/v2, not v3.

Start from the repository root with `./scripts/run.py --no-reload`.
Open `http://127.0.0.1:5173/workspace`, choose a patient/session, and use **Local AI draft**
below the transcript. Research has a separate page:
`http://127.0.0.1:5173/workspace/research-notes` (development conversations only).
No separate inference server command is needed: the API owns a loopback server,
starts it on demand and terminates/reaps it after each job or writer switch.
Patient generation now fills the structured session note and retains previous saved fields
in its audit history. The clinical decision workflow still requires explicit review and
approval. Research-only generated versions retain their separate review history.
See **Integrated patient notes and printing** below for the current UI workflow.

## Tested installation

No Python dependency was added. Use existing requirements and frontend dependencies.
The tested llama.cpp commit is `7071ba378afa96dcd69c41e1ec2b4912364dbee4`, reporting
`0.8.0-dev (build 1, commit 7071ba3)`, GNU 16.2.1, Linux x86_64. Build CPU runtime:

```bash
git clone https://github.com/ggml-org/llama.cpp.git models/llama.cpp
git -C models/llama.cpp checkout 7071ba378afa96dcd69c41e1ec2b4912364dbee4
cmake -S models/llama.cpp -B models/llama.cpp/build \
  -DGGML_CUDA=OFF -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_BUILD_UI=OFF -DLLAMA_USE_PREBUILT_UI=OFF \
  -DCMAKE_BUILD_TYPE=Release
cmake --build models/llama.cpp/build --target llama-server -j 6
./scripts/run.py --no-reload
```

During this implementation source was built from `/tmp/pshynox-llama` into the same
persistent `models/llama.cpp/build` binary location. Upstream's UI-asset download
attempt failed; UI assets are unnecessary. The commands above disable that feature.
Inference uses explicit local `-m` paths; no Hugging Face auto-download parameters.
API usage: [upstream server documentation](https://github.com/ggml-org/llama.cpp/blob/7071ba378afa96dcd69c41e1ec2b4912364dbee4/tools/server/README.md).

Expected files:

- `models/note_generation/qwen/Qwen3-4B-Instruct-2507-Q4_K_M.gguf`
- `models/note_generation/omi/sum-small-Q4_K_M.gguf` (optional)

Environment settings (also accepted in the existing `.env` by the launcher):

| Variable | Default / meaning |
|---|---|
| `NOTE_LLAMA_SERVER` | `models/llama.cpp/build/bin/llama-server` under repository root |
| `NOTE_QWEN_MODEL`, `NOTE_OMI_MODEL` | Alternate **absolute** GGUF paths |
| `NOTE_GPU_LAYERS` | `0`: CPU; positive explicitly requests offload, actual backend must be checked in runtime.log |
| `NOTE_CONTEXT` | `4096` total prompt + output tokens; explicit override 2048–32768; measure before increasing |
| `NOTE_CALL_TIMEOUT` | `180` seconds per HTTP call |
| `NOTE_JOB_TIMEOUT` | `1800` seconds; checked between calls, readiness and chunk operations |
| `LOCAL_DATA_DIR` | Existing app setting; generated artifacts under `note-generation/` |

GPU failure does not silently cause hosted inference. CPU fallback is the explicit
default, shown in saved metrics; failed GPU load is actionable and requires selecting
0 layers. This CPU build cannot provide CUDA. After repairing the driver, install a
compatible CUDA toolkit and rebuild llama.cpp with `-DGGML_CUDA=ON`; start with small
explicit offload (e.g. `NOTE_GPU_LAYERS=8`) and measure. No GPU acceleration claim.

## Initial hardware and model verification (before driver repair)

Measured: Intel i5-12450H, 12 logical CPUs, ~15 GiB usable RAM (16 GB class),
GTX 1650 Mobile / Max-Q, kernel-reported 4096 MiB VRAM. Fedora 44 KDE,
kernel `7.2.13-200.fc44.x86_64`. NVIDIA driver 615.78.08 is installed but not loaded;
Nouveau owns the device; `nvidia-smi` fails. CPU runtime was used.

Both files parse as GGUF, contain native chat templates, and record file type 15
(Q4_K_M), quantization version 2. Full tensor compatibility is checked by loading.

| Model | Size bytes | SHA-256 |
|---|---:|---|
| Qwen | 2497281120 | `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597` |
| Omi-named file | 2393231264 | `caff8ea593abe1224515ee3e40930cc41eb39ede4ae13a9378228fbd08232983` |

Qwen metadata names Qwen3-4B-Instruct-2507 and quantizer Unsloth, with base model URL
`https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507`; the exact downloaded GGUF source
repository/revision is **unverified**. Omi-named file says architecture phi3/name Phi3;
its originating Omi repository/revision is **unverified**. Filenames do not establish
provenance. Jobs record header metadata, model hash, size, runtime version and template.
Weights, generated content, evidence, logs and caches remain under Git-ignored paths.

## Workflow and review

Check text and speaker roles, acknowledge transcript review, choose Qwen and either
predefined retrieval or bounded follow-ups, then generate. Stage/status and cancellation
are visible. Expand references to inspect original turns and surrounding dialogue;
clinical references also scroll to the transcript editor. Research has the full
transcript for review before generation and omission assessment.

Every run snapshots stable IDs/roles/timestamps/revisions and rechecks before saving.
Changes mark older versions stale on read; stale versions cannot be saved or approved.
Regeneration creates a fresh ID and preserves all prior versions, originals, edits and
audit snapshots. Saving edits is separate from explicit approval, which requires a
reviewer and semantic/source/omission acknowledgement. Existing local reviewer names
are self-reported, not authenticated identities. Decided clinical sessions are locked.

Overview chunks prefer complete turns; oversized turns are split into exact character
spans with original IDs. Coverage lists all spans. Actual native template rendering
and `/tokenize` count the full system+JSON+template before every call, reserving output
in the context budget. No silent truncation. Independent chunk overviews are cached
by transcript/model hashes, prompt/version and context; merged by provenance rather
than repeatedly rewriting one rolling summary. The full chunk list is retained.

Six versioned section searches always execute through the existing retrieval engine;
up to three overview hints supplement them. BM25 is note-generation default and E5 is
optional/local-only; application retrieval defaults and frozen thresholds are unchanged.
Windows use existing recording/meeting timeline boundaries, two neighbouring turns,
four ranked centers per search. Central/context/rank/query/provider are recorded.
Selected evidence is deduplicated by original source ID.

Follow-ups allow only structured search or expansion requests, maximum two rounds,
three requests per round, 300-character queries and five expansion IDs per request.
Duplicate queries, foreign IDs, extra operation/scope keys and malformed outputs stop
with a visible trace. Planning stops when hints exceed its exact budget instead of
silently omitting hints. No-new-evidence, completion and round-limit stops are logged.
Malformed JSON has at most one retry; invalid content is withheld. No hidden reasoning
is requested/stored. Transcript contents are explicitly untrusted data in system prompts.

The complete evidence packet is saved before a possible switch to Omi; Qwen's owned
process is killed/waited before loading Omi once. The ASR worker and note jobs share a
cross-process memory lock. E5 uses CPU/cached embeddings and the note provider releases
its encoder after retrieval. Existing unrelated retrieval activity is not forcibly
unloaded. Whole-application memory and simultaneous user retrieval remain unmeasured.

Five provisional sections are defined by `SECTIONS` in `services/note_generation/workflow.py`
for later clinician schema feedback. Evidence is drafted in exact-token bounded shards
per section, with every selected source/span recorded. Cross-shard semantic deduplication
and contradiction resolution remain human work. Empty evidence produces unestablished
limitations, not normal findings. Quotations are currently disallowed to avoid fabricated
verbatim claims. Source/schema failures withhold a result; raw generated responses and
saved packets permit investigation. Client-only items in Objective/Evaluation notes are
withheld and surfaced in limitations. This structural role check cannot prove semantic
support, including clinician assessment versus suggestion. Human review is mandatory.

## Real smoke and checks

Real fictional three-turn Qwen smoke (predefined mode): 123.41 s total, 2.63 s cold
server load (hashing additional), 5,265,452 KiB / 5.02 GiB peak **runtime-process** RSS,
2,709 evaluated input tokens and 512 generated tokens across six calls. VRAM unavailable;
whole-app peak RAM unmeasured. Owned process exit was verified. The real output had
valid source IDs but incorrectly placed client reports in Objective/Evaluation notes;
that observation motivated the client-only withholding check, now unit-tested. No human
quality score or clinician approval is claimed. CPU speed makes a full seven-conversation
A/B/C comparison expensive; selection and execution support are prepared, quality study
pending. See `data/note-generation-experiment/smoke/predefined/` for local artifacts.

Routine tests use mocked inference, not generated clinical notes. Backend: 93 relevant
API/speech/retrieval/generation tests passed before final documentation; frontend build
passed; Chrome mocked generation/edit/save/approval/source-exchange flow passed.

```bash
.venv/bin/python -m pytest tests/api tests/speech tests/note_generation tests/retrieval/test_engine.py -q
npm run build --prefix apps/web
(cd apps/web && npx playwright test --config playwright.notes.config.ts)
.venv/bin/python -m services.note_generation.experiment smoke
```

Smoke refuses to overwrite a successful cached result; choose `--output` for a deliberate
new measurement. TestClient/Chrome/local runtime require loopback access; this execution
sandbox required escalation, not an application networking workaround.

## Development comparison and human evaluation

```bash
.venv/bin/python -m services.note_generation.experiment prepare
.venv/bin/python -m services.note_generation.experiment run --case annomi-3 --mode full
.venv/bin/python -m services.note_generation.experiment run --case annomi-3 --mode predefined
.venv/bin/python -m services.note_generation.experiment run --case annomi-3 --mode followup
.venv/bin/python -m services.note_generation.experiment omi \
 --packet data/note-generation-experiment/annomi-3/followup/evidence.json
```

Repeat A/B/C for the seven selected development IDs: annomi-3, 19, 24, 40, 50, 97, 103.
`selection.json` records hashes and rationale; full transcript snapshots permit omission
review. No labels, thresholds, final packets or reference notes are used. Full baseline
checks all input with exact prompt/output budget and writes an ineligible failure if
oversized. Compare A/B/C on matched eligible cases; report failures and eligibility
separately. Do not call an oversized case a truncated full baseline. Omi takes the exact
frozen evidence packet, same fields and 700-token per-call output budget, with its native
template/tokenizer. Its citation/format contract must pass; failure is recorded, never
silently repaired or used to replace default Qwen.

Human CSV at `data/note-generation-experiment/human-review.csv` covers unsupported facts,
omissions, attribution, negation/time errors, citation support, format, corrections and
editing time. The UI JSON export includes full transcript, drafts, edits, evidence and
review history. Reviewers must read the full transcript, enter their own scores and
corrections, and retain review files locally. No gold notes, AI-generated human scores,
clinical validation, Urdu performance or ROUGE-based quality claims are supplied.

### Optional Omi result

The actual frozen-fictional-evidence Omi load was attempted on the same CPU runtime.
It failed **before generation**, with `error loading model vocabulary: unordered_map::at`.
The file's tokenizer metadata says `llama`/`default`; this does not establish a correct
Omi tokenizer conversion. No Omi note or semantic/format score exists. The process exited
and was reaped; Qwen remains usable. Remedy: obtain/re-export a verified Omi GGUF with
its correct tokenizer vocabulary and native chat template, record source/revision/hash,
then retry the frozen-packet command above with `NOTE_OMI_MODEL=/absolute/path.gguf`.
Do not rename this file or invent missing vocabulary metadata to bypass the failure.

Additional bounded settings: `NOTE_FOLLOWUP_ROUNDS=0..2`,
`NOTE_REQUESTS_PER_ROUND=1..3`. `NOTE_SECTIONS` optionally supplies a JSON list of
1–8 unique names (restart API after changing). Defaults retain the five provisional
sections; new section semantics need clinician feedback and corresponding policy review.

## Integrated patient notes and printing (4 November 2026)

The patient-session page now has one editable structured note. Qwen generation fills
Subjective, Objective, Assessment, Functioning/progress, and Interventions/plan using
an explicit section mapping. Empty generated sections stay blank. The old button
that copied every client utterance into Subjective has been removed from the UI.
Generated versions and citations remain available under **Generated version and source
 evidence**. Generation settings and source search are collapsed by default.
The experimental Omi file failed its earlier compatibility test; it is no longer
presented as a working writer in the UI. Its backend experiment remains available.

New patient generation applies the result server-side, including if the browser tab
closes. Existing content requires an explicit replacement confirmation. Prior saved
fields are retained in the `AI_DRAFT_APPLIED` audit event. An optimistic note token
and database update condition prevent changes made during generation from being
replaced. A conflicting generated version remains available for comparison. Generation
never approves a note; generated fields carry `AI_GENERATED` provenance and pending
review. Unsaved editor changes disable generation and printing. Saved versions reopen
without replacing the note again. Research-note generation remains separate.

Use **Print / Save as PDF** beside **Save draft** to open a clean SOAP-format preview
with a functioning/progress supplement. Its own print button opens the browser print
dialog; select **Save as PDF** as the destination. It prints the saved note, record and
save dates, patient/session identifiers, draft/decision status and existing reviewer
identity. It does not invent a signature or examination findings. Demo records and
stale-source notes are identified. Text is inserted as text, never interpreted as HTML.
This is a general progress-note layout, not a jurisdiction-specific form.

### AnnoMI demo patients

All 133 existing dataset conversations (9,699 turns) are copied into 133 clearly named
**AnnoMI demo** patient records, one session each. This grouping is for testing and
makes no claim that videos represent distinct people. Demographics remain blank and
recording consent is false. Original research IDs, splits, labels and files are unchanged;
clinical copies have a `demo-` ID prefix. The three conversations reserved in the
independent-final packet remain blocked from AI generation. No labels or research
benchmarks are run by this import.

```bash
.venv/bin/python -m services.api.import_annomi
```

The command loads the existing local `.env`, backs up SQLite using its backup API under
`data/backups/`, then imports missing sessions. Repeating it skips existing sessions,
preserving transcript and note edits. The directory supports **Show → AnnoMI demos**,
search and pages of 18 records. Start with demo 3 or 24 for short development examples.
Import time is record creation time, not the original conversation date.

### Current verification

`nvidia-smi` now detects the GTX 1650 with 4 GiB VRAM outside the execution sandbox.
The installed llama.cpp binary remains CPU-only and no `nvcc` compiler was found;
GPU offload has not been enabled or claimed. A new real CPU Qwen smoke completed in
131 seconds: Subjective and Plan were populated, Objective/Assessment/Progress stayed
empty, and proposal versus agreement remained distinct. This is a functional smoke,
not an independent quality evaluation. Artifacts: `data/note-generation-experiment/ui-fix-v2/`.

Browser checks cover generation into the actual composer, saving corrections,
persistence after reload, print/PDF text escaping, research review and mobile layout.

### CUDA installation and startup verification

The local `.env` now selects the official llama.cpp **b12079** CUDA 12.8 binary
(commit `71efc559a`) under `models/llama-cuda-b12079/llama-b12079/llama-server`.
`NOTE_LIBRARY_PATH` contains that directory and its sibling
`cudart-llama-b12079-bin-ubuntu-cuda-12.8-x64`. These libraries are added only to the
inference subprocess environment. Settings: `NOTE_GPU_LAYERS=99`, context 4096,
batch 256 and microbatch 128. Restart the application launcher after `.env` changes.

The initial GPU verification error was a logging-level mismatch: b12079's default
verbosity 3 omits the layer-offload summary. The app now explicitly requests verbosity
4 and checks the current model load's log, excluding earlier loads in the same file.
It still rejects a requested GPU run if no actual offloaded layers are confirmed.
The completed job displays its measured backend above the generated draft details.

Real verification: **37/37 layers offloaded**, approximately **3,056 MiB** sampled
inference-process VRAM, GPU utilization observed up to **98%**, and approximately
**29–30 generated tokens/second**. The first complete fictional smoke took 117.6 s,
including an 87-second first prompt; subsequent calls in that process processed prompts
at about 151–155 tokens/second. Logs/results are stored in
`data/note-generation-experiment/cuda-b12079/`. Functional verification does not establish
clinical note quality. CPU PyTorch/E5 settings are separate from llama.cpp note generation.

A second complete smoke in a fresh inference process, using a separate output/cache
directory, finished in 33.1 s with the same GPU offload.
The first-prompt processing time fell to 2.56 s.
This confirms the large initial delay did not recur in that repeat. Artifacts:
`data/note-generation-experiment/cuda-b12079-warm/`.

## Token-limit recovery (v4)

The v3 overview emitted the full draft schema, including empty basis/missing-information
fields, and stopped the whole job at its fixed 1,100-token output limit. The latest
three failed jobs confirm `stop_type=limit` at exactly 1,100 generated tokens. This
was an output reservation failure, with the runtime log confirming a 4096 context.
No applicable AGENTS.md was present in the repository or ancestor directories.

V4 uses `compact-overview-v4.3` hints: concise text/type/attribution/source IDs plus
status, timeframe and uncertainty when needed. Optional empty attributes are omitted.
The draft schema is unchanged. Negation, contradictions, attribution, proposal versus
agreement and original references remain required. Original transcripts remain the
evidence. This change invalidates old overview cache keys; existing files remain intact.

| Setting | Default | Rationale |
|---|---:|---|
| `NOTE_CONTEXT` | 4096 | Unchanged laptop context; runtime `/props` must agree |
| `NOTE_OVERVIEW_OUTPUT` | 800 | Compact first attempt, leaving input room |
| `NOTE_OVERVIEW_MAX_OUTPUT` | 1600 | Ceiling for one meaningful larger-output retry |
| `NOTE_SAFETY_MARGIN` | 64 | Reserved beyond input and output on every request |
| `NOTE_MAX_SPLIT_DEPTH` | 8 | Bounds recovery tree depth |
| `NOTE_MAX_GENERATION_CALLS` | 96 | Total model calls across all stages, including repairs |
| `NOTE_MIN_SPAN_CHARS` | 64 | Stops splitting into unusably small source fragments |
| `NOTE_OVERVIEW_MAX_TURNS` | 16 | Limits fact density per initial chunk, reducing omission pressure |

Settings are explicit reservations, never silently increased or clamped. Before each
request, `/apply-template` renders the native GGUF template and `/tokenize` counts that
exact prompt including system/schema instructions, JSON, revision, source metadata and
context. `input + output + margin <= effective context` is enforced. The JSON
grammar constrains item fields and source IDs to supplied originals; it adds no hidden prompt text. A one-slot owned server is
started with the configured context, verified against `/props`. Oversized metadata or
prior draft history fails safely rather than being truncated.

Complete turns are packed with output space reserved first, at most 16 primary turns per initial chunk by default. A first real-model review with 38 primary turns omitted brief opening measurements despite ending normally; the tighter initial turn bound and explicit clinical extraction instructions address this observed omission pressure. Overviews receive labelled
primary turns and neighbouring context spans (up to 256 characters from each adjacent
turn), retaining IDs, roles, timestamps, revisions and exact offsets. Context is never
counted as new coverage. Oversized primary turns use contiguous exact spans. Recovery
bisects complete turns first, then oversized individual spans; no characters disappear.
The persisted expected-source manifest, completed spans and unresolved ranges are
independent of generated prose; completion checks reject gaps and duplicate coverage.

`limit`, `length`, `max_tokens`, `stopped_limit`, and context truncation all withhold the
entire response even when it parses as valid JSON. If the full prompt leaves room,
recovery retries once with at least 256 more tokens (normally 1.5 times the allowance),
within the configured maximum. Otherwise it splits. A second limit also splits. JSON
or typed-schema repair has a separate single retry. Other failures, cancellation,
timeouts, minimum spans and work/depth exhaustion terminate without recursive retries.
There is no JSON continuation splicing. Section drafting reuses this recovery utility;
full-transcript experimental baselines continue to fail explicitly rather than split.

Overview consolidation is deterministic: only identical structured facts are merged,
unioning original IDs. Conflicting status, attribution, time or text stays distinct.
There is no unbounded consolidation prompt. Planning checks its entire hint payload
before calling; drafting checks complete hints and prior section text before packing.

Successful chunk results and split checkpoints are written atomically under the shared
`note-generation/cache-v4/`. Keys include transcript revision, primary/context spans,
actual payload, model hash, runtime version, prompt text/version, response schema, context, margin,
recovery settings and decoding settings. Completed parents are reused; split parents
resume through children and never reuse incomplete JSON. Saved v3 caches are retained,
but cannot satisfy the changed v4 prompt/schema. Successful work is not labelled a
complete overview or final note while any primary range is unresolved.

Diagnostics: `overview-recovery.json`, `drafting-recovery.json`, `request-metrics.json`,
and job metrics record ranges, counts, reasons, model/prompt hashes and versions.
Raw-response file logging is disabled; terminal content tracing is a separate explicit option. Existing saved logs are retained. Generated
checkpoint facts still contain sensitive information and use existing local storage.
The panel shows recovery progress, failure stage/range, saved-work status, and expandable
technical counts. Existing notes, human edits and note-application conflict checks are
unchanged.

To retry/resume after deploying this code, restart `./scripts/run.py --no-reload`, reopen
the same session, review its current transcript and select **Retry / resume saved work**.
Keep the same settings to reuse valid v4 checkpoints. A new job/version is created;
previous notes and versions remain. Changed transcripts get new revision keys and
cannot reuse stale results. First retry of a v3 failure requires new v4 overview work.
CLI validation without applying to a patient note:

```bash
.venv/bin/python -m scripts.verify_note_recovery --case annomi-97 \
  --output data/note-generation-verification/v4.3-97/job
```

The validator permits development conversations only, checks transcript revisions and
existing held-out/consent restrictions, uses the shared model memory lock, and never
applies its result to a saved note. Repeating the same failed output folder resumes
checkpoints; it refuses to overwrite a successful final result. `--legacy` reproduces
the v3 overview prompt and 1,100-token cap for comparison.

Runtime API reference checked: [llama.cpp b12079 server documentation](https://github.com/ggml-org/llama.cpp/blob/b12079/tools/server/README.md) (`/props`, `/apply-template`, `/tokenize`, `stop_type`, `truncated`).

Quality iteration: the first smaller-chunk prompt retained opening clinical facts but repeated quoted acknowledgements. V4.3 uses a shorter dedicated extraction prompt that explicitly requests paraphrases and excludes ordinary acknowledgements. Original sources and all trial artifacts were preserved.

The quality check also found a missed medication-change phrase interrupted by an acknowledgement. The final prompt explicitly joins interrupted phrases and retains needed versus completed medication actions. A narrow validation guard requires a medication mention in extracted facts when a primary turn explicitly mentions medication; one corrective schema retry is allowed, then failure is explicit. This guard is not a general semantic completeness test. Valid context-only hints are retained and deduplicated without adding primary coverage.

Final measurements, source-review findings, limitations and retry instructions: [token recovery verification](NOTE_TOKEN_RECOVERY_VERIFICATION.md).

## Live terminal tracing

Restart the launcher after changing `NOTE_LOG_LEVEL` in `.env`:

- `events` (default): timestamped job/stage progress, memory/model loading, exact request
  budgets, response token counts/finish reasons, validation, retrieval source IDs,
  cache hits/checkpoints, retries/splits, section assembly and final note application.
- `full`: also prints the complete formatted native model prompt and decoding/schema
  options before each call, the raw response when that call returns, retrieval queries,
  cached chunk results, consolidated overview, assembled sections and the final draft.
- `off`: suppresses this note-generation trace.

Full tracing is enabled in this workspace's local `.env` at the user's request. It
exposes transcript and draft content in the terminal, so switch back to `events` when
content debugging is finished. The trace adds no content log files. Existing application
artifacts and llama-server's `runtime.log` retain their established storage behaviour.

Messages are flushed immediately and tagged with job and stage. While a model request
is in progress a heartbeat prints every ten seconds. The existing inference API remains
non-streaming: full responses print when each request completes, rather than token by
token. Raw responses, including token-limited ones, are explicitly labelled; validation
and final draft assembly are logged separately. Cached chunks print as cache hits and
are not presented as fresh inference. Terminal escape sequences in source text are
neutralized, and a closed stdout pipe cannot fail note generation.

Run `./scripts/run.py --no-reload` in a terminal and generate from the app to follow the
whole flow there. No extra inference-server command is required.

## Grouped drafting update

The default now uses compact grouped drafting with section-specific recovery rather than seven unconditional drafting passes. See [implementation, benchmark and limitations](NOTE_DRAFTING_OPTIMIZATION.md). The existing note/editor schema and human review workflow are unchanged.

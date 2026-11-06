# Grouped local drafting

The default drafting strategy is now `auto` (workflow `local-notes-v5`, compact draft
contract `grouped-draft-v6`). Qwen, its native template/tokenizer, the configured 4096
context and the existing note/editor schema remain unchanged. No drivers or models
were installed. Generation calls remain serial under the existing shared memory lock.

## What changed

All seven sections are requested in one structured completion when the exact formatted
input plus the initial output reservation and safety margin fits. If it does not fit,
the workflow tries related groups: Subjective/Functioning, Objective/Assessment/Progress,
and Interventions/Plan. Groups can split to individual sections, then original turns
and exact source spans. Output-limit detection, one meaningful larger-output retry,
bounded schema repair, maximum split depth, job deadlines and the global generation
request budget remain enforced. Runtime/OOM/model failures never trigger endless splits.

Only complete EOS responses can contribute validated sections. If a complete response
has some invalid sections, independently validated sections are atomically checkpointed
and only the remaining sections are requested again. A token-limited response contributes
nothing, even if its JSON looks complete. This is section-object checkpoint reuse, not
splicing continuation text onto incomplete JSON. Per-section coverage ledgers keep all
primary spans distinct from neighbouring context.

Prompts contain unique original sources once, using aliases T0/T1/etc. No duplicate
extracted-hint statements or ever-growing already_documented text is sent to the drafter.
Alias identities, source timestamps/revisions and span coverage are stored separately;
all persisted note references are mapped back to original IDs. Identical replies from
different turns are not merged by text. Actual preceding turns and interrupted statement
parts are linked where available; nonadjacent selected turns are not falsely treated as
neighbours. A contextless acknowledgement cannot support an asserted fact by itself.

The model returns section status plus compact text/kind/refs/who items. Time, material
uncertainty and factual status are optional, with defaults expanded in code. Ordinary
rationale and verbose empty-section explanations are removed. AI formulation alone needs
an evidence basis and missing information and is always created with pending human review.
Standard empty messages are rendered in code from validated statuses. No selected evidence
produces insufficient_evidence, never a finding that the full transcript contains nothing.
All empty findings remain scoped to supplied evidence.

Prompts and type/source checks distinguish clinician advice/health-risk education as an
intervention, clinician recommendations as proposals, client intentions as tentative
plans, and explicit specific agreement from ambiguous acknowledgements. Advice is not an
objective observation. Functioning requires additional concrete functional impact rather
than a repeated Subjective narrative. Exact duplication checks retain attribution,
time and uncertainty, so identical words about different people remain separate.

Narrow source-aware validation checks reject an explicitly rejected characterization
being assigned as the client's belief and some unsupported blanket symptom denials.
These are conservative lexical checks, not general semantic entailment verification.
Broader accuracy, paraphrase deduplication, missing qualifications and who a fact concerns
still require review against the originals.

## Evidence selection correction

The inspected `demo-annomi-106` job used only turns 0–10 for drafting. It excluded the
resource discussion at turn 12 and tentative client next step at 19 even though overview
hints referenced them. A faster drafter cannot recover facts from unseen originals.
The live workflow now expands every validated overview source reference back to original
turns, including needed preceding reply context, with provenance tags. Generated hint
text remains navigation, not evidence. Selection expansion respects available timeline
boundaries and does not change retrieval thresholds, dataset splits, labels or approvals.

This selection change is tested independently. Timed baseline/optimized comparisons
below use an identical frozen evidence packet, so time gains are not attributed to
changing or dropping evidence.

## Settings and usage

| Setting | Default | Meaning |
|---|---:|---|
| NOTE_DRAFT_STRATEGY | auto | Grouped drafting with measured fallbacks |
| NOTE_DRAFT_OUTPUT | 1600 | Initial output reservation for all seven sections |
| NOTE_DRAFT_MAX_OUTPUT | 2304 | Output ceiling for a measured recovery retry |
| NOTE_CONTEXT | 4096 | Unchanged total context |
| NOTE_SAFETY_MARGIN | 64 | Reserved in every request |
| NOTE_MAX_SPLIT_DEPTH | 8 | Existing bounded fallback depth |
| NOTE_MAX_GENERATION_CALLS | 96 | Existing total generation call budget |

Smaller section groups reserve `min(initial, max(800, initial * section_count // 7 + 256))`
output tokens. Reservations never permit exceeding the actual runtime context. User
settings are validated and not silently overridden. `NOTE_DRAFT_STRATEGY=sections`
selects the retained legacy v4 drafter for diagnostics/comparison; the automatic
individual-section fallback uses the new compact contract.

Restart `./scripts/run.py --no-reload`, refresh the app, open a permitted development
session, review the transcript and generate a new draft version. Existing human edits,
accepted formulations, approvals, stale-source checks and application conflict checks
are preserved. Old notes/checkpoints are retained; changed draft contracts receive new
cache keys. Full terminal tracing remains controlled by NOTE_LOG_LEVEL.

## Reproducible benchmark

Run these **sequentially**, using new output directories on each deliberate rerun:

```bash
NOTE_LOG_LEVEL=events .venv/bin/python -m scripts.benchmark_note_drafting \
  --case annomi-3 --only baseline --output data/note-drafting-benchmark/dev3-baseline-new
NOTE_LOG_LEVEL=events .venv/bin/python -m scripts.benchmark_note_drafting \
  --case annomi-3 --only optimized --output data/note-drafting-benchmark/dev3-optimized-new
```

Each command measures one fresh inference process (filesystem caches uncontrolled), a
warm-model generation with a separate empty output-cache directory, then exact output
checkpoint reuse. The warm run still calls the model; the cached run must show zero
model calls. The model/runtime, evidence hash and context are recorded. Benchmarks refuse
test/blind data and successful-result overwrites and never apply results to patient notes.
No reference notes or human quality scores are used.

Runtime metrics separate GGUF manifest/hash preparation, model readiness, total load time,
per-call input processing, output generation, runtime-reported prompt reuse, token counts,
confirmed GPU layer offload, sampled inference-process VRAM and process peak RSS. Warm
memory peaks are cumulative for the same owned process, not per-run or whole-application
memory. No GPU or prompt-cache effectiveness is inferred solely from configuration flags.

## Actual comparison (5 November 2026)

No applicable AGENTS.md was present in the repository or its ancestor paths. Current
code, all ten saved request records, the complete runtime log, checkpoint records,
source transcript and generated draft were inspected before editing. The observed
`demo-annomi-106` job was **test split**, not development. At the user's direction it
was left read-only and the model comparison used development conversation `annomi-3`.
The benchmark refuses test/blind cases; no held-out generation or dataset changes occurred.

The observed 106 run took 200.42 s end to end; drafting was 136.23 s in seven calls.
The final three calls used 6,933 formatted input tokens, 197 output tokens and 47.46 s
of reported input/output inference time (41.12 s input, 5.33 s output). They reused
only 1,425 prompt tokens: caching was partial, not a reason to assume those passes free.
These existing measurements are diagnostic context, not a same-conversation comparison
with the development timings below.

For the controlled comparison, both implementations received the same 16 original
turns, no generated hints, and evidence hash
`23c056b76b05bd6b1ed925c216ba3ae0836f7e2bdb9eb579232fea22293297c3`.
Qwen GGUF hash was `3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597`;
llama.cpp b12079 confirmed **37/37 layers offloaded**, 4,096 context and approximately
3,056 MiB sampled inference-process VRAM. There were no concurrent generation calls.

The baseline is the retained v4 section drafter, not a weakened synthetic baseline.
The final optimized contract is `grouped-draft-v6`. All failed requests, repairs and
validation fallbacks within each measured run are included in its time/token/call totals.

| Drafting measure | Baseline fresh | Optimized fresh | Baseline warm | Optimized warm |
|---|---:|---:|---:|---:|
| Wall time, excluding model load | 173.97 s | 61.39 s | 133.36 s | 28.34 s |
| Model calls | 7 | 3 | 8 | 2 |
| Formatted input tokens | 14,792 | 4,694 | 16,976 | 3,219 |
| Generated tokens | 2,985 | 1,361 | 4,332 | 1,039 |
| Runtime-reported prompt tokens reused | 2,850 | 1,126 | 16,134 | 3,217 |
| Input processing | 87.60 s | 25.23 s | 7.37 s | 0.053 s |
| Output generation | 81.68 s | 35.38 s | 120.67 s | 27.40 s |
| Owned-process peak RSS | 3,028.9 MiB | 2,787.0 MiB | 3,408.4 MiB | 2,787.0 MiB |

Fresh means a fresh inference process and empty result cache, **not** a filesystem-cold
machine. Baseline total load was 7.56 s (5.24 s GGUF manifest/hash, 1.72 s server readiness,
remainder runtime preparation); optimized load was 7.70 s (5.41 s manifest/hash,
1.71 s readiness). Fresh load + drafting was 181.53 versus 69.09 s. Warm runs reused the
same model process but used separate empty result-cache directories; actual model calls
and output tokens prove they were fresh generations. Warm RSS is the cumulative peak of
that process, not incremental memory or whole-application RAM.

Fresh drafting improved **64.7%**, warm drafting **78.8%**, in this single case. Warm
prompt reuse was observed in native `cache_n`, not inferred from `cache_prompt=true`.
The model's warm output varied despite temperature 0/seed 42; it was not substituted
with an earlier output. Repeated timing and independent quality studies remain pending.

Exact result reuse was a separate third measurement: baseline **1.914 s / 0 calls**;
optimized **0.0167 s / 0 calls**. Those are checkpoint-read timings, not generation speeds.
The cached notes exactly matched their respective warm outputs. In the optimized fresh
run, six independently validated sections were retained from the first complete response;
Subjective alone needed a focused retry and one corrective retry. The warm run needed
one focused Subjective request. Both had one section fallback, no token-limit acceptance
and no omitted primary spans. Reuse of the six sections within the same job is reported
separately from a whole-output cache hit.

## Quality/source findings and remaining errors

All final references belonged to the original evidence, all aliases mapped back to
original IDs, and every section had exact coverage of all 16 primary source turns/spans.
Every request satisfied formatted input + output reservation + 64 <= 4,096, and formatted
input counts matched runtime `tokens_evaluated`. Both inference processes were reaped.
These checks establish structural provenance and budgeting, not semantic entailment.

The optimized output preserved the client's later clarification about the spouse instead
of repeating the clinician's rejected characterization. Objective remained empty;
Interventions recorded the clinician's offer of information. Functioning no longer
repeated Subjective history/weight/health reports. Plan recorded openness to information,
with source 13 (offer) included beside source 14 (short response), without inventing an
agreed lifestyle-change commitment. No AI formulation was produced on this evidence;
synthetic regressions verify that any generated formulation still needs basis/missing
information and remains pending explicit human review. Child/adult attribution and
recommendation/intention/agreement distinctions are covered by source-aware synthetic
regressions; the held-out 106 conversation was not regenerated to claim those errors
empirically solved there.

**Remaining semantic errors:** a Subjective sentence still says the client denies weight
gain while also noting a slight increase; the source supports the slight increase and
specific denial of bloating. The model also uses language about toxins/detoxification
more specific than the source's reported belief and adds some unsupported generalized
wording about people with demanding jobs in the warm output. These require human
correction. The narrow fidelity checks do not catch every contradiction or extrapolation.
No clinician accuracy score, exhaustive fact completeness or clinical validation is claimed.

Earlier grouped trials were retained locally. They revealed client-only clinician
references, lost later clarification, and over-broad symptom denials. One fast one-call
trial was rejected as insufficient quality. Another strict trial stopped and saved six
validated sections rather than publishing a partial draft. Those trial costs are not
included in the final-version comparison above and are not concealed as successful runs.
Only the final contract/version is used in the reported optimized measurements.

Final backend regressions: **157 passed**. Frontend `npm run build --prefix apps/web`
passed after empty-status rendering was updated; `git diff --check` passed. Tests cover
one-call/group/single-section routing, native budget enforcement and token-limit handling,
output growth followed by section repair, work/depth limits, oversized source spans,
source aliases, real preceding context, interrupted utterances, distinct subjects,
overview-original expansion, empty evidence, validated statuses, exact duplicate handling,
AI review, stale caches, cancellation/runtime failure, and resuming only failed sections.

Machine-readable verification and measurements: `data/note-drafting-benchmark/comparison.json`.
Raw per-call metrics, frozen input, output notes and coverage are under
`data/note-drafting-benchmark/dev3-baseline/` and `dev3-optimized-v6/`.
Previous trials remain under their versioned sibling directories. Generated artifacts
are local/Git-ignored. No existing notes, human edits, approvals or research labels were
rewritten, and no commit or push was made.

## Formulation-field validation repair

A later failed `demo-annomi-108` job ended normally (`eos`, 377 generated tokens with an
800-token allowance), then failed Assessment validation. The previous grammar made
`basis` and `missing` optional for all Assessment kinds while Python required both for
AI formulations. It also incorrectly required at least one missing-information entry.
Repeated fallback wrapping made the final error list the same source ranges many times.

The Assessment item schema now uses two separate `oneOf` branches: ordinary assessment
or unresolved items omit explanatory metadata; AI formulations require a nonempty basis
and a string-array `missing` field. The array may be empty, so the model does not have to
invent unresolved clinical information. Basis and source validation, pending human review,
token-limit rejection and work limits remain active. The schema follows the runtime's
[documented JSON-schema grammar support](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md#json-schemas),
using separate branches rather than unsupported if/then conditionals or mixing oneOf
with properties on the same object.

Compatible old completed chunks, validated section results and split checkpoints are
revalidated and copied to the new schema key, preserving the original cache entries.
A cache-only traversal of the actual failed job reused seven completed checkpoints and
stopped at the unfinished Assessment range with **zero model calls**. The failed
conversation was not regenerated and no patient note was applied. Incomplete responses
are never upgraded into completed work. Nested failures now keep one concise stage/range
message; full source ranges remain in diagnostics and the alias map.

To use the repair, restart the existing launcher, refresh the session, review the current
transcript and use **Retry / resume saved work**. No context or model change is required.
Only valid completed work can be reused; remaining Assessment/other sections still need
to complete under the normal validation and review rules.

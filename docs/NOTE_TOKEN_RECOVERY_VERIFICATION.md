# Local note token recovery verification

Verified 5 November 2026. Workflow `local-notes-v4`, overview prompt/schema
`compact-overview-v4.3`; existing note/editor schema unchanged.

1. **Confirmed cause.** The latest three saved failures (jobs `3f5f00a1…`,
   `e7a14721…`, `2dbdb923…`) failed during overview extraction at exactly 1,100
   generated tokens with native `stop_type=limit`. The latest failing call evaluated
   2,346 input tokens; this was an output cap, rather than exhaustion of the 4,096
   total context. The runtime log independently confirms 4,096 context/slot tokens.
   V3 used the verbose draft item schema for navigation hints and had no output-limit
   retry/split recovery. Its overview writes were not atomic. No applicable AGENTS.md
   was found in the repository or ancestor directories.

2. **Configuration.** `NOTE_CONTEXT=4096` remains unchanged. New defaults:
   `NOTE_OVERVIEW_OUTPUT=800`, `NOTE_OVERVIEW_MAX_OUTPUT=1600`,
   `NOTE_SAFETY_MARGIN=64`, `NOTE_OVERVIEW_MAX_TURNS=16`,
   `NOTE_MAX_SPLIT_DEPTH=8`, `NOTE_MAX_GENERATION_CALLS=96`,
   `NOTE_MIN_SPAN_CHARS=64`. See [configuration rationale](NOTE_GENERATION.md#token-limit-recovery-v4)
   and `.env.example`. Existing `.env` settings and model selections were preserved.

3. **Accounting/recovery.** Native `/apply-template` and `/tokenize` include policy,
   schema instructions, JSON, roles, revision, primary spans and labelled neighbour
   context. Before every call: formatted input + output reservation + margin <=
   runtime context. `/props` must agree with configured context. Decoding grammar
   constrains references to supplied originals. One meaningful larger-output retry
   is allowed if measured room exists; otherwise turns, then exact character spans,
   are bisected. Schema repair has a separate single corrective retry. Cancellation,
   non-token errors, minimum spans, depth and total-call limits terminate safely.
   Partial JSON is never accepted or spliced. Successful chunks and split manifests
   are atomic and keyed by revision, boundaries/context, model/runtime, prompt/schema,
   settings and decoding parameters. Coverage lives outside generated prose.
   Consolidation is exact structured deduplication with source union; conflicting
   facts survive and no consolidation model prompt can overflow. Drafting uses the
   same budget and recovery utility. Planning refuses an oversized complete payload.

4. **Actual model result.** Saved failures were all marked `test`, so these were not
   regenerated. Instead the original v3 overview prompt/cap was run on eligible
   development conversation `annomi-97`. It reproduced the same token-limit failure
   on call two (115.5 seconds). The final fixed workflow completed the same source
   with configured local Qwen3-4B Q4_K_M / llama.cpp b12079, 37/37 layers offloaded,
   and runtime-verified context 4,096. The verification CLI never applies to a saved
   patient note. Existing notes, edits and prior versions were preserved.

5. **Measurements.**

   | Run | Generation calls | Retries | Recovery splits | Wall time |
   |---|---:|---:|---:|---:|
   | Legacy development reproduction | 2 | 0 | 0 | 115.5 s (failed) |
   | Final fixed development run | 38: 7 overview + 31 drafting | 0 | 0 | 930.3 s |
   | Checkpoint resume | 0; 38 cache hits | 0 | 0 | 41.7 s including lock wait |

   Fixed workflow time excluding final teardown was 928.4 s; resumed workflow time
   was 23.6 s. The fixed run generated 10,624 tokens. Formatted inputs ranged from
   969 to 2,929 tokens. Every input count equalled runtime `tokens_evaluated`, and
   every full budget equation passed. This real run did not need adaptive retries;
   forced token-limit recovery paths are covered by deterministic mocked tests.
   Earlier prompt-quality trials were retained separately under the verification
   directory; the final-run measurements above exclude those interrupted/failed trials.

6. **Coverage and source review.** All 108 source turns have exact successful primary
   coverage, with no gaps or duplicate spans. Neighbour context does not add coverage.
   The final overview contains 29 hints; retrieval selected 83 original turns.
   Every overview and draft source ID belongs to the snapshot; every draft reference
   belongs to selected evidence. Source ID lists were checked for duplicates.
   All seven draft sections completed. The resumed overview and draft are identical
   to the initial completed output, with zero generation calls.

   Manual inspection against the original source confirmed retention of elevated
   blood pressure, medication needing change, time/work barriers, breathlessness,
   exercise/meal-planning barriers, convenience food, and the proposed 8:30 follow-up.
   The final Plan also retained the proposed follow-up in a couple of weeks, citing
   turn 86, even though the overview hints omitted that timeframe. Initial quality
   trials exposed missing opening clinical facts and quoted acknowledgements; these
   motivated the smaller initial chunks, dedicated compact prompt and a narrow guard
   against omitting an explicitly mentioned medication concept. This guard does not
   establish general factual completeness.

7. **Checks and limitations.** Backend: **129 passed** with mocked inference for
   deterministic failure paths. Frontend: `npm run build --prefix apps/web` passed.
   `git diff --check` passed. Tests cover exact budget boundaries/template overhead,
   native and compatibility finish reasons, valid-looking limited JSON, output growth,
   no-room splitting, second-limit splitting, work/depth/minimum exhaustion, span
   preservation, neighbouring context, overlap coverage, deterministic large-summary
   consolidation, checkpoint reuse, stale revision/model/prompt/settings rejection,
   cancellation, non-token errors, constrained IDs and corrective schema retry.

   Semantic limitations remain: some overview clinician reflections were classified
   as observations; there are overlapping paraphrases and an interrupted medication
   fact cites only part of its exchange. The final Objective was empty, but other
   attribution, interpretation, plan classification and omissions still require human
   review. Structural coverage and valid IDs do not prove semantic support or exhaustive
   fact extraction. This is one development case, not clinical validation. The laptop
   run took about 15.5 minutes. Very large prior draft context can still fail explicitly
   rather than truncate. No model/context increase or hosted fallback was introduced.

8. **Retry/resume.** Restart the existing launcher with `./scripts/run.py --no-reload`,
   refresh the same session, review its current transcript, keep the generation
   settings, and click **Retry / resume saved work**. The API creates a new version
   and reuses valid v4 completed chunks from `data/note-generation/cache-v4/`.
   Changed transcripts/configuration/models/prompts invalidate those keys. V3 cache
   files remain on disk; the changed prompt/schema requires new v4 extraction on the
   first retry. Saved notes and human edits retain the existing application conflict
   and preservation checks. Terminal failures show stage/range and saved-work status;
   technical counts are expandable separately.

   Reproduce development verification without applying to a patient note:

   ```bash
   .venv/bin/python -m scripts.verify_note_recovery --case annomi-97 \
     --output data/note-generation-verification/v4.3-97/job
   ```

   That folder now contains a successful result and is deliberately protected from
   overwrite. To repeat checkpoint reuse, choose a new sibling folder, for example
   `data/note-generation-verification/v4.3-97/resume-2`. To resume a failed CLI run,
   repeat its original command/output folder. The shared parent cache is reused.

Local artifacts: `data/note-generation-verification/legacy-97/job/`,
`data/note-generation-verification/v4.3-97/job/` (result, overview, snapshot,
request metrics, ledgers, runtime metrics and verification.json), and sibling `resume/`.
These generated artifacts remain local and Git-ignored. No commit or push was made.

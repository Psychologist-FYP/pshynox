# Progress monitoring: fast DSM pipeline (`dsm-fast`): implementation plan

**Status (10 November 2026):** Phases 0 and 1 done (see the progress log). Next: Phase 2.
**Branch:** `feat/progress-fast`, created from `feat/progress-dsm` at `04ee367` (Q1).
**Persistent rules:** the "Fast DSM pipeline" section of the root [`CLAUDE.md`](../CLAUDE.md), plus the progress-module
principles and the DSM-registry rules above it. Every session reads that section and this whole document first.

Why: the DSM pipeline (`progress-extract-dsm-v1` + `condition-predict-dsm-v1`) took **43 GPU-minutes** for a fictional
role-play of two short sessions (5.5 and 1.2 minutes). A 45–60 minute real session would take hours.

**Targets** (GTX 1650 4 GiB, Qwen3-4B-Instruct-2507 Q4_K_M, same model):

| Case | Target |
|---|---|
| 5-minute session, Stage A + Stage B | ≤ 4 min |
| 45-minute session, Stage A + Stage B | ≤ 15 min, scaling roughly linearly with length |
| Stage B after a new session | ≤ 30 s |
| Quality | the pre-registered criteria in §8, compared with `dsm-v1` |

**Core principle: the model reads; code decides.** The model answers short, closed questions about a fixed set of
transcript turns: which turns show this feature, and is it present, denied or not discussed? Criterion mapping, counts,
durations, labels, attribution checks, denial → `conflicting`, merging and questionnaire estimates are code.

---

## 1. Where the 43 minutes went (measured, Phase 0)

Source: `data/research/progress-runs/dsm-roleplay/` (Phase 4 run of the DSM plan). Stage A timings come from
`run-1-failed-stage-b.log`; Stage B timings from `run-2.log` (the completed Stage B; `run-3.log` replays it from the
checkpoint cache). Every number is from the runtime's own `model.request` / `model.response` events (llama-server
`timings`: `prompt_n`, `cache_n`, `prompt_ms`, `predicted_n`, `predicted_ms`).

| Stage | Calls | Wall time | Prefill time | Generation time | Input tokens | Reused from cache | Generated tokens | Gen. tokens/call |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A, session 1 (100 turns, 5.5 min) | 40 | 832 s | 201 s (24%) | 626 s (75%) | 138,929 | 115,071 (83%) | 12,985 | 325 |
| A, session 2 (13 turns, 1.2 min) | 9 | 375 s | 59 s (16%) | 312 s (83%) | 19,174 | 11,668 (61%) | 9,186 | 1,021 |
| **A total** | **49** | **1,207 s** | **260 s (22%)** | **938 s (78%)** | 158,103 | 126,739 (80%) | 22,171 | 452 |
| **B** | **45** | **1,392 s** | **974 s (70%)** | **393 s (28%)** | 151,053 | 31,341 (21%) | 12,204 | 271 |
| **All** | **94** | **2,599 s** | 1,234 s (47%) | 1,331 s (51%) | 309,156 | 158,080 (51%) | 34,375 | 366 |

Measured speeds: prefill **≈ 122 tokens/s** (179 tokens/s on the first 768 tokens of a prompt, falling with length),
generation **20.8 tokens/s in session 1** and **29–35 tokens/s in session 2 and Stage B**.

What this shows (and where it corrects the task's starting assumption):

1. **Stage A is generation-bound, not prefill-bound.** Prompt caching already works inside a feature group: 80% of
   Stage A input tokens were reused (`cache_prompt: true` and one slot are already set in `runtime.py`). The cost is
   output: about **230 generated tokens per feature**, even for `insufficient_evidence` (every feature returns all 13
   fields: `counter_check`, `basis`, `period_label`, …). Example: the elevated group in session 1 made **14 one-feature
   calls** (the full output reservation left room for one feature per call) and spent 198 s on a session with no
   elevated content.
2. **Stage B is prefill-bound.** Each of its 44 feature calls builds its own retrieval packet, so only the 728-token
   system prompt is reused, and every call re-reads ≈ 2,600 tokens (≈ 21.5 s). This is the one place the "re-reading"
   diagnosis is right.
3. **Session 1 used retrieval in every group** (the whole transcript did not fit with the output reservation), and each
   group's packet differs, so each group start re-read ≈ 2,800 tokens (≈ 24 s × 7 starts).
4. **The packet format is expensive.** Each turn is a JSON object
   (`{"id": "T12", "speaker_role": "CLIENT", "start_ms": …, "end_ms": …, "text": …}`). On session 1 that is 13,550
   characters, versus 5,417 for a compact `12 C: text` line per turn: **2.5× the tokens**, nearly all of it keys and
   timestamps the model does not need. On LongCounsel-8 sessions (40 longer turns) the overhead is 1.6×.
5. **Generation is ~35% slower in session 1 than in session 2.** The most likely cause is the grammar: every `refs`
   array is an enum of all supplied aliases (100 strings in session 1, 13 in session 2). Stage B (enum of ≈ 40 aliases)
   ran at 31 tokens/s. To be confirmed in Phase 1 by replaying one session-1 call with an integer-range grammar.
6. Runtime settings today: `-b 256 -ub 128`, flash attention `auto` (the log confirms it is enabled), KV cache f16
   (576 MiB at 4,096 context), peak VRAM 3.0 GiB of 4 GiB, `--cache-ram` host prompt cache 8 GiB (build default).
   The small micro-batch is a plausible cause of the slow prefill; Phase 1 measures it.

**Consequence for the order of work:** deterministic Stage B (1.1) removes 54% of the time on its own; compact output
(1.4) attacks most of what remains. Prefix caching (1.2) and runtime tuning (1.7) are worth less than the task
estimated for Stage A, but they matter for long sessions and are cheap.

---

## 2. Constraints this design respects

- **Benchmark-bound files are not edited.** `services/note_generation/{runtime,recovery}.py`,
  `services/retrieval/{engine,query}.py` and the v1 progress files are pinned by `tests/progress/dsm/benchmark_hashes.json`.
  The fast path *imports* `Runtime`, `Checker`, `aliases_for`, `EvidenceRetrievalProvider` and `rules.py` but changes
  none of them.
- **Server flags without editing `runtime.py`.** `Runtime.load()` passes `-c`, `-np 1`, `-ngl`, `-b`, `-ub` and
  inherits the process environment (`runtime_environment()`). This llama.cpp build (`b12079`) reads every other flag
  from `LLAMA_ARG_*` variables (`LLAMA_ARG_FLASH_ATTN`, `LLAMA_ARG_CACHE_TYPE_K/V`, `LLAMA_ARG_CACHE_REUSE`,
  `LLAMA_ARG_SPEC_*`, `LLAMA_ARG_SLOT_SAVE_PATH`, `LLAMA_ARG_THREADS`, checked with `--help`). A small
  `FastRuntime(Runtime)` subclass sets `self.context` and these variables only around `super().load()`, under the
  existing `memory_slot` lock. **`dsm-v1` keeps today's settings exactly**, so it stays reproducible.
- **One LLM runtime.** No second server or client; the fast path calls `Runtime.generate()`.
- **The DSM registry content is unchanged.** Short feature codes, one-line triage descriptions and the risk lexicon
  live in a new versioned config, `services/progress/dsm/fast/config.json`, keyed by the registry's feature keys. A
  test checks that it covers every feature exactly once.
- **The evaluation package never imports DSM code** (existing rule). The reference runner and comparison live in
  `services/progress/dsm/fast/`, like `dsm/fixture_run.py`.

New files (all under `services/progress/dsm/fast/`): `config.json`, `runtime.py` (FastRuntime + call log), `packet.py`,
`triage.py`, `prompts.py`, `schema.py`, `extract.py`, `merge.py`, `durations.py`, `stage_b.py`, `questionnaires.py`,
`reference.py` (CLI: select, run, status), `compare.py` (§8 report). Tests under `tests/progress/dsm/fast/`.

---

## 3. Instrumentation (Phase 1, used by every later phase)

`FastRuntime.generate()` calls `Runtime.generate()` and then appends one row to `<folder>/calls.jsonl`, built from the
metrics entry the runtime has just written. **Counts, timings and hashes only, never transcript or model text.**

| Field | Source |
|---|---|
| `run_id`, `pipeline` (`dsm-v1`/`dsm-fast`), `session_id`, `stage` (`A`, `B`, `B-duration`), `group`, `window`, `attempt` | caller context (a small `with log.context(...)` block) |
| `prompt_tokens` | formatted prompt, counted by `/tokenize` |
| `cache_tokens` | `timings.cache_n` (tokens reused from the KV cache) |
| `prefill_tokens`, `prefill_ms` | `timings.prompt_n`, `timings.prompt_ms` |
| `generated_tokens`, `generation_ms` | `timings.predicted_n`, `timings.predicted_ms` |
| `request_s`, `output_reserved`, `finish_reason` | runtime metrics |
| `server_config_sha256`, `model_sha256`, `prompt_sha256`, `schema_sha256` | settings and hashes |

Non-model phases (`model_load`, `triage`, `e5_load`, `stage_b_rules`, `persist`) are logged as `phase` rows with
seconds. `dsm-v1` reference runs use the same wrapper (an `InstrumentedRuntime` that only logs), so both pipelines are
measured identically. `reference.py report-speed` turns the logs into the per-session tables; speed claims come only
from these logs.

**Cache-reuse check.** For the second and later calls on the same session window, `cache_tokens` must be at least the
prefix length minus 8 tokens. Otherwise the call logs a `cache.reuse_low` event and the run summary shows it. Unit tests
use a fake runtime that reports `cache_n` and fail when reuse drops to 0 between calls of the same window.

---

## 4. Design (adapted to the real code)

### 4.1 Canonical prompt layout and prefix caching (task 1.2)

Every Stage A call for a session window is:

```text
system: [static block]  instructions + one line per feature (46) + two tiny worked examples        ≈ 1,200 tokens
user:   {"turns": ["0 T: How have you been sleeping?", "1 C: Badly, I wake at four …", …],
         "task": "Group: depressive. Answer for codes DM, AN, AP, SL, PM, FA, WG, SE, HO, CO."}
```

- The system block is byte-identical across all calls, sessions and patients, so after the first call of a server
  lifetime it is always cached (and the `--cache-ram` host cache keeps it across slot changes).
- `Runtime.prompt()` serialises the user message with `json.dumps`. `turns` comes first and is stable (integer turn ids
  = transcript position, fixed order, no timestamps, no per-call aliases), so the KV cache covers system + turns and
  only the `task` suffix (≈ 30–80 tokens) is processed per call.
- Turn lines are `<id> <C|T|U>: <text>` (client, therapist/clinician, unknown). Timestamps and segment ids stay in code,
  which maps ids back to segments for citations, playback and staleness.
- Integer ids map to the existing `T<n>` aliases, so the v1 `Checker` (foreign ids, attribution, other-person, copy and
  name guards) is reused unchanged.

**Windows.** If system + turns + suffix + output does not fit the context, the transcript is cut into consecutive
windows that fit, overlapping by 4 turns so a question and its answer are never split. Each window is its own cached
prefix (the system block stays cached across windows). Measured sizes, compact format: role-play session 1 ≈ 1,400
tokens; a LongCounsel-8 session ≈ 1,300; an estimated 45-minute session ≈ 9,000. So a 5–20 minute session is one window
even at 4,096 context, and a 45-minute session is 2 windows at 8,192 or 4–5 at 4,096.

**Deviation from the task (Q3): the whole transcript is always sent; triage gates, it does not shrink the packet.**
The task proposed a packet of only triage candidate turns. Compact rendering already makes whole transcripts cheap, and
the estimates in §6 meet the 45-minute target with every turn included. Sending every turn means:
- risk statements phrased without lexicon words ("I don't want to be here any more") still reach the model in the risk
  call (rule 2), with no dependence on retrieval recall;
- context around a candidate (a denial three turns later) is never cut away.
A triage-only packet stays implemented as a fallback (`packet: "candidates"` in the config), used only if a measured
45-minute run misses its target. When it is used, every risk-lexicon turn and ±1 context turn is always included.

**KV slot save/restore:** not planned. A session's KV state is ≈ 200–400 MB on disk, and unchanged sessions are never
reprocessed (§4.10), so there is nothing for it to speed up. Phase 1 measures `--cache-reuse` once and keeps it only if
it helps.

### 4.2 Retrieval-first triage (task 1.3): feature and group gating

- **Scores.** Every turn is scored against every feature with:
  - BM25 (existing `EvidenceRetrievalProvider`, method `bm25`, called through its public `search` with `k` = all turns)
    on the feature's registry `queries`;
  - E5 (`intfloat/multilingual-e5-small`, already cached under `models/retrieval`, CPU) cosine similarity between the
    turn and 2–4 short triage descriptions per feature in `config.json`. Description embeddings are computed once and
    cached on disk, keyed by the config hash and model revision.
  - Combined per feature as `max(bm25 / bm25_scale, e5 − e5_floor)` or RRF, whichever gives higher recall at equal
    gating on the dev set (§7.2).
- **Candidates** for a feature: turns at or above its threshold, plus ±1 context turn.
- **Denials.** The registry's `counter_queries` are generic ("no, not, never, fine, okay…") and match almost every turn,
  so they cannot gate. A denial is caught because it answers a clinician question about the feature (the question turn
  scores high, and ±1 context brings in the answer) or names the topic ("I'm not scared of them"). Each feature's triage
  descriptions include one denial form ("never had a period of feeling high"). Dev recall is measured on reference
  denials separately.
- **Gating.** A feature with no candidates is recorded as `insufficient_evidence` with the note "not discussed (no
  candidate turns found)", with no model call. A group whose features are all gated is skipped. The risk+plans group is
  **never gated**.
- **Thresholds** are chosen on development data only (§7.2), targeting **≥ 98% recall** of the dev reference's
  present/subthreshold/denial findings, and recorded in `config.json` with a version tag.
- **Memory.** E5 is loaded for triage and released before the LLM loads (`del` + `gc`), inside the same `memory_slot`.
  It runs on the CPU, so it does not compete for VRAM.

### 4.3 Compact output, simpler questions (task 1.4)

Output schema, grammar-constrained (JSON schema → GBNF by llama-server, as now):

```json
{"f":[{"k":"SL","s":"P","q":"ned","t":"c","c":["m"],"e":[12,15],"x":[],"a":[]}]}
```

| Key | Values | Maps to |
|---|---|---|
| `k` | the group's short codes only (enum) | feature key |
| `s` | `P` present, `S` subthreshold, `A` absent, `D` denied, `C` conflicting | status |
| `q` | `ned` nearly every day, `mdn` more days than not, `mod` most of the day, `occ` occasional, `-` unclear | frequency |
| `t` | `c` current, `p` past, `u` unclear | time frame |
| `c` | `m` low mood, `w` worry/anxiety, `s` social fear, `e` elevated period, `p` physical/substance, `o` other | contexts (needed for the context-gated links, e.g. GAD.C3 and SAD.D) |
| `e` | evidence turn ids, integer range `0..n−1`, 1–4 items | `evidence_segment_ids` |
| `x` | client denial turn ids, 0–3 | `counter_evidence` kind `denial` |
| `a` | alternative-explanation turn ids, 0–3 | `counter_evidence` kind `alternative` |
| `d` | optional duration phrase ≤ 6 words, only when stated | `onset_or_duration_text` |
| `p` | optional past-period label ≤ 6 words | `period_label` |
| `g`, `v` | only for features that have a degree or qualifiers in the registry | `degree`, `qualifier` |
| `b` | optional basis ≤ 12 words (Q4) | `basis` |

Adaptations to the task's schema, all to keep the existing statuses and rules working (rule 3): `A` absent is kept
(cited normal or clinician-observed absence is different from a client denial); `c` contexts are required for the
context-gated links; counter-evidence is split into denials `x` and alternatives `a`, because a client's "it's because of
the exams" is an alternative, not a denial, and only denials turn a criterion `conflicting`; `mod` keeps "most of the
day".

- **Rows are only listed for features with evidence.** Code records every unlisted, ungated feature of the group as
  `insufficient_evidence` ("not found in the turns"). Ids use an integer range in the grammar instead of a 100-string
  enum (finding 5).
- **Basis (Q4).** Principle 2 in `CLAUDE.md` requires a one-sentence paraphrased basis per finding, so the field cannot
  simply be dropped. Recommended: a ≤ 12-word `b` on listed rows only (≈ 15 tokens × ≈ 15 listed features ≈ 8 s per
  5-minute session), measured against a no-`b` ablation in Phase 3. The alternative is a code template ("Client report,
  turns 12 and 15; more days than not; current") plus an on-demand model paraphrase when a clinician opens "Why?".
- **Counter-evidence check.** `dsm-v1` withholds a `present` finding without `counter_check.searched`. In `dsm-fast` the
  same answer lists `x` and `a`, so `counter_check` is stored as `{searched: true, found: x + a, method:
  "single-pass-compact"}`.
- **Prompt style.** Short imperative rules; one line per feature (paraphrase + one inclusion/exclusion hint, e.g.
  "avoidance from tiredness is not social fear"); two tiny worked examples (one present, one denial); no reasoning about
  conditions, rules or durations; the existing untrusted-data sentence; "statements about another person are not about
  the client"; "a denial is D, never absent".
- **Guards kept in code:** attribution (a client turn for present/denial; a clinician turn for observations), the
  other-person guard, foreign-id rejection, context-gated links, past vs current, the copy and name guards on `b`, `d`
  and `p`, uncited absence → `insufficient_evidence`, `clinician_intensity` never accepted.

### 4.4 Fewer, larger calls (task 1.5)

- One call per **ungated** group per window: depressive (11), elevated (15), anxiety (9), social (6),
  impairment + exclusion (7), risk + plans. A typical depression session: 3–5 calls instead of 25–40.
- Output reservation: `120 + 45 × features in group` (a listed row is ≈ 25–40 tokens; unlisted rows cost nothing);
  risk + plans `200 + 40 × plans`.
- **Recovery.** A thin wrapper, not `Recovery.process` (which splits *sources* and so would break the cached prefix):
  checkpoint per (session revision, window, group, pipeline version, config hash, server-config hash, schema hash);
  one schema-repair retry; on an output limit, one retry with more output, then split the **feature list** in half (the
  prefix stays cached). Cancellation and the job timeout use `Runtime.check()`.
- **Single-call experiment (Phase 4):** all ungated features in one call per window, compared with per-group calls on the
  dev set.

### 4.5 Merging windows and duplicates

Per (session, feature), across windows, in code:
- `P` + `D` → `C` (a cited report and a cited denial); `S` + `D` → `C`; `C` + anything → `C`;
- `P` beats `S` beats `A` beats not-found; `D` beats `A`;
- evidence, denials and alternatives are unioned (ordered by time, capped as the contract allows); frequency is the
  highest cited level; `t` is `c` if any window says current;
- risk rows from all windows go through the existing `dedupe_risks`.

### 4.6 Stage B in code (task 1.1)

`stage_b.py` builds the rule engine's inputs (`periods` with `proposals`, see the `rules.py` docstring) directly from
the active findings of the patient's sessions up to `as_of`, then calls the unchanged `rules.evaluate_all`.

- **Proposal per feature (Q5).** Recommended policy: the most recent session with an assessed (non-gap) finding for the
  feature gives the status. Earlier sessions fill gaps only within an episode window (default 28 days before `as_of`
  when session dates exist; all sessions otherwise). A later denial or absence overrides an earlier `present`
  (improvement, noted). Evidence and counter-evidence are carried with their segment ids, and Stage A client denials are
  attached as counter-evidence, as `add_stage_a_denials` does now. Rejected and stale findings are never used; edited
  ones use the clinician's values.
- **Periods.** Current period: features with `t` current or unclear. Past periods: `t` past, grouped by normalised
  `period_label` (string similarity), otherwise one "Past (undated)" period. `hospitalised` is true for a period whose
  `feat.hospitalised_elevated` is present.
- **Durations** (`durations.py`): a deterministic parser over the period's findings' `onset_or_duration_text` (and
  `period_label`), English, Roman Urdu and Urdu: numbers (digits and words), "a couple"/"a few" (lower bounds), units
  (day, week, fortnight, month, semester, year; din, hafta/hafte, mahina/mahine, saal; دن، ہفتہ، مہینہ، سال), "since
  last <unit>", "past year", "for over …", "do hafte", "teen mahine se". It returns `{days, kind: exact|at_least|
  approximate, matched_text}` or `unresolved`. The period's duration uses the core features' findings (MDE: depressed
  mood and anhedonia; mania/hypomania: elevated or irritable mood and energy; GAD: excessive worry; SAD: fear of
  scrutiny), and the duration evidence is the cited turns of the finding whose text was parsed. Phrases relative to a
  date ("since summer") resolve only when the session date is known.
- **One fallback call per patient, only when it can change a label:** a condition's core element is met, its duration
  element is unknown, and at least one relevant finding has a duration phrase the parser cannot resolve. The call gets
  only those findings' phrases and their cited turns (not the transcript) and returns
  `{period_id, start_hint, approx_days, refs}`. An uncited answer is ignored.
- **Labels, checklists, specifiers, codes, "information still needed"** come from `rules.py` exactly as now. There is no
  model label to log; `postprocess.model_label` is null with `method: "rules-only"`.
- Target: 0 model calls usually, at most 1. The `dsm-v1` Stage B stays available for comparison.

### 4.7 Questionnaire estimates derived in code (task 1.6): reverses Q4 of the DSM plan

**This reverses your 9 November decision (DSM plan Q4: "PHQ-9 and GAD-7 AI estimates stay as their own separate
calls"). It needs your approval (Q2).**

- PHQ-8 items 1–8 and GAD-7 items 1–7 are derived from findings through `registry/dsm/instrument_map.json` (item →
  criterion items → linked features; when several features map to an item, the highest value is used).
- Mapping: `D` or `A` → 0; `occ` → 1; `mdn` or `mod` → 2; `ned` → 3; `S` → 1; present with frequency unclear → 1
  (lowest clearly supported); `C` or not found → null (a gap, never 0).
- **PHQ-9 item 9 is never derived** (unchanged).
- The derived values use the existing AI-estimate series, badges and review flow; their provenance names
  `dsm-fast-derived-v1` and the source finding ids.
- Validated in §8 against LongCounsel-8 PHQ-8 ground truth. LongCounsel-8 has no GAD-7 ground truth, so GAD-7 is only
  compared with the `dsm-v1` (v2-prompt) estimates and reported, not used as a criterion.
- Plan follow-ups move into the risk + plans call.

### 4.8 Runtime tuning (task 1.7), via `FastRuntime` only

Phase 1 runs `llama-bench` (in the same `b12079` build) and a real-call benchmark that replays the role-play's logged
prompt shapes. Sweep:
- `-b`/`-ub`: 256/128 (today), 512/512, 1024/512, 1024/1024 (prefill);
- flash attention on/off (today `auto` = on);
- KV cache f16 vs `q8_0`/`q8_0`;
- context 4,096 / 6,144 / 8,192 with the VRAM saved (f16 KV at 4,096 is 576 MiB; q8_0 at 8,192 ≈ 612 MiB);
- CPU threads for the server's host-side work (`LLAMA_ARG_THREADS`).

Each setting records prefill and generation tokens/s, peak VRAM, and whether replayed outputs are byte-identical to the
current settings. Results go into this document. The model stays fully on the GPU (37/37 layers). Settings that change
outputs are allowed in `dsm-fast` only, and are covered by the §8 comparison.

**Phase 1 results (measured 10 Nov 2026, `bench.py`; raw data in `data/research/progress-runs/dsm-fast-bench/`):**

`llama-bench`, Qwen3-4B Q4_K_M, all 37 layers on the GPU, 2 repetitions:

| Setting | Prefill (2,048 tokens) | Generation (128 tokens) |
|---|---:|---:|
| `-b 256 -ub 128` (today), flash attention on | 142.1 tok/s | — |
| every `-b` 256/512/1024 × `-ub` 128/256/512, and 1024/1024 | 140.5–143.9 tok/s | — |
| `-b 512 -ub 512`, KV f16 | 140.5 tok/s | 45.1 tok/s |
| `-b 512 -ub 512`, KV q8_0, at depth 0 / 3,000 / 7,000 tokens | 139.9 / **99.4 / 71.8** tok/s | 45.5 / **35.3 / 26.7** tok/s |
| CPU threads 4 vs 8 | — | 45.1 vs 45.0 tok/s |
| flash attention off, at depth 3,000 | out of memory (4 GiB) | — |

Real-call replay (the exact `dsm-v1` Stage A calls of role-play session 1):

| Setting | Call | Prompt | Reused | Prefill | Generated | Wall | Output identical |
|---|---|---:|---:|---|---|---:|---|
| today | depressive chunk 1 (cold) | 3,126 | 0 | 3,126 @ 130/s | 603 @ 30.7/s | 43.8 s | reference |
| today | depressive chunk 2 (same packet) | 3,179 | 2,797 | 382 @ 109/s | 621 @ 30.6/s | 23.9 s | reference |
| today | chunk 2, alias enum → pattern grammar | 3,179 | 3,178 | 1 | 627 @ 31.9/s | 19.8 s | reference |
| KV q8_0 | chunks 1 / 2 | same | same | same speed | 597 / 621 @ 31.8–32.1/s | 42.9 / 22.9 s | **no** (slightly different text) |
| KV q8_0, 8,192 context | two groups on one whole-transcript packet: depressive (cold), elevated | 6,593 / 6,708 | 743 / **6,264** | 5,850 @ 101/s / 444 @ 77/s | 710 / 548 @ 25.1–25.5/s | 86.2 / 27.3 s | — |

Conclusions:
- **Server flags do not help on this GPU.** Prefill is compute-bound at ≈ 142 tokens/s whatever the batch sizes
  (the GTX 1650 has no tensor cores); threads do nothing; q8_0 KV gains ≈ 3% and changes outputs. `TUNED` = today's
  server with flash attention pinned on. A q8_0 KV cache is kept only as the way to reach 8,192 context if needed.
- **Prompt depth is the real cost**: prefill and generation both slow by ≈ 25–30% at 3,000 tokens of depth and ≈ 40–50%
  at 7,000. So prompts must be short: compact turn lines and windows of ≈ 2,500 transcript tokens at 4,096 context,
  not one large 8,192 window.
- **Prefix caching across groups works on the existing prompts** (6,264 of 6,708 tokens reused; the second group's
  prefill took 6 s instead of ≈ 55 s). It saves ≈ 20–25 s per group start, about 2.5 minutes of the role-play's
  20-minute Stage A, but the longer whole-transcript prompt slows generation to ≈ 25 tokens/s. Alone, it is worth ≈ 5%.
- **The grammar is not the cause of the slow session-1 generation.** The same call that ran at 22.9 tokens/s in the
  9 November run ran at 30.7 tokens/s today with the same grammar and settings; a pattern instead of the 100-alias enum
  gains only ≈ 4%. The 9 November slowdown is unexplained (likely GPU clocks or another process). Integer ids are still
  used in `dsm-fast`, because they are shorter.
- **Where the time must come from:** Stage B in code (Phase 2) and many fewer generated tokens (Phase 3).

### 4.9 Speculative decoding (task 1.8, experiment)

This build supports draft-free n-gram speculation (`--spec-type ngram-simple|ngram-map-k|ngram-mod|ngram-cache`) and
draft models. Order: n-gram variants first (no VRAM); then `Qwen3-0.6B` as a draft **only if** its tokenizer matches the
main model exactly and VRAM fits (I will ask before downloading, Q9). At temperature 0 the outputs must stay
byte-identical on the replayed calls, and it is kept only if generation time drops by ≥ 15% with the grammar active.

### 4.10 Incremental and cached work (task 1.9)

- Stage A results are cached per (session id, transcript revision, pipeline version, config hash). Unchanged sessions
  are never reprocessed; re-analysis of an unchanged session is a cache hit.
- Editing a turn re-runs only the windows that contain it (with whole-transcript windows, that is usually one window),
  then the merge and Stage B (code) again.
- Live meetings (process completed chunks during the call): only after Phases 1–3 pass, as an optional phase, and only
  if it does not disturb the transcription worker queue.

### 4.11 Integration

- `PROGRESS_DSM_PIPELINE=dsm-v1|dsm-fast` (default `dsm-v1` until §8 passes), read by `jobs.py` next to
  `PROGRESS_REGISTRY`. The benchmark never reads it.
- `dsm-fast` findings are persisted by the existing `dsm_store` (same tables, `version = progress-extract-dsm-fast-v1`),
  so evidence links, staleness, review badges, audit, re-analysis and revert are unchanged. Stage B version
  `condition-predict-dsm-rules-v1`.
- UI: no change needed beyond showing the pipeline version and the "not discussed (no candidate turns found)" note.

---

## 5. Risk statements (rule 2)

- The risk + plans group runs on **every window of every session**, never gated, with every turn present (§4.1).
- The risk lexicon (English, Roman Urdu, Urdu script: suicide, self-harm, death wishes, harming others; seeded from the
  registry's `risk.json` queries and v1's `RISK_QUERY`) is in `config.json`. Lexicon hits are logged per session, and
  a test asserts that every lexicon turn was inside a risk-call window. If the candidate-packet fallback is ever used,
  lexicon turns and ±1 context are forced into it.
- Categories, supporting notes, de-duplication, "no statements flagged" (never "safe") and no numeric score are unchanged.
- §8: 100% of reference risk statements must be found.

---

## 6. Expected effect (estimates, to be replaced by measurements)

Role-play (both sessions + Stage B), baseline 2,599 s of model time:

| Change | Mechanism | Estimated effect |
|---|---|---|
| Stage B in code (1.1) | 45 calls → 0–1 | −1,390 s |
| Compact output, omitted rows, integer-id grammar (1.4) | ≈ 22,000 → ≈ 3,000 generated tokens in Stage A; ≈ 21 → ≈ 30 tokens/s in session 1 | −800 s |
| One packet per session, group calls (1.2, 1.5) | 49 → ≈ 8 Stage A calls; ≈ 31,000 → ≈ 4,000 prefilled tokens | −200 s |
| Compact turn lines (4.1) | session 1 ≈ 3,400 → ≈ 1,400 tokens | included above |
| Questionnaires in code (1.6) | 3 calls removed | included above |
| Runtime tuning (1.7) | prefill ≈ 122 → ? tokens/s | unknown until measured |

Estimated `dsm-fast` role-play total: model load 11 s + triage ≈ 10 s + session 1 ≈ 50 s + session 2 ≈ 25 s + Stage B
≈ 0 s ≈ **1.5–2 minutes** (target 4 min per 5-minute session).

45-minute session (≈ 6,000 words ≈ 9,000 compact tokens, 4,096 context, 4–5 windows): prefill ≈ 10,000 tokens ≈ 80 s
at today's speed; ≈ 20 group calls × ≈ 6 s ≈ 120 s; triage ≈ 15 s; model load 11 s: **≈ 4 minutes** (target 15).
Prefill and calls both grow linearly with windows.

---

## 7. Reference and evaluation data

### 7.1 Sets

| Set | Use | Content |
|---|---|---|
| Role-play fixture | labels identical; risk; speed | `tests/fixtures/roleplay_dsm` (2 sessions). `dsm-v1` results already exist (`data/research/progress-runs/dsm-roleplay/`). |
| **Dev** (thresholds and prompt iteration only) | §4.2 thresholds, Phase 3 prompt work | the 6 `val-rest-25` sessions of the v2 prompt pilot: `746_cbt_099` s2, `2596_integrative_008` s4, `866_pdt_049` s3, `385_cbt_135` s5, `3642_pdt_040` s3, `1402_het_004` s1 |
| **Reference** (§8 only, never used for tuning) | the acceptance comparison | 10 `val-rest-25` sessions, listed below |
| Synthetic long transcripts | speed only (15, 30, 45 min) | consecutive sessions of the reference trajectories joined into one transcript, research folder only, never the clinical DB |

**Reference set rule** (fixed now, before any run): `val-rest-25` minus the 6 pilot trajectories (19 trajectories, 95
sessions); bands by ground-truth PHQ-8 (low < 5, moderate 5–< 10, high ≥ 10), as in `prompt_pilot.select`; one
`random.Random(42)` shuffle per band in the order low, moderate, high; first 3 / 3 / 4 sessions from distinct
trajectories.

| Band | Trajectory | Session | GT PHQ-8 | Turns | Words |
|---|---|---:|---:|---:|---:|
| low | `3475_pmt_014` | 1 | 3 | 40 | 960 |
| low | `294_pmt_030` | 5 | 3 | 40 | 868 |
| low | `3399_het_043` | 1 | 4 | 40 | 904 |
| moderate | `1294_bt_003` | 4 | 5 | 40 | 874 |
| moderate | `4207_cbt_079` | 3 | 7 | 40 | 930 |
| moderate | `1537_cbt_020` | 2 | 6 | 40 | 906 |
| high | `936_cbt_003` | 5 | 24 | 40 | 889 |
| high | `1784_pdt_046` | 4 | 10 | 40 | 880 |
| high | `4931_het_044` | 2 | 15 | 40 | 923 |
| high | `4807_cbt_148` | 2 | 11 | 40 | 998 |

Each reference session is analysed as a one-session patient (Stage A, then Stage B as of that session). `test-final-37`
and `val-arms-12` are never used; `reference.py` refuses any run id outside `val-rest-25`. Ground-truth labels are read
only by `compare.py`, never by the pipelines, and nothing enters the clinical DB.

### 7.2 Threshold selection (dev only)

For each candidate configuration: gating recall = share of dev-reference present/subthreshold/denial findings whose
feature is *not* gated in that session; gated share = calls avoided. Choose the configuration with the most gating whose
recall is ≥ 98% overall and whose denial recall is ≥ 95%, ties broken toward lower thresholds. Record it in
`config.json` with the dev numbers. Thresholds are not changed after the reference comparison starts.

### 7.3 `dsm-v1` baseline runs (you run them)

> **Amendment (10 Nov 2026, your decision, before any results): no `dsm-v1` baseline runs.** The previous pipeline is
> taken as ≈ 45 min for the role-play (measured 43 min, §1). The commands below are kept for reference only and
> `reference.py` is not built.

`reference.py` (Phase 1) runs the current `dsm-v1` pipeline, unchanged, with today's server settings and the
instrumented wrapper. Each session writes a done marker; Stage A and B checkpoints use the existing `cache-v4` cache, so
an interrupted run resumes where it stopped.

Runtime estimate (each LongCounsel-8 session is about the size of role-play session 1: 851 s Stage A; Stage B ≈ 1,390 s):

| Run | Content | Estimate |
|---|---|---|
| `dev` | 6 sessions, Stage A only | ≈ 1.5 h |
| `reference` | 10 sessions, Stage A + Stage B | ≈ 6–6.5 h |

Planned commands (exact flags are fixed in Phase 1 and copied into the progress log):

```bash
tmux new -s dsm-ref
.venv/bin/python -m services.progress.dsm.fast.reference run --set dev       --pipeline dsm-v1 --out data/research/progress-runs/dsm-fast-ref
.venv/bin/python -m services.progress.dsm.fast.reference run --set reference --pipeline dsm-v1 --out data/research/progress-runs/dsm-fast-ref
# detach: Ctrl-b d · reattach: tmux attach -t dsm-ref · progress: ... reference status --out data/research/progress-runs/dsm-fast-ref
# after an interruption, run the same command again: finished sessions are skipped and checkpoints are reused
```

The `dev` run is needed before Phase 3 (thresholds); the `reference` run before Phase 5.

---

## 8. Acceptance criteria (pre-registered 10 November 2026; not changed after results)

> **Amendment (10 Nov 2026, your decision, before any results).** No comparison runs against `dsm-v1`. What is still
> measured: **speed (criterion 6)** from the call logs; **citations (5)** by tests; and, at no GPU cost, the risk rows
> and labels on the role-play fixture against the `dsm-v1` outputs that already exist (§1). Criteria 1–4 on the
> LongCounsel-8 reference set are not run; `dsm-fast` PHQ-8 MAE against ground truth is reported for information only.
> The default switches to `dsm-fast` when the speed targets are met, the role-play risk flags are all found and the
> tests pass.

All are measured with `compare.py` on the sets in §7.1, `dsm-fast` against `dsm-v1`, same sessions. **Every criterion
must pass** to switch the default to `dsm-fast`.

1. **Risk (non-negotiable).** 100% of the `dsm-v1` *flag* rows (not supporting notes) on the role-play fixture and the
   10 reference sessions are found by `dsm-fast`. A reference row is found when a `dsm-fast` flag row in the same
   session cites at least one of its segments, or a turn adjacent to one (±1), **and** shares a category family
   (suicidal ideation, self-harm, harm to others). Within-family differences (for example passive vs active) are listed
   for your review (Q6). Supporting notes are reported, not scored.
2. **Findings.** Over the 10 reference sessions + the role-play fixture, per (session, feature), with status classes
   *positive* = {present, subthreshold} and *negative* = {explicit_denial, absent}:
   - **recall ≥ 95%**: share of reference findings with status present, subthreshold or explicit_denial for which
     `dsm-fast` has a status in the same class, or `conflicting`;
   - **precision ≥ 90%**: share of `dsm-fast` findings with status present, subthreshold or explicit_denial whose
     reference status is in the same class, or `conflicting`.
   Every disagreement is listed with both statuses and cited turns for your manual review, because the reference is not
   ground truth. Exact status agreement is reported but is not a criterion.
3. **Labels.**
   - Role-play fixture: identical labels for every condition (as of session 2), deterministic Stage B on `dsm-fast`
     findings versus the `dsm-v1` run.
   - Reference sessions: the MDE label differs from `dsm-v1` on **at most 1 of 10** sessions. Other labels are reported.
   - Also reported (not a criterion): deterministic Stage B on the *cached* `dsm-v1` Stage A findings versus `dsm-v1`
     Stage B, which isolates the effect of 1.1.
4. **PHQ-8 from findings vs LongCounsel-8 ground truth** (10 reference sessions):
   - current-score MAE (`metrics.current_mae` definition: sum of rated items vs GT total) of `dsm-fast` derived items is
     **not worse than the `dsm-v1` (v2-prompt) estimate by more than 0.5 points**;
   - item coverage (rated or denied items / 80) is **not lower** than the `dsm-v1` estimate's.
   Item MAE on rated items and the over/under-rating pattern are reported.
5. **Citations.** 0 invalid ids in `dsm-fast` output (foreign, out-of-range or other-session); unit tests prove the
   attribution, other-person, foreign-id and context guards still reject bad rows.
6. **Speed** (from `calls.jsonl`, wall time from job start to stored results, including model load, triage and Stage B;
   cold server; median of 2 runs):
   - role-play session 1 (5.5 min) Stage A + Stage B ≤ 4 min, and each reference session ≤ 4 min;
   - synthetic 45-minute transcript ≤ 15 min, with 15/30/45-minute runs reported to show the scaling;
   - Stage B after a new session (role-play, as of session 2) ≤ 30 s.

Runs: `dsm-fast` is run on the reference set exactly once for §8, after thresholds and prompts are frozen (recorded
with their config hash). If a criterion fails, the result is reported as a failure; any fix is a new version that is
re-run and reported alongside, never instead.

---

## 9. Phases

| Phase | Work | GPU time (estimate) | Stop condition |
|---|---|---|---|
| 0 | This plan; profile of the existing run; `CLAUDE.md` rules | none | **Approval** |
| 1 | `FastRuntime` + call log + cache-reuse check; `llama-bench` sweep; real-call replay benchmark of the settings in §4.8; prefix caching on the **existing** `dsm-v1` prompts and schema (a measurement variant: one packet per session shared by all groups, the whole transcript when it fits, otherwise the union of the groups' retrieval turns) to measure caching alone; `reference.py` (select, run, status) so you can start the `dev` and `reference` baseline runs | ≈ 30–40 min (I'll confirm before starting) | tests + build pass; commit; log |
| 2 | `stage_b.py` + `durations.py` (English, Roman Urdu, Urdu tests); labels compared with `dsm-v1` on the cached role-play findings, no new model calls | none | same |
| 3 | `config.json`, triage, compact schema and prompts, group calls, merge, derived questionnaires, risk + plans call, `PROGRESS_DSM_PIPELINE`; unit tests for gating, schema mapping, guards, merge, risk coverage; thresholds on dev (needs the `dev` baseline); role-play runs | ≈ 20–30 min | same |
| 4 | Speculative decoding (n-gram, then draft if approved), context 6,144/8,192, single-call vs per-group; keep only what passes | ≈ 30–45 min | same |
| 5 | `compare.py`; one `dsm-fast` run on the reference set and the synthetic long transcripts; §8 report; docs (`PROGRESS_MONITORING.md`, `PROJECT_STATUS.md` addendum); switch the default only if every criterion passes; final report | ≈ 30–45 min (needs the `reference` baseline) | final report |
| 6 (optional) | 1.9 live processing during meetings; 1.10 whisper.cpp CUDA build measurement (real-time factor for 45 min; no ASR model change) | ≈ 20 min | report |

---

## 10. Measurement protocol

- Every model call is logged as in §3; every phase logs its non-model steps.
- Speed numbers in this document come from `reference.py report-speed`, which reads `calls.jsonl` only.
- Each technique is measured in isolation and combined, on the role-play fixture: (a) `dsm-v1` today (from the existing
  logs); (b) + runtime settings; (c) + one cached packet per session; (d) Stage B in code; (e) compact output and group
  calls; (f) + triage gating; (g) + speculative decoding. Each row reports calls, prefilled tokens, reused tokens,
  generated tokens, prefill and generation seconds.
- Cold vs warm: "cold" = first session after a fresh server start (the system block is not cached yet); both reported,
  criteria use cold.
- No GPU run longer than 10 minutes without telling you the expected runtime first. Long runs are yours, via tmux and
  resumable commands.

---

## 11. Open questions

**For you (before Phase 1 unless noted):**

- **Q1: Branch.** `feat/progress-fast` from `feat/progress-dsm` (`04ee367`). OK?
- **Q2: Questionnaires in code** (§4.7). This reverses your DSM-plan Q4 decision. Approve deriving AI PHQ-8 and GAD-7
  estimates from findings in `dsm-fast` (no separate calls; item 9 never derived), validated by §8.4? `dsm-v1` keeps
  the separate calls either way.
- **Q3: Whole transcript always** (§4.1). Recommended: every turn is sent (windowed), triage only gates features and
  groups; the candidate-only packet is a fallback used only if the 45-minute target is missed. OK?
- **Q4: Basis.** Recommended: a ≤ 12-word basis on listed rows (keeps principle 2; ≈ 8 s per 5-minute session), with a
  no-basis ablation reported. Alternative: a code template plus an on-demand "Why?" model paraphrase.
- **Q5: Cross-session policy for Stage B in code** (§4.6). Recommended: the latest assessed session wins; earlier
  sessions fill gaps within 28 days of `as_of`; a later denial overrides an earlier present. This is a clinical-policy
  choice for the adviser too.
- **Q6: Risk matching** (§8.1). Our categories are flat, so "more specific" is read as "same family (suicidal ideation,
  self-harm, harm to others)", with within-family differences listed for you. OK?
- **Q7: Baseline runs** (§7.3): `dev` (≈ 1.5 h) and `reference` (≈ 6–6.5 h) with `dsm-v1` at today's settings. OK to
  use the 6 pilot sessions as dev?
- **Q8: Synthetic long transcripts** (joined LongCounsel-8 sessions, research folder only) for the 45-minute speed
  target, since there is no real 45-minute transcript. OK?
- **Q9 (Phase 4):** may I download `Qwen3-0.6B` GGUF as a draft model if n-gram speculation does not reach the 15%
  gain and its tokenizer matches?

**For the clinical adviser (later):** the cross-session policy (Q5); the duration parser's readings of vague phrases
("a few weeks", "kaafi arsay se"); the derived PHQ-8 frequency mapping.

---

## 12. Progress log

### Phase 0: 10 November 2026
- **Done:**
  - read `CLAUDE.md`, the DSM plan (all of it, including the Phase 4 log), `PROGRESS_MONITORING.md` §0,
    `services/progress/dsm/` (extraction, prompts, prediction, rules API), `runtime.py`, `recovery.py`,
    `retrieval/engine.py` and `config.py`, `progress/evidence.py`, the v2 prompt pilot, the subsets and the benchmark
    hash pins;
  - profiled the Phase 4 role-play run from its logs (§1);
  - checked the llama.cpp build's flags and `LLAMA_ARG_*` variables, the current server settings in `runtime.log`, the
    GPU (GTX 1650, 4 GiB) and the local E5 model;
  - measured packet sizes (JSON rows vs compact lines) on the fixture and on LongCounsel-8;
  - fixed the reference-set rule and listed the 10 sessions (§7.1);
  - wrote this plan and the `CLAUDE.md` rules.
- **Tests:** none run; no code changed.
- **Deviations:**
  - Plan-mode tool not used, because it blocks writing this file (same as the earlier Phase 0s).
  - The profile does not support "every call spends ~20 s re-reading its prompt" for Stage A (80% of its input was
    already reused; 78% of its time is generation). It is true for Stage B. The phase order is unchanged, but the
    expected gains in §6 are re-estimated from the measurements.
  - Proposed: whole transcript always, triage for gating only (Q3); `A`, `c`, `x`/`a`, `mod` and an optional `b` added
    to the compact schema (§4.3); slot save/restore not planned (§4.1).
- **Next:** wait for approval and answers to Q1–Q8, then Phase 1.

### Phase 0 addendum: approval, 10 November 2026
- **Approved.** Answers:
  - Q1 yes (branch `feat/progress-fast`).
  - Q2 yes: PHQ-8 and GAD-7 AI estimates are derived from findings in `dsm-fast` (DSM-plan Q4 reversed for this path).
  - Q3 yes: whole transcript always (windowed); triage gates features and groups.
  - Q4–Q8: "no need to compare, just do the changes and report the time". So: the ≤ 12-word basis is implemented with
    no ablation; the cross-session policy (Q5) and risk matching (Q6) are as recommended; **no `dsm-v1` baseline runs**
    (Q7; the previous pipeline is taken as ≈ 45 min); synthetic long transcripts are used for speed only (Q8).
  - Q9 no: no draft-model download; speculative decoding is tried with n-gram modes only.
- §7.3 and §8 carry the resulting amendments (written before any results).

### Phase 1: 10 November 2026 (instrumentation, runtime tuning, prefix caching on the existing prompts)
- **Built:**
  - `services/progress/dsm/fast/runtime.py`: `ServerSettings` (context, batch, micro-batch, flash attention, KV types,
    threads, cache reuse, n-gram speculation; validated and hashed); `scoped_env` (sets `NOTE_*`/`LLAMA_ARG_*` only
    around `Runtime.load()` and restores them); `CallLog` (append-only `calls.jsonl`: prompt, reused, prefilled and
    generated tokens, prefill and generation ms, stage, group, window, settings/schema/prompt hashes; context fields are
    restricted to a text-free list; timed phases; the `cache.reuse_low` check); `Instrumented` mixin and `FastRuntime`.
    `settings=None` leaves today's server untouched.
  - `services/progress/dsm/fast/bench.py`: `sweep` (llama-bench) and `replay` (the exact `dsm-v1` Stage A calls of
    role-play session 1, replayed under each setting with output hashes).
- **Measured:** §4.8 "Phase 1 results". GPU time: sweep 12.5 min, replay 7 min.
- **Time result after Phase 1:** no end-to-end change. No server setting helps by more than ≈ 3%; prefix caching alone
  on the existing prompts is worth ≈ 5% of Stage A. The role-play still takes ≈ 43–45 min with the `dsm-v1` prompts.
- **Tests:** 426 backend tests passed (+15 in `tests/progress/dsm/fast/test_fast_runtime.py`: settings, scoped env,
  text-free rows, reuse detection and the loud failure when reuse drops to 0 within a window, failed calls logged,
  summaries). Frontend build passes. The benchmark hash pin test passes (no bound file changed).
- **Deviations:**
  - `reference.py` was not built (no `dsm-v1` baseline runs, your decision).
  - The grammar hypothesis from Phase 0 did not reproduce (see conclusions).
  - Context stays 4,096 by default; windows will be sized from the depth measurements.
  - Flash-attention-off could not be measured at depth (out of memory), and is not needed.
- **No new dependencies.**
- **Next:** Phase 2, Stage B in code with the duration parser (no GPU).


### Phase 2: 10 November 2026 (Stage B in code)
- **Built:**
  - `services/progress/dsm/fast/durations.py`: the deterministic duration parser (English, Roman Urdu, Urdu script
    and Urdu digits; exact, at-least and approximate readings; vague quantities as lower bounds; a month is 365.25/12
    days, so "six months" reaches the 183-day threshold; date-relative phrases only with a session date; anything else
    is unknown, never 0).
  - `services/progress/dsm/fast/stage_b.py` (`condition-predict-dsm-rules-v1`): findings → current and past periods
    (latest assessed session wins; earlier sessions fill gaps within 28 days when dates are known; past findings grouped
    by period label) → per-condition durations from each condition's core findings (an earlier finding's onset phrase,
    such as "around two months ago", dates the current episode) → the unchanged rule engine. One fallback model call
    only when a core is met, the duration is unknown and a phrase could not be parsed; an uncited answer is ignored.
  - `rules.py`, additive only: a period may carry `durations` per condition. `dsm-v1` periods never carry it, so its
    behaviour is unchanged (all `dsm-v1` tests pass unchanged).
- **Result on the cached role-play findings (no model calls):** **10 of 10 labels identical to `dsm-v1` Stage B**
  (MDE and MDD likely; manic, hypomanic, bipolar I and II, GAD, SAD, PDD and cyclothymic not met), in **0.011 s with 0
  model calls**, against 1,392 s and 45 calls for `dsm-v1` Stage B. The first attempt gave MDE "possible", because the
  cached `dsm-v1` Stage A had put session 1's low mood in a *past* period labelled "around two months ago"; the onset
  rule above fixed it.
- **Time result after Phase 2:** the role-play drops from ≈ 43–45 min to ≈ **20 min** (Stage A unchanged at ≈ 1,207 s;
  Stage B ≈ 0 s).
- **Tests:** 472 backend tests passed (+46: 30 parser cases; latest-wins and denial override; rejected/stale ignored;
  the episode window; past periods not combined; hospitalisation; per-condition durations; the onset rule; the fallback
  call only when it can change a label, cited only; the mocked role-play in code). Frontend build passes.
- **Next:** Phase 3, the fast Stage A.


### Phase 3: 10 November 2026 (fast Stage A)
- **Built** (`services/progress/dsm/fast/`): `config.json` (`dsm-fast-config-v1`: 46 features with two-letter codes
  and one-line definitions, five groups, output budgets, windows, risk lexicon in English, Roman Urdu and Urdu script,
  triage thresholds); `config.py` (validated against the registry; `PROGRESS_DSM_PIPELINE`, default `dsm-v1`);
  `triage.py` (BM25 group gating, risk-lexicon hits, candidate-turn pointers; E5 off: 0 of 40 groups were skippable by
  it); `prompts.py` (`progress-extract-dsm-fast-v1`: one static system prompt, per-call task with definitions and
  pointers; grammar requires every code; cite first, then a short paraphrase `b`, then the status); `extract.py` (windows,
  ≤ 5 codes per call, checkpointed calls with one repair and an output-limit split, risk + plans call first in every
  window, a risk recheck for uncited lexicon turns, the `dsm-v1` citation guards, merge, `attach_questions`, derived
  questionnaires); `questionnaires.py` (PHQ-8/GAD-7 from findings; PHQ-9 item 9 never derived); `run.py` (role-play,
  reference, dev and long runs with timings); `jobs.py` dispatch for `dsm-fast` (Stage A fast, Stage B in code).
- **Role-play iterations (real model), against the existing `dsm-v1` run:**

  | Attempt | Change | S1 Stage A | S2 Stage A | Labels same | Risk flags found |
  |---|---|---|---|---|---|
  | 1 | first version | 247 s | 70 s | 0/10 | 3/5 |
  | 2 | output budgets, fewer codes per call | 116 s | 45 s | 0/10 | 4/5 |
  | 3 | stricter "N" rule | 79 s | 31 s | 1/10 | 4/5 |
  | 4 | definitions in the task | 111 s | 32 s | 1/10 | 4/5 |
  | 5 | candidate-turn pointers | 127 s | 37 s | 1/10 | 4/5 |
  | 6 | paraphrase before status; denial rules | 318 s* | 73 s* | 5/10 | 4/5 → **5/5** with `attach_questions` |

  \*Attempt 6 ran on battery (power profile "quiet"): every call generated at ≈ 18 tokens/s instead of 34–39, and
  the longer rows hit the output budget (depressive group split into 10 calls). The budget is now 90 tokens per code.
  Its timing is not valid; the speed runs (Phase 5) must run on mains power.
- **Labels (attempt 6, as of session 2):** MDE, MDD and SAD as `dsm-v1` (likely, likely, not met). Manic, hypomanic,
  bipolar I and II and GAD are "insufficient evidence" where `dsm-v1` has "not met": `dsm-fast` leaves the elevated
  and worry features as "not discussed" where `dsm-v1` recorded denials or absence. Safer direction (a gap, never a
  false "not met"), listed for your review.
- **Risk:** all 5 `dsm-v1` flags found. The last one (`dsm-v1` cites the clinician's question at t85) is found because
  `attach_questions` now also cites the clinician risk question(s) right before a flagged client answer. Difference
  for review (Q6): `dsm-v1` adds harm-to-others at t83–84; `dsm-fast` has passive ideation only there.
- **Tests:** 510 backend tests passed (+38: config coverage, static prompt, schema, windows, checkpoint cache, row
  mapping and guards, merge, risk coverage and recheck, `attach_questions`, derived questionnaires, job dispatch).
  Frontend build passes.
- **Next:** Phase 4 (n-gram speculation, context size) and Phase 5 (timed runs), both on mains power.


### Phases 4 and 5: 10 November 2026 (server experiments, timed runs, default switch)
- **Fixes before the runs:** `bench.setting` now knows `tuned` and builds `ngram-*` spec types correctly (test added);
  `run.py --max-turn-tokens` (experiment only, recorded in `summary.json`). Triage never gates sessions under 30 turns
  (`min_turns_to_gate`): in a one-turn session BM25 scores are near zero, so a clearly depressive statement had its
  group skipped. Found by a new `dsm-fast` reanalysis test.
- **Phase 4 (role-play, mains power, cold server, `--fresh`):**

  | Setting | Wall (both sessions) | Generation | Outputs |
  |---|---|---|---|
  | `tuned` (4k, flash attention on) | 175 s | 35.3 tok/s | 5/10 labels, 5/5 risk |
  | `tuned+ngram-mod` | 193 s | 30.9 tok/s | identical |
  | `tuned+ngram-simple` | 195 s | 30.4 tok/s | identical |

  Synthetic 45-minute transcript: `tuned` 459 s Stage A (6 windows, 74 calls, 3,056 MiB peak) vs `tuned+ctx8192` with
  5,000-token windows 332 s (2 windows, 26 calls, 3,632 MiB). **Kept: `tuned` at 4k.** N-gram speculation is slower.
  8k is faster but its quality is not checked, and 4k already meets the target.
- **Phase 5 results (`tuned`; `calls.jsonl` in each run folder under `data/research/progress-runs/`):**

  | §8 criterion 6 (amended) | Target | Result | |
  |---|---|---|---|
  | Role-play session 1 (5.5 min), Stage A + B | ≤ 4 min | 138.5 s + 0.01 s = **2.3 min** | pass |
  | Each reference session (6.7 min) | ≤ 4 min | **1.4–2.1 min** (A 73–112 s, B 4–16 s) | pass |
  | Synthetic 45-minute transcript (46.6 min) | ≤ 15 min | **7.9 min** (A 459 s, B 12 s) | pass |
  | Stage B after a new session | ≤ 30 s | 0.01 s (role-play), ≤ 16 s (reference) | pass |

  Also: synthetic 20 and 33-minute transcripts take 4.8 and 6.0 min. The whole role-play (both sessions, model load)
  takes **3.1 min**, against ≈ 43 min for `dsm-v1` (§1).
  The speed runs were single runs, not the median of 2.
- **Risk (role-play):** 5/5 `dsm-v1` flags found (`dsm-fast-roleplay`, final code). For review (Q6): `dsm-v1` adds
  harm-to-others at t83–84; `dsm-fast` marks t87 active ideation + recent self-harm, where `dsm-v1` has the same.
  The reference sessions have no `dsm-v1` run, so they are not scored; `dsm-fast` flagged 0–2 statements per session.
- **For information (amended §8):** derived PHQ-8 on the 10 reference sessions has current-score MAE **6.4** and item
  coverage **15%** (12 of 80 items). The `dsm-v1` item prompt had 62% coverage in its 6-session pilot
  (`docs/PROGRESS_PROMPT_V2_PILOT.md`, a different set). MDE is "insufficient evidence" on 9 of 10, "possible" on 1.
  Derived items are much sparser, because a feature must be discussed explicitly to be rated. If AI questionnaire
  series matter, the v2 item calls could be added back to `dsm-fast` (about +1 to 1.5 min per session, still under the
  target): open question for you.
- **Default switched to `dsm-fast`** (the amended §8 conditions: speed targets met, all role-play risk flags found,
  tests pass). `PROGRESS_DSM_PIPELINE=dsm-v1` brings back the previous pipeline. The `dsm-v1` job tests now pin
  `dsm-v1`.
- **Not done:** the 6 dev sessions (only needed for tuning; the prompt is frozen); median-of-2 timing runs.
- **Tests:** 513 backend tests passed. Frontend build passes.

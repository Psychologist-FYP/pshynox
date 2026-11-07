# Stage A prompt v2 pilot (progress-extract-v2)

**Status:** criteria and pilot set written **before** the v2 pilot run (7 November 2026). Results are appended below.
Synthetic benchmark (LongCounsel-8), depression track only.

## 1. Subsets (locked first)

`val-arms-12`, `test-final-37` and `val-rest-25` were generated with seed 42 and committed (`1c04d55`) before any
prompt work. **All prompt tuning uses `val-rest-25` sessions only**, never `val-arms-12` or the test split.

**Disclosure:** the earlier v1 timing pilot used `1799_pdt_031` sessions 1–2, chosen before the subsets existed. That
trajectory is in `val-arms-12`. Its checkpoints were used only for the mechanism-level diagnosis below (causes and
counts), as instructed; no v2 wording was derived from its turns. Report arm results with this note, or as a
sensitivity check excluding `1799_pdt_031` sessions 1–2.

## 2. Diagnosis of v1 (existing pilot checkpoints, no new model calls)

The 32 item estimates are 2 sessions × {full, retrieval} × PHQ items 1–8, ground truth PHQ-8 = 4 in both sessions.
Each cached model response was mapped to its session and evidence mode through the pilot event log, and our
validation code was replayed on it.

| Session | Evidence | Item | GT | Model status | Withheld by validation | Basis |
|---|---|---|---:|---|---|---|
| s1 | full | 1 | 1.0 | insufficient_evidence | no | — |
| s1 | full | 2 | 1.0 | insufficient_evidence | no | — |
| s1 | full | 3 | 1.0 | insufficient_evidence | no | — |
| s1 | full | 4 | 1.0 | insufficient_evidence | no | — |
| s1 | full | 5 | 0.0 | insufficient_evidence | no | — |
| s1 | full | 6 | 0.0 | insufficient_evidence | no | — |
| s1 | full | 7 | 0.0 | insufficient_evidence | no | — |
| s1 | full | 8 | 0.0 | insufficient_evidence | no | — |
| s1 | retrieval | 1 | 1.0 | insufficient_evidence | no | — |
| s1 | retrieval | 2 | 1.0 | insufficient_evidence | no | — |
| s1 | retrieval | 3 | 1.0 | insufficient_evidence | no | — |
| s1 | retrieval | 4 | 1.0 | insufficient_evidence | no | — |
| s1 | retrieval | 5 | 0.0 | insufficient_evidence | no | — |
| s1 | retrieval | 6 | 0.0 | rated 2 | no | Client reported feeling guilty and small when Mom is angry, and described a persistent worry about disloyalty  |
| s1 | retrieval | 7 | 0.0 | rated 2 | no | Client described difficulty concentrating on what to say during interactions with Mom, and repeatedly mentione |
| s1 | retrieval | 8 | 0.0 | insufficient_evidence | no | — |
| s2 | full | 1 | 1.0 | insufficient_evidence | no | — |
| s2 | full | 2 | 1.0 | insufficient_evidence | no | — |
| s2 | full | 3 | 1.0 | insufficient_evidence | no | — |
| s2 | full | 4 | 1.0 | insufficient_evidence | no | — |
| s2 | full | 5 | 0.0 | insufficient_evidence | no | — |
| s2 | full | 6 | 0.0 | insufficient_evidence | no | — |
| s2 | full | 7 | 0.0 | insufficient_evidence | no | — |
| s2 | full | 8 | 0.0 | rated 1 | no | Client reported feeling a tight chest, holding breath, and a desire to vanish when their mom was upset, and de |
| s2 | retrieval | 1 | 1.0 | insufficient_evidence | no | — |
| s2 | retrieval | 2 | 1.0 | insufficient_evidence | no | — |
| s2 | retrieval | 3 | 1.0 | insufficient_evidence | no | — |
| s2 | retrieval | 4 | 1.0 | insufficient_evidence | no | — |
| s2 | retrieval | 5 | 0.0 | insufficient_evidence | no | — |
| s2 | retrieval | 6 | 0.0 | insufficient_evidence | no | — |
| s2 | retrieval | 7 | 0.0 | insufficient_evidence | no | — |
| s2 | retrieval | 8 | 0.0 | insufficient_evidence | no | — |

Summary:
- **29 of 32 abstained by the model; 0 withheld by our validation code** (the client-evidence rule and the
  other-person guard never fired on items).
- **Cause (a), the prompt requiring explicit symptom statements.** The model's own limitation notes say so ("no explicit
  statements…", "not quantified or structured to match PHQ-9 items", "inferred rather than directly reported").
- **Not (c), retrieval packets missing turns:** the full-transcript mode abstained just as much (15/16 vs 14/16).
- **Not (d), token or chunking limits:** 3 output-limit events in full mode were split by Recovery and completed with
  item outputs present; retrieval mode had none.
- The 3 rated items were all **over-rated** (GT 0, AI 1–2): single-event emotional reactions read as symptoms. A
  coverage fix alone could therefore inflate totals, which is what the low-session criterion guards against.

## 3. v2 changes (`services/progress/prompts.py`, `EXTRACT_V2`; v1 kept unchanged, `PROGRESS_EXTRACT_PROMPT=v1`)

- Indirect behavioural evidence counts (example: dragging oneself to class and sleeping through alarms → energy and
  sleep).
- Three cases kept apart: described → rated 1–3; denied or described as normal → `explicit_denial`, score 0; never
  touched → `insufficient_evidence`.
- Plain-language frequency anchors for 0–3 (no questionnaire wording), plus "a single event is not a two-week pattern;
  choose the lowest clearly supported level".
- New per-item field `evidence_strength` (`direct` | `indirect`).
- All safeguards kept: citations required, refs grammar-restricted to supplied aliases, the other-person guard, past vs
  current, no item 9. Validation: a denial needs a client turn and is stored as score 0; "rated 0" becomes a denial.
  Validation code was not wrongly withholding items (diagnosis above), so no relaxation was made.

## 4. Pilot set (chosen by ground-truth PHQ-8 before running; `prompt_pilot select`, seed 42, distinct trajectories)

| Band | Trajectory | Session | GT PHQ-8 |
|---|---|---:|---:|
| low | `746_cbt_099` | 2 | 3.0 |
| low | `2596_integrative_008` | 4 | 2.0 |
| moderate | `866_pdt_049` | 3 | 7.0 |
| moderate | `385_cbt_135` | 5 | 6.0 |
| high | `3642_pdt_040` | 3 | 12.0 |
| high | `1402_het_004` | 1 | 12.0 |

## 5. Acceptance criteria (pre-registered)

All must pass, on the six sessions above, with retrieval evidence:

1. **Coverage:** on the two high sessions pooled, at least 60% of items (≥ 10 of 16) are rated or denied, i.e. not
   `insufficient_evidence`.
2. **No over-rating:** on each low session, AI PHQ-8 total within ±4 of ground truth.
3. **Direction:** mean AI PHQ-8 total of the low sessions < moderate < high (strict).
4. **Validity:** 0 invalid citations (refs to unsupplied or other-session turns), and no increase in values withheld by
   validation over the v1 baseline. v1 pilot: 0 withheld across 4 extractions, so v2 must withhold 0.
5. **Cost:** mean Stage A retrieval time per session ≤ 1.25 × 175 s = 219 s. Full-transcript mode is not part of this
   pilot.

**Iteration limit:** at most two prompt revisions on these six sessions. If v2 still fails after the second revision,
stop and report what is failing.

## 6. Results

### Attempt 1 — progress-extract-v2 as first written (now `v2-attempt1`)


| Band | Session | GT PHQ-8 | AI PHQ-8 | Rated | Denied | Insufficient | Direct | Indirect | Withheld | Time |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| low | 746_cbt_099 s2 | 3.0 | 12 | 5 | 0 | 3 | 1 | 4 | 0 | 218 s |
| low | 2596_integrative_008 s4 | 2.0 | 4 | 2 | 0 | 6 | 0 | 2 | 0 | 237 s |
| moderate | 866_pdt_049 s3 | 7.0 | 16 | 7 | 0 | 1 | 1 | 6 | 0 | 272 s |
| moderate | 385_cbt_135 s5 | 6.0 | 3 | 1 | 0 | 7 | 0 | 1 | 0 | 176 s |
| high | 3642_pdt_040 s3 | 12.0 | 14 | 5 | 1 | 2 | 3 | 3 | 0 | 223 s |
| high | 1402_het_004 s1 | 12.0 | 2 | 1 | 0 | 7 | 0 | 1 | 0 | 209 s |

Item level (GT / AI, status):

| Session | item 1 | item 2 | item 3 | item 4 | item 5 | item 6 | item 7 | item 8 |
|---|---|---|---|---|---|---|---|---|
| low 746_cbt_099 s2 | 1.0 / – | 0.0 / 2 | 1.0 / – | 1.0 / 2 | 0.0 / – | 0.0 / 3 | 0.0 / 3 | 0.0 / 2 |
| low 2596_integrative_008 s4 | 0.0 / – | 0.0 / – | 1.0 / – | 1.0 / – | 0.0 / – | 0.0 / – | 0.0 / 2 | 0.0 / 2 |
| moderate 866_pdt_049 s3 | 1.0 / 2 | 1.0 / 2 | 2.0 / – | 2.0 / 3 | 1.0 / 2 | 0.0 / 2 | 0.0 / 3 | 0.0 / 2 |
| moderate 385_cbt_135 s5 | 1.0 / – | 1.0 / – | 2.0 / – | 2.0 / – | 0.0 / – | 0.0 / – | 0.0 / 3 | 0.0 / – |
| high 3642_pdt_040 s3 | 2.0 / 0 D | 2.0 / – | 2.0 / 3 | 2.0 / 3 | 1.0 / – | 1.0 / 3 | 1.0 / 3 | 1.0 / 2 |
| high 1402_het_004 s1 | 2.0 / – | 2.0 / – | 2.0 / – | 2.0 / – | 1.0 / – | 1.0 / – | 1.0 / – | 1.0 / 2 |

| Criterion | Value | Result |
|---|---|---|
| Coverage: high sessions pooled, rated or denied >= 60% of items | 44% | FAIL |
| No over-rating: each low session |AI - GT| <= 4 | 9.0, 2.0 | FAIL |
| Direction: mean AI total low < moderate < high | 8.0 < 9.5 < 8.0 | FAIL |
| Validity: 0 invalid citations and withheld values <= v1 baseline (0) | invalid 0, withheld 0 | PASS |
| Cost: mean Stage A retrieval s/session <= 1.25 x 175 s | 223 s | FAIL |

**Attempt 1 FAILS** coverage, over-rating, direction and cost; validity passes (0 invalid citations, 0 withheld).
Pattern: items 6–8 (self-worth, concentration, psychomotor) over-rated at 2–3 even in low sessions (GT 0); items 1–4,
where most ground-truth severity sits, mostly left insufficient. Distress is being rated, but under the wrong items.
Cost is 2% over the limit because v2 produces more output.

**Revision 1** (the first of two allowed): item-by-item evidence definitions from general clinical meaning, with
explicit exclusions (one interaction's guilt is not self-worth; being unsure what to say is not concentration; a tight
chest is not psychomotor change); an item is rated only from turns describing that area; frequency discipline (3 needs a
near-constant pattern, one or two occasions score 1); shorter basis fields (domains 240→160, items 200→140 characters)
for cost. Attempt 1 is kept verbatim as `PROGRESS_EXTRACT_PROMPT=v2-attempt1`.

### Attempt 2 — progress-extract-v2 revision 1 (final v2)


| Band | Session | GT PHQ-8 | AI PHQ-8 | Rated | Denied | Insufficient | Direct | Indirect | Withheld | Time |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| low | 746_cbt_099 s2 | 3.0 | 6 | 3 | 0 | 5 | 0 | 3 | 0 | 209 s |
| low | 2596_integrative_008 s4 | 2.0 | 2 | 1 | 0 | 7 | 0 | 1 | 0 | 226 s |
| moderate | 866_pdt_049 s3 | 7.0 | 9 | 4 | 0 | 4 | 1 | 3 | 0 | 212 s |
| moderate | 385_cbt_135 s5 | 6.0 | 7 | 3 | 0 | 5 | 0 | 3 | 0 | 185 s |
| high | 3642_pdt_040 s3 | 12.0 | 16 | 6 | 0 | 2 | 2 | 4 | 0 | 214 s |
| high | 1402_het_004 s1 | 12.0 | 8 | 4 | 0 | 4 | 0 | 4 | 0 | 183 s |

Item level (GT / AI, status):

| Session | item 1 | item 2 | item 3 | item 4 | item 5 | item 6 | item 7 | item 8 |
|---|---|---|---|---|---|---|---|---|
| low 746_cbt_099 s2 | 1.0 / – | 0.0 / – | 1.0 / – | 1.0 / – | 0.0 / – | 0.0 / 2 | 0.0 / 2 | 0.0 / 2 |
| low 2596_integrative_008 s4 | 0.0 / – | 0.0 / – | 1.0 / – | 1.0 / – | 0.0 / – | 0.0 / – | 0.0 / 2 | 0.0 / – |
| moderate 866_pdt_049 s3 | 1.0 / – | 1.0 / – | 2.0 / – | 2.0 / 2 | 1.0 / – | 0.0 / 2 | 0.0 / 3 | 0.0 / 2 |
| moderate 385_cbt_135 s5 | 1.0 / – | 1.0 / – | 2.0 / – | 2.0 / – | 0.0 / – | 0.0 / 2 | 0.0 / 3 | 0.0 / 2 |
| high 3642_pdt_040 s3 | 2.0 / – | 2.0 / 2 | 2.0 / 3 | 2.0 / 3 | 1.0 / – | 1.0 / 3 | 1.0 / 3 | 1.0 / 2 |
| high 1402_het_004 s1 | 2.0 / – | 2.0 / – | 2.0 / – | 2.0 / 2 | 1.0 / – | 1.0 / 2 | 1.0 / 2 | 1.0 / 2 |

| Criterion | Value | Result |
|---|---|---|
| Coverage: high sessions pooled, rated or denied >= 60% of items | 62% | PASS |
| No over-rating: each low session |AI - GT| <= 4 | 3.0, 0.0 | PASS |
| Direction: mean AI total low < moderate < high | 4.0 < 8.0 < 12.0 | PASS |
| Validity: 0 invalid citations and withheld values <= v1 baseline (0) | invalid 0, withheld 0 | PASS |
| Cost: mean Stage A retrieval s/session <= 1.25 x 175 s | 205 s | PASS |

**Attempt 2 PASSES all five pre-registered criteria. Iteration stopped here (1 of 2 revisions used).**

Item-level caveats (measured, not tuned away; watch `item_mae_rated` and coverage in the arms):
- Items 6–8 (self-worth, concentration, psychomotor) are still rated 2–3 in 10 of the 12 low- and moderate-session cells
  where ground truth is 0.
- Items 1 (interest), 3 (sleep) and 5 (appetite) remain mostly insufficient (5/6, 5/6 and 6/6 sessions), although
  ground truth is mostly 1–2.
- Session totals therefore agree partly through offsetting errors: under-coverage of items 1/3/5 against over-rating
  of 6–8. Total-score MAE may look better than item-level accuracy, so report both.
- The pilot covers 6 sessions; the arms (60 sessions, disjoint from these) are the real test.

**Consequence for the full-transcript arms:** the v2 prompt is about 1,400 characters longer than v1. v1 full-mode prompts
already reached 3,631 tokens; calibrated on those counts, a single-task v2 full-transcript chunk needs about 4,300
tokens on every `val-arms-12` session, so **full-transcript Stage A is ineligible on all 60 arm sessions at
NOTE_CONTEXT=4096** (estimate; the benchmark now logs a WARNING per session with ineligible groups). The `full-*` arms are
therefore not run with v2 at 4,096 context; see the runbook for the options.

v1 → v2 retrieval Stage A time: 175 s → 205 s per session (+17%).

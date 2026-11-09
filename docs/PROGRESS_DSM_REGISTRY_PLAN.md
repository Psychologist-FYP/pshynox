# Progress monitoring: DSM-5-TR criteria registry (registry v2): implementation plan

**Status (9 November 2026):** approved; see the progress log (§12).
**Branch:** `feat/progress-dsm`, created from `feat/internet-meetings` at `3920843` (that branch already contains all of
`progress-monitoring`).
**Persistent rules:** the "DSM registry (progress v2)" section of the root [`CLAUDE.md`](../CLAUDE.md), and the
progress-module principles above it. Every session reads that section and this whole document before starting a phase.

Why: a clinical adviser (counsellor and PFA trainer) advised that every tracked symptom and condition follow
**DSM-5-TR** as the reference dictionary. Today the module tracks 20 ad-hoc domains ("low mood", "worry", "social
fear"…) that are not tied to a diagnostic standard. Registry v2 makes every tracked item a specific DSM-5-TR criterion
item. Condition logic follows the DSM-5-TR criteria structure, and **code** (not the model) computes every label.

---

## 1. Benchmark and freeze status (rule 4)

| Check | Finding (9 Nov 2026) |
|---|---|
| `data/research/progress-frozen/` | **Does not exist.** No frozen config. |
| `data/research/progress-runs/final-test/` | **Does not exist.** No final half-test run. |
| Completed benchmark runs | `val12-v2-retrieval-current` and `val12-v2-retrieval-prior`, both 60/60 sessions, `state: completed`. Reports, freeze and calibration have not been run. |
| Running jobs | None (no `llama-server`, benchmark or vignette process). |
| Hash binding | The run configs record SHA-256 hashes of 14 files: `STAGE_A_FILES` and `STAGE_B_FILES` in `services/progress/evaluation/benchmark.py`. **All 14 still match the current files** (checked today). |

The 14 bound files: `services/progress/{prompts,extraction,validation,evidence,prediction,postprocess}.py`;
`services/progress/registry/{loader.py,domains.json,instruments.json,conditions.json}`;
`services/note_generation/{runtime,recovery}.py`; `services/retrieval/{engine,query}.py`.

What this means: `freeze` copies those hashes from a run's config, and `run_final` refuses to start unless today's files
match them exactly. If any of the 14 files changes, the two completed arms can no longer be frozen and run on the test
split without being rerun (about 6 GPU-hours each).

**Decision built into this plan:** none of the 14 files is edited, in any phase. The DSM pipeline lives in new files. It
may *import* v1 helpers (for example the `Checker` validation class and the `T<n>` alias builder), but it never modifies
them. A new test pins the 14 hashes to the values in the `val12` configs, so an accidental edit fails the suite. If a
phase ever seems to need a change to one of these files, I will stop and ask.

The benchmark, `run_final` and the vignette runner keep using v1. Nothing in `services/progress/evaluation/` imports the
DSM code, and a test checks that too.

**Copyright housekeeping (done in Phase 0):** `docs/DSM 5 TR-APA (2022).pdf` is a copyrighted book, and `docs/` is
pushed. It is now git-ignored (`docs/DSM*.pdf`) so it can never be committed. I read its text only to check criteria
*structure* (counts, durations, exclusions, specifiers, ICD-10-CM codes). The extracted text is in the session
scratchpad, outside the repository. No wording was copied into the repository.

---

## 2. Design overview

```text
Stage A (per session, progress-extract-dsm-v1)
  transcript ──► retrieval packets per disorder group (+ counter-evidence queries)
             ──► model: one FINDING per feature (status, frequency, timeframe, period, contexts, refs, counter-refs)
             ──► deterministic validation (v1 Checker reused) ──► dsm_findings rows (AI, pending review)

Stage B (per patient, as of a session, condition-predict-dsm-v1)
  all sessions' active findings + per-criterion retrieval ──► model proposes CRITERION statuses per period, with citations
             ──► denial check (cited client denial ⇒ conflicting) ──► RULE ENGINE (code) computes every label,
                 specifier, checklist line, differential and "information still needed"
             ──► condition_predictions rows (registry_kind = dsm). The model's own label is logged, never used.
```

Three registry concepts, kept apart on purpose:

- **Criterion item:** a DSM-5-TR criterion, id `<DISORDER>.<CRITERION>` (for example `MDE.A4`, `MANIA.B2`, `GAD.C6`,
  `SAD.A`). It has a ≤ 20-word paraphrase, the chapter, ICD codes and a verification status. These are what the grid
  shows and what the rules count.
- **Feature:** what Stage A extracts once per session (for example `feat.concentration`). Each feature links to every
  criterion item it can satisfy (`MDE.A8`, `PDD.B5`, `GAD.C3`, `ANXD.3`). A link can be **context-gated**: `GAD.C3`
  counts only if the finding is linked to worry or anxiety. Features whose meanings differ are never merged; the
  registry gives the reason for each separation (§3.10).
- **Rule:** a condition's criteria structure (counts, core items, same-period requirement, duration, impairment,
  exclusions, episode history, specifiers), in a small declarative JSON format evaluated by `rules.py`.

---

## 3. Registry v2 (`services/progress/registry/dsm/`)

Files (JSON, validated by Pydantic in a new `services/progress/dsm/registry.py`; the v1 `loader.py` is not touched):

| File | Content |
|---|---|
| `items.json` | Every criterion item: `id`, `disorder`, `criterion`, `paraphrase` (≤ 20 words), `chapter`, `model_assessed`, `notes` (inclusion/exclusion guidance for the prompt, also paraphrased), `verification` |
| `features.json` | Extraction units: `key`, `label`, `group` (depressive, elevated, anxiety, social, impairment, exclusion, context), `links` [{`item`, `requires_context`?}], `queries`, `counter_queries`, `separate_from` [{`feature`, `reason`}] |
| `conditions.json` | Disorders and episodes: `key`, `label`, `chapter`, `icd10cm`, `icd11`, `role` (`target` or `differential`), `rule`, `specifiers`, `differentials`, `verification` |
| `instrument_map.json` | PHQ-9 and GAD-7 items → criterion items (v1 `instruments.json` stays the scoring definition and is not edited) |
| `v1_mapping.json` | Each v1 domain → DSM item(s) or "no DSM equivalent", with notes (§6) |
| `risk.json` | Risk categories and supporting-note kinds (§4.3) |

`verification` on every item and every condition rule is `{status: pending|verified|changed, verified_by, verified_on,
comment}`. The JSON files ship with every entry `pending`. Clinician decisions are stored in the database, not by
editing the JSON (§5.4), and each decision is bound to the SHA-256 of the item's content. If a paraphrase later changes,
its old decision no longer applies and the item shows as pending again.

Each registry file carries `"source": "Paraphrased by the project team from the DSM-5-TR criteria structure; not APA
text"`. The UI shows **"Paraphrased from DSM-5-TR · pending clinical verification"** until an item is verified.

ICD-10-CM codes are taken from the DSM-5-TR coding notes. ICD-11 MMS codes are from my knowledge of the WHO
classification and are marked for verification.

### 3.1 Major depressive episode (`MDE`) and major depressive disorder (`MDD`)

Chapter: Depressive Disorders. The MDE criteria are also used inside the bipolar chapter. MDD codes: ICD-10-CM F32.x
(single episode) or F33.x (recurrent); ICD-11 6A70 (single episode) or 6A71 (recurrent).

| Id | Paraphrase (our words, ≤ 20) |
|---|---|
| `MDE.A` | Five or more A-items within the same two-week period, a change from usual; one must be A1 or A2. |
| `MDE.A1` | Low, sad, empty or hopeless mood most of the day, nearly every day, reported or observed. |
| `MDE.A2` | Much less interest or pleasure in all or almost all activities, most days. |
| `MDE.A3` | Clear weight loss without dieting, weight gain, or appetite clearly lower or higher nearly every day. |
| `MDE.A4` | Sleeping too little (insomnia) or too much (hypersomnia) nearly every day. |
| `MDE.A5` | Slowed or agitated movement nearly every day that others can see, not only felt. |
| `MDE.A6` | Tiredness or loss of energy nearly every day. |
| `MDE.A7` | Feeling worthless, or excessive or misplaced guilt, nearly every day; more than guilt about being unwell. |
| `MDE.A8` | Reduced ability to think, concentrate or make decisions nearly every day. |
| `MDE.A9` | Repeated thoughts of death or suicide, or a suicide plan or attempt. |
| `MDE.B` | The symptoms cause significant distress or impair social, work, study or other functioning. |
| `MDE.C` | The episode is not explained by a substance, medication or another medical condition. |
| `MDD.A` | At least one major depressive episode (MDE.A to MDE.C) has occurred. |
| `MDD.D` | The episodes are not better explained by a schizophrenia-spectrum or other psychotic disorder. |
| `MDD.E` | No manic or hypomanic episode ever, unless all were caused by substances or medical conditions. |

Prompt notes (paraphrased): A2 is not satisfied by a lack of opportunity. A7 is not satisfied by ordinary self-criticism.
A5 needs an observable change. Symptoms clearly due to a medical condition do not count.

**`MDE.A9` is not model-assessed.** It counts as present only when a clinician has **accepted or edited** a
`suicidal_ideation_*` or `self_harm_*` risk statement in the period, or entered PHQ-9 item 9 ≥ 1 for it. Otherwise it is
"not assessed by the model (see risk statements)", which counts as unknown, never as absent. This keeps principle 7:
risk content is surfaced, not scored.

**Specifiers (suggested by code only where rules allow):**

| Id | Paraphrase |
|---|---|
| `MDD.SEV` | Severity: mild, moderate or severe, from symptom count beyond the threshold, intensity and impairment. |
| `ANXD.1` | Feeling keyed up or tense. |
| `ANXD.2` | Feeling unusually restless. |
| `ANXD.3` | Hard to concentrate because of worrying. |
| `ANXD.4` | Fearing that something terrible may happen. |
| `ANXD.5` | Feeling one might lose control of oneself. |

- **"With anxious distress"** is suggested when ≥ 2 `ANXD` items are present on most days of the episode. Its level:
  2 items mild, 3 moderate, 4–5 moderate-severe, 4–5 plus motor agitation (`MDE.A5`, agitated) severe.
- **Severity (project rule, needs adviser verification).** DSM-5-TR describes severity qualitatively, not by count. The
  code suggests:
  - **mild**: 5–6 items and impairment not marked;
  - **moderate**: 7 items, or impairment cited as marked;
  - **severe**: 8–9 items, or 7 items with marked impairment.
  - It is shown as "suggested", and only when the episode label is `likely`.

### 3.2 Manic episode (`MANIA`) and hypomanic episode (`HYPO`)

Chapter: Bipolar and Related Disorders.

| Id | Paraphrase |
|---|---|
| `MANIA.A` | Distinct period of unusually high, expansive or irritable mood and raised energy, most days, ≥ 1 week or hospitalised. |
| `MANIA.B` | During that period, three or more B-items (four if mood is only irritable), clearly changed from usual. |
| `MANIA.B1` | Inflated self-esteem or grandiose ideas about oneself. |
| `MANIA.B2` | Needing much less sleep than usual while still feeling rested. |
| `MANIA.B3` | Talking more than usual or feeling driven to keep talking. |
| `MANIA.B4` | Ideas jumping rapidly between topics, or thoughts experienced as racing. |
| `MANIA.B5` | Attention pulled too easily by unimportant or irrelevant things, reported or observed. |
| `MANIA.B6` | More goal-directed activity (social, work, study or sexual), or aimless restless agitation. |
| `MANIA.B7` | Excessive risky activity likely to cause harm, such as spending sprees or reckless investments. |
| `MANIA.C` | Marked social or work impairment, hospitalisation to prevent harm, or psychotic features. |
| `MANIA.D` | Not explained by a substance, medication or medical condition; full antidepressant-emergent episodes still count. |
| `HYPO.A` | Distinct period of unusually high, expansive or irritable mood and raised energy, most days, ≥ 4 consecutive days. |
| `HYPO.B` | Three or more B-items (four if only irritable) persisting during the period, clearly changed from usual. |
| `HYPO.B1`–`HYPO.B7` | Same symptoms as `MANIA.B1`–`B7` (registry `same_as`). They share one finding, because the meaning is identical. |
| `HYPO.C` | An unmistakable change in functioning, unlike the person when well. |
| `HYPO.D` | Other people can notice the mood change and the change in functioning. |
| `HYPO.E` | Not severe enough for marked impairment or hospitalisation; psychotic features would make it manic. |
| `HYPO.F` | Not explained by a substance, medication or medical condition. |

**Episode logic (code):**

- A *period* is a cited stretch of time: the current one, or a past one with an approximate date such as "around
  March 2024" or "two years ago".
- The A-mood, the B-items and the duration must belong to the same period.
- **Irritable only:** if the period's mood is only irritable (no elevated or expansive finding), `B` needs 4 items.
- **Manic** needs A (≥ 7 days, or hospitalised at any duration), B, C and D not contradicted.
- **Hypomanic** needs A (≥ 4 consecutive days), B, C, D and E. If marked impairment, hospitalisation or psychotic
  features are cited for the period, the period is classed as manic, never hypomanic.
- Note that B-items without a mood period never count. For example, decreased need for sleep alone is not hypomania.

### 3.3 Bipolar I (`BP1`), bipolar II (`BP2`), cyclothymic disorder (`CYC`, differential only)

| Id | Paraphrase |
|---|---|
| `BP1.A` | At least one manic episode, current or past. |
| `BP1.B` | Not better explained by schizoaffective or another psychotic disorder. |
| `BP2.A` | At least one hypomanic and one major depressive episode, current or past. |
| `BP2.B` | There has never been a manic episode. |
| `BP2.C` | Not better explained by schizoaffective or another psychotic disorder. |
| `BP2.D` | Depression, or frequent switching between depression and hypomania, causes significant distress or impairment. |
| `CYC.A` | For at least two years, many periods of hypomanic and depressive symptoms below episode thresholds. |
| `CYC.B` | Those symptoms present at least half the time, never absent for more than two months. |
| `CYC.C` | Full depressive, manic or hypomanic episode criteria have never been met. |
| `CYC.D` | Not better explained by a psychotic disorder. |
| `CYC.E` | Not explained by a substance, medication or medical condition. |
| `CYC.F` | The symptoms cause significant distress or impairment. |

Codes:
- `BP1`: ICD-10-CM F31.x, depending on the current or most recent episode, severity and remission, from the DSM-5-TR
  coding table. ICD-11 6A60.
- `BP2`: F31.81; ICD-11 6A61.
- `CYC`: F34.0; ICD-11 6A62.

Rules:
- Bipolar evaluation is episode-based over **all** of the patient's sessions. Past episodes count when they are cited
  with a duration.
- Cyclothymic disorder is a *differential*. It is shown as "information needed" (two-year course, gaps) unless cited
  evidence exists for `CYC.A` and `CYC.B`.

### 3.4 Persistent depressive disorder (`PDD`, differential only)

F34.1; ICD-11 6A72.

| Id | Paraphrase |
|---|---|
| `PDD.A` | Depressed mood most of the day, more days than not, for at least two years. |
| `PDD.B` | While depressed, two or more of B1–B6. |
| `PDD.B1` | Poor appetite or overeating. |
| `PDD.B2` | Sleeping too little or too much. |
| `PDD.B3` | Low energy or fatigue. |
| `PDD.B4` | Low self-esteem. |
| `PDD.B5` | Poor concentration or difficulty making decisions. |
| `PDD.B6` | Feeling hopeless. |
| `PDD.C` | Never free of these symptoms for more than two months during the two years. |
| `PDD.E` | There has never been a manic or hypomanic episode. |
| `PDD.F` | Not better explained by a psychotic disorder. |
| `PDD.G` | Not explained by a substance, medication or medical condition. |
| `PDD.H` | The symptoms cause significant distress or impairment. |

(DSM criterion D is a note that major depressive criteria may be present throughout. It is recorded as a rule note, not
an item.) PDD is tracked only as "information needed" (two-year duration) unless `PDD.A` is cited with a duration of two
years or more.

### 3.5 Generalized anxiety disorder (`GAD`)

Anxiety Disorders. F41.1; ICD-11 6B00.

| Id | Paraphrase |
|---|---|
| `GAD.A` | Excessive anxiety and worry about several events or activities, more days than not, for at least six months. |
| `GAD.B` | The person finds the worry hard to control. |
| `GAD.C` | Three or more C-items linked to the anxiety, some present more days than not for six months. |
| `GAD.C1` | Restless, keyed up or on edge. |
| `GAD.C2` | Tiring easily. |
| `GAD.C3` | Trouble concentrating, or mind going blank. |
| `GAD.C4` | Irritability. |
| `GAD.C5` | Muscle tension. |
| `GAD.C6` | Trouble falling or staying asleep, or restless, unsatisfying sleep. |
| `GAD.D` | Anxiety, worry or physical symptoms cause significant distress or impaired functioning. |
| `GAD.E` | Not explained by a substance, medication or medical condition such as an overactive thyroid. |
| `GAD.F` | Not better explained by another disorder's focus of worry, such as judgement fears in social anxiety. |

Duration: `GAD.A` needs a cited onset or duration of six months or more. Otherwise the label is at most `possible`, and
"duration ≥ 6 months" goes into "information still needed".

### 3.6 Social anxiety disorder (`SAD`)

Anxiety Disorders. F40.10; ICD-11 6B04.

| Id | Paraphrase |
|---|---|
| `SAD.A` | Marked fear or anxiety in social situations where others might scrutinise the person. |
| `SAD.B` | Fears acting, or showing anxiety, in ways others will judge as embarrassing, humiliating or rejectable. |
| `SAD.C` | Those social situations almost always trigger fear or anxiety. |
| `SAD.D` | The situations are avoided, or endured with intense fear or anxiety. |
| `SAD.E` | The fear is out of proportion to the actual threat and the sociocultural context. |
| `SAD.F` | Fear, anxiety or avoidance persists, typically six months or more. |
| `SAD.G` | Causes significant distress or impaired social, work or other functioning. |
| `SAD.H` | Not explained by a substance, medication or medical condition. |
| `SAD.I` | Not better explained by another mental disorder, such as panic disorder or autism. |
| `SAD.J` | If a medical condition is present, the fear is unrelated to it or clearly excessive. |
| `SAD.PERF` | Specifier: fear limited to speaking or performing in public. |

**`SAD.A` requires cited fear, anxiety or embarrassment about scrutiny.**
- Avoidance alone, or avoidance the client explains by low energy or low mood, satisfies `SAD.D` at most, never `SAD.A`.
  This is enforced in two places: the item notes in the prompt, and code. A `feat.social_avoidance` finding never links
  to `SAD.A`, and `SAD.D` counts only when the finding is linked to fear.
- A cited client denial of fear (for example "it's not that I'm scared of them…") makes `SAD.A`
  `explicit_denial`. The SAD label is then `not_met`.

### 3.7 Impairment and functioning

DSM-5-TR criteria refer to clinically significant distress or impairment in social, occupational or other important
areas. These become three **impairment features**, linked to each condition's impairment criterion: `MDE.B`, `MANIA.C`
(only when the impairment is marked), `HYPO.C`/`HYPO.E`, `BP2.D`, `CYC.F`, `PDD.H`, `GAD.D` and `SAD.G`.

| Feature | Covers |
|---|---|
| `imp.social` | Relationships, family, friends, social participation |
| `imp.occupational` | Work, study, academic performance, attendance |
| `imp.other` | Self-care, household, other important roles; also clinically significant distress without a functional loss |

Each impairment finding carries `contexts` (§3.9). `GAD.D` counts only for impairment linked to worry or anxiety,
`SAD.G` only for impairment linked to social fear, and `MDE.B` only for impairment linked to low mood or depressive
symptoms. Impairment is **not** a symptom domain any more.

**WHODAS 2.0 (12-item).** It is in DSM-5-TR Section III. I could not confirm its usage terms: the WHO requires a
permission request, and the book states no free-use terms for it. It is therefore an **extension point**:
`instrument_extensions` in `instrument_map.json`, with `licence_confirmed: false`, not loaded. See Q10.

### 3.8 Instruments

v1 `instruments.json` (PHQ-9, GAD-7 scoring, bands, reliable change) stays as it is. `instrument_map.json` maps the
items. **The PHQ-9 order is not the DSM order:**

| PHQ-9 | → | GAD-7 | → |
|---|---|---|---|
| 1 interest/pleasure | `MDE.A2` | 1 nervous, anxious, on edge | `GAD.A`, `GAD.C1` |
| 2 down/depressed/hopeless | `MDE.A1` | 2 cannot stop or control worrying | `GAD.B` |
| 3 sleep | `MDE.A4` | 3 worrying too much about different things | `GAD.A` |
| 4 tired/little energy | `MDE.A6` | 4 trouble relaxing | `GAD.C1`, `GAD.C5` |
| 5 appetite | `MDE.A3` | 5 so restless it is hard to sit still | `GAD.C1` |
| 6 feeling bad about yourself | `MDE.A7` | 6 easily annoyed or irritable | `GAD.C4` |
| 7 concentration | `MDE.A8` | 7 afraid something awful might happen | `ANXD.4` (no GAD criterion; closest is the apprehension in `GAD.A`) |
| 8 slowed or fidgety | `MDE.A5` | | |
| 9 thoughts of death or self-harm | `MDE.A9`: **clinician-entered only** | | |

**AI questionnaire estimates in DSM mode** (Q4): the DSM pipeline makes no separate PHQ/GAD scoring calls. The
AI-estimate series is *derived in code* from the mapped findings:
- **present**: by frequency. "nearly every day" → 3, "more days than not" → 2, "occasional" → 1, "most of the day"
  with no day count → 2.
- **subthreshold** → 1.
- **absent** or **explicit denial** → 0.
- **insufficient evidence** or **conflicting** → gap.
- PHQ item 9 is never derived.

Administered scores are unchanged. Extension points for the **DSM-5-TR Level 1 Cross-Cutting Symptom Measure** and a
**mania self-rating scale** are listed as disabled `instrument_extensions` until their terms are confirmed (Q10).

### 3.9 Findings (per session, per feature)

| Field | Values |
|---|---|
| `status` | `present`, `subthreshold`, `absent`, `explicit_denial`, `insufficient_evidence`, `conflicting` |
| `frequency` | `nearly every day`, `more days than not`, `most of the day`, `occasional`, `unclear` |
| `onset_or_duration_text` | short paraphrase, for example "about three weeks", "since last semester" |
| `time_frame` | `current_episode`, `past_episode`, `unclear`; `period_label` for past periods (approximate dates) |
| `contexts` | subset of `low_mood`, `worry_anxiety`, `social_fear`, `elevated_period`, `physical_or_substance`, `other`; used for context-gated links |
| `evidence` / `counter_evidence` | segment ids (aliases mapped back), with `who` (client report, clinician observation) |
| `counter_check` | required for `present`: `{searched: true, found: refs or [], alternative_explanation: text or ""}` |
| `basis` | one-sentence paraphrase (≤ 160 characters), no quotations over 15 words, no names |
| `clinician_intensity` | optional 0–3, **clinician-entered only**; the schema rejects AI values |

Status semantics:
- `absent`: cited evidence that the feature is not present, such as a clinician observation or a clear contextual
  statement.
- `explicit_denial`: the client denies the feature.
- `subthreshold`: present, but below the frequency or degree the item needs. It never counts toward a threshold, but it
  feeds the cyclothymic and PDD "information needed" lists.
- `conflicting`: cited evidence both ways.
- In Stage B, a cited client denial forces `conflicting` on any criterion the model marks met (§4.2).

### 3.10 Shared and separated features (documented in `features.json`)

| Feature | Links to | Kept separate from, and why |
|---|---|---|
| `feat.depressed_mood` | `MDE.A1`, `PDD.A` | `feat.hopelessness`: PDD lists hopelessness as its own B-item |
| `feat.anhedonia` | `MDE.A2` | |
| `feat.appetite_weight` | `MDE.A3`, `PDD.B1` | |
| `feat.sleep_insomnia_hypersomnia` | `MDE.A4`, `PDD.B2` | `feat.reduced_sleep_need`: little sleep *without* tiredness is a mania feature. `feat.sleep_worry`: GAD counts disturbed sleep tied to anxiety, including unrefreshing sleep |
| `feat.sleep_worry` | `GAD.C6` (needs `worry_anxiety`) | as above |
| `feat.reduced_sleep_need` | `MANIA.B2`, `HYPO.B2` | as above |
| `feat.psychomotor_observable` | `MDE.A5`; agitation also feeds the severe anxious-distress level | `feat.restlessness`: MDE.A5 must be observable; GAD and anxious distress use felt restlessness |
| `feat.fatigue` | `MDE.A6`, `PDD.B3`, `GAD.C2` (needs `worry_anxiety`) | |
| `feat.worthlessness_guilt` | `MDE.A7` | `feat.low_self_esteem`: PDD's low self-esteem is milder and does not include guilt |
| `feat.low_self_esteem` | `PDD.B4` | |
| `feat.hopelessness` | `PDD.B6` | |
| `feat.concentration` | `MDE.A8`, `PDD.B5`, `GAD.C3` and `ANXD.3` (both need `worry_anxiety`) | `feat.distractibility`: mania distractibility is attention pulled by irrelevant things during an elevated period |
| `feat.elevated_mood` | `MANIA.A`, `HYPO.A` (mood part) | |
| `feat.irritable_period` | `MANIA.A`, `HYPO.A` (irritable part; sets "only irritable") | `feat.irritability`: GAD irritability is an ongoing anxiety symptom, not a distinct mood period |
| `feat.increased_energy` | `MANIA.A`, `HYPO.A` (energy part) | |
| `feat.grandiosity` … `feat.risky_activity` (7) | `MANIA.B1`–`B7`, `HYPO.B1`–`B7` | |
| `feat.hospitalised_elevated` | `MANIA.A` (any duration), `MANIA.C` | |
| `feat.psychotic_features` | `MANIA.C`, `HYPO.E` (makes the period manic), psychotic exclusions | |
| `feat.observed_by_others` | `HYPO.D` | |
| `feat.functioning_change_elevated` | `HYPO.C` | |
| `feat.worry_excessive` | `GAD.A` | |
| `feat.worry_uncontrollable` | `GAD.B` | |
| `feat.keyed_up_tense` | `GAD.C1`, `ANXD.1` | |
| `feat.restlessness` | `GAD.C1`, `ANXD.2` | |
| `feat.muscle_tension` | `GAD.C5` | |
| `feat.irritability` | `GAD.C4` (needs `worry_anxiety`) | |
| `feat.fear_awful` | `ANXD.4` | |
| `feat.fear_losing_control` | `ANXD.5` | |
| `feat.social_fear_scrutiny` | `SAD.A` | `feat.social_avoidance`: avoidance without fear is not SAD.A |
| `feat.fear_negative_evaluation` | `SAD.B` | |
| `feat.social_fear_consistent` | `SAD.C` | |
| `feat.social_avoidance` | `SAD.D` (needs `social_fear`) | |
| `feat.fear_disproportionate` | `SAD.E` (clinician judgement; the model may cite) | |
| `feat.performance_only` | `SAD.PERF` | |
| `imp.social`, `imp.occupational`, `imp.other` | impairment criteria (§3.7) | |
| `excl.substance`, `excl.medical`, `excl.psychotic_disorder`, `excl.recent_loss` | `*.C`/`D`/`E`/`F`/`G`/`H` exclusions; `excl.recent_loss` is a clinician-judgement note for `MDE` | |

That is 46 features: 11 depressive, 15 elevated, 9 anxiety, 6 social, 3 impairment, 4 exclusion. Exclusion findings use
their own statuses: `cause_evidence` (cited evidence that a substance or medical cause could explain the symptoms),
`ruled_out_cited` (for example "thyroid tests normal" is cited), and `not_assessed`.

---

## 4. Pipeline (new files under `services/progress/dsm/`)

### 4.1 Stage A: `progress-extract-dsm-v1` (`dsm/extraction.py`, `dsm/validation.py`, `dsm/prompts.py`)

- **Groups by disorder:**
  - depressive (11 features);
  - elevated (15);
  - anxiety (9);
  - social (6);
  - impairment and exclusion (7);
  - risk and plans (§4.3; the plan follow-up logic is reused).
  - Within a group, chunks of about 4–6 features are planned by the v1 token-budget planner pattern (output reservation
    scales with the features). `Recovery` splits a chunk on output limits and resumes from its checkpoint cache.
- **Packet:**
  - BM25 retrieval (±2 context turns) on each feature's `queries` **and** `counter_queries`, which look for denials and
    alternative explanations (for example "not sad", "I'm fine", "because of the exam", "after the illness", "on the
    new medicine");
  - English, Roman Urdu and Urdu keywords carried over from v1 where they exist.
  - The full transcript is used when it fits. Real 45–60 minute sessions will not, so retrieval is the norm.
- **Counter-evidence (single pass):** the model sees the counter-query turns in the same packet. For every `present`
  finding it must fill `counter_check`, either with refs and an alternative explanation, or `found: []`. Validation
  withholds a `present` finding whose `counter_check` is missing. A second "counter pass" call would double the calls
  per session; it is kept as an option if the pilot shows misses.
- **Prompt:**
  - the v1 untrusted-data wording;
  - the item paraphrases and inclusion/exclusion notes for each feature's linked items;
  - the status, frequency and timeframe definitions;
  - "a client denial is `explicit_denial`, not absent";
  - "avoidance explained by tiredness or low mood is not social fear";
  - "do not judge the whole condition".
- **Validation:** the v1 `Checker` is imported and reused:
  - alias mapping and foreign-id rejection;
  - attribution (`present` needs a client turn, or a clinician turn for observations; `explicit_denial` needs a client
    turn);
  - the other-person guard;
  - the 15-word copy guard;
  - the name guard.
  - New checks: `counter_check` for `present`; context-gated items are linked only when the finding carries the context;
    `clinician_intensity` is never accepted from the model; exclusion statuses only on `excl.*`.
- **Persistence:** in `dsm_findings` (§5), AI, pending review. Manual, accepted or edited values are never overwritten;
  a new suggestion becomes an *alternative* version, as in v1. Evidence links and staleness are handled as in v1.

### 4.2 Stage B: `condition-predict-dsm-v1` (`dsm/prediction.py`, `dsm/rules.py`)

- **Input:** all of the patient's sessions up to `as_of`:
  - active findings as compact rows (feature, status, frequency, time_frame, period, onset text, refs);
  - per-criterion retrieval across sessions, with patient-wide `A<n>` aliases;
  - the oldest raw turns are dropped first to fit the 4,096 context, with a visible note.
- **Calls:** one sequence per condition group: depression (MDE, MDD, PDD, specifiers), bipolar (episodes, BP1, BP2,
  CYC), GAD and SAD. Each sequence is criteria chunks, then one short period/duration call:
  - **criteria chunks:** the model proposes, per criterion item and per period, `met | not_met | insufficient_evidence |
    conflicting`, with evidence and counter-evidence refs and a duration note;
  - **period/duration call:** it groups findings into periods and states the cited duration for each period;
  - the model also returns its own label (logged in `postprocess_json.model_label`, **never used**).
- **Denial rule (code):** if any cited client turn in the criterion's counter-evidence, or in a linked `explicit_denial`
  finding, denies the criterion, a `met` proposal becomes `conflicting` and does not count.
- **Rule engine (code computes every label).** Element types:
  - `count` (items, min, must-include-any, `min_if_only_irritable`, same period);
  - `item` (a single criterion present);
  - `duration` (min days from a cited duration note, or hospitalised);
  - `impairment` (contexts, `marked`);
  - `exclusion` (cause evidence downgrades, not-assessed goes into "information needed");
  - `episode` (≥ 1 episode of type X in any cited period);
  - `never_episode`;
  - `not`, `all_of`, `any_of`.
- **Labels.** The card reads "Criteria pattern consistent with <condition>: <label>", with the fixed footer **"Hypothesis
  for clinician review; not a diagnosis."**
  - `likely`: every required element is met with citations, including the duration and impairment elements, and no
    exclusion or never-episode element is contradicted by cited evidence.
  - `possible`: the core element is met with citations (for example A1 or A2 within MDE.A; SAD.A; GAD.A; the mood
    period for MANIA.A), but another required element is unmet or unknown, or an exclusion has cited cause evidence.
  - `not_met`: the core element is cited absent, denied or conflicting (for example both A1 and A2; SAD.A denied).
  - `insufficient_evidence`: anything else.
  - **Not-assessed exclusions** (for example nobody asked about substances or past elevated periods) do not downgrade a
    label. They are listed under "information still needed". This is the policy question Q2.
- **Output:**
  - the label;
  - the checklist lines, for example `A: 6 of 9 present, includes A1 ✓ · B ✓ · C: thyroid normal cited ✓ · duration
    ≥ 2 weeks ✓`, each line with evidence and counter-evidence refs;
  - specifier suggestions (severity, anxious distress, SAD performance only), only when the label is `likely`;
  - ICD-10-CM and ICD-11 codes as reference;
  - differentials (from the registry, plus those whose elements were touched);
  - "information still needed", generated from unknown elements, never by the model alone;
  - `probability = null` (calibration stays v1/research-only).

### 4.3 Risk statements (`dsm/risk.py`; same table, additive columns)

- **Flag categories:** `suicidal_ideation_passive`, `suicidal_ideation_active`, `self_harm_past`, `self_harm_recent`,
  `harm_to_others`.
- **Supporting notes:** `protective_factor` and `denial_of_plan_or_intent`. These are shown under the flags, never as
  "safe".
- There is still no numeric field and no "no risk" conclusion.
- **Dedupe by segment:** flags that share a segment, or cite overlapping-time turns with near-identical text, merge into
  one row with `categories_json`. The role-play transcript has duplicated turns from overlapping recordings (for example
  "I feel guilty all the time" appears twice), and that is why v1 shows two `self_harm` rows.
- **ASR check:** when a flagged turn has a low-confidence word within ±3 words of a risk term, the card shows "possible
  transcription error: check audio", with ▶ at that turn. The transcript does not store ASR confidence today (Q6).

### 4.4 Trends (`services/progress/trends.py` is not benchmark-bound, so it can gain functions)

- **Per criterion item per session:** the status series. Missing stays missing (a gap); there are no zeros and no
  carry-forward.
- **Per condition:** the criteria-met count per `as_of` session.
- **Change events:**
  - `criterion.status_change`: present ↔ absent or explicit denial between consecutive assessed sessions;
  - `condition.label_change`: the DSM label changes;
  - `instrument.reliable_change`: unchanged.
  - De-duplication and acknowledgement as in v1.

---

## 5. Data model: migration `0010_dsm_registry` (additive only)

| Table | Change |
|---|---|
| **`dsm_findings`** (new) | id `dfind-`, patient_id, session_id, feature_key, registry_version, status, frequency, onset_or_duration_text, time_frame, period_label, contexts_json, linked_items_json (snapshot of the links applied), evidence_segment_ids_json, counter_evidence_segment_ids_json, counter_check_json, basis, clinician_intensity (int, nullable), is_active, plus the shared `_ReviewColumns`. One active row per (session, feature) |
| **`dsm_finding_versions`** (new) | as `domain_observation_versions` (history, alternative) |
| **`registry_verifications`** (new) | id, registry_kind (`dsm`), entity_kind (`item`, `condition`, `feature`), entity_id, content_sha256, status, verified_by, verified_on, comment, import_batch_id, created_at. Append-only; the latest row per (entity, content hash) applies |
| `condition_predictions` | add `registry_kind` (default `v1`), `checklist_json`, `specifiers_json`, `codes_json`, `periods_json` |
| `criterion_findings` | add `registry_kind` (default `v1`), `period_label`, `model_status` (the model's proposal before the denial and rule checks) |
| `risk_statements` | add `categories_json`, `note_kind` (`flag` or `supporting`), `asr_check_json` |
| `progress_evidence_links` | new owner kind `dsm_finding` (value only; no schema change) |
| `transcript_segments` or `segment_sources` | Q6 only: `asr_low_confidence_json` |

v1 tables and rows are untouched. `registry_kind` defaults to `v1`, so existing rows keep their meaning. The downgrade
drops only the new tables and columns.

**Contracts** (`schemas.py`, additive):
- new models: `DsmFinding`, `DsmFindingInput` (manual entry; `clinician_intensity` allowed only here), `ChecklistLine`,
  `RegistryVerification`, `RegistryImportResult`;
- `ConditionPrediction` gains optional `registry_kind`, `checklist`, `specifiers`, `codes`;
- `PredictionLabel` gains `not_met`;
- `RiskCategory` gains the new values;
- `PatientProgress` gains an optional `dsm` block.
- Existing fields are unchanged.

### 5.1 Registry selection

- `PROGRESS_REGISTRY=v1|dsm`, read by `jobs.py` and `progress_routes.py` (neither is benchmark-bound).
- Default `v1` until Phase 3 passes, then `dsm` for the clinical app (a one-line default change, done in Phase 3 and
  noted in the log).
- The benchmark, `run_final` and the vignette runner ignore it and use v1.
- The dashboard shows whichever registry produced the data. v1 data is shown in the collapsed section (§6).

### 5.2 Locks, staleness and review

These follow v1 rules:
- **Decided-note lock:** 409 `NOTE_TERMINAL` on creating or reviewing a decided session's findings.
- **Staleness:** `mark_stale` follows `progress_evidence_links`. A stale finding makes the dependent DSM predictions
  stale.
- **Review:** accept, edit or reject, with an audit row and the `original_ai_json` kept immutable.
- **Badges:** unchanged ("AI-generated · human review pending", "Reviewed by …").

### 5.3 API (`progress_routes.py` plus a new `dsm_routes.py`)

`GET /progress/registry?kind=dsm` (registry plus effective verification) ·
`GET /progress/registry/dsm/export.csv` · `GET /progress/registry/dsm/export.html` (printable) ·
`POST /progress/registry/dsm/verifications/import` (CSV) ·
`GET /sessions/{id}/dsm-findings` · `PUT /sessions/{id}/dsm-findings/{feature_key}` (manual) ·
`POST /sessions/{id}/dsm-findings/{id}/review` · `POST /patients/{id}/dsm-reanalysis` → 202 ·
`POST /patients/{id}/dsm-reanalysis/{batch}/revert` · and the existing extraction/prediction/job routes, dispatching on
`PROGRESS_REGISTRY`.

### 5.4 Clinician review export and import

- **Export:** one row per item, feature, rule and condition. Columns: `entity_kind`, `id`, `paraphrase` or rule
  summary, `chapter`, `icd10cm`, `icd11`, `content_sha256`, `current_status`, `decision` (blank), `verified_by`,
  `verified_on`, `comment`.
- **Printable HTML:** the same content grouped by condition and criterion letter, with tick boxes and space for
  comments, and a footer stating these are paraphrases, not APA text.
- **Import:**
  - It accepts only known ids with a matching `content_sha256`. A changed hash is rejected for that row and reported.
  - `decision` must be `verified`, `changed` (comment required: the suggested wording or rule) or `pending`.
  - It never changes ids or paraphrases. A `changed` decision is a request; a developer edits the JSON deliberately,
    which changes the hash and returns the item to pending.
  - Each import is a batch with an audit row.

---

## 6. Mapping from v1 (`v1_mapping.json` and `docs/PROGRESS_REGISTRY_V1_TO_DSM.md`)

| v1 domain | DSM item(s) | Note |
|---|---|---|
| `low_mood` | `MDE.A1`, `PDD.A` | |
| `interest` | `MDE.A2` | |
| `sleep` | `MDE.A4`/`PDD.B2` (reduced, increased); `MANIA.B2` (reduced need without fatigue); `GAD.C6` (disrupted, with worry) | v1 direction decides |
| `appetite` | `MDE.A3`, `PDD.B1` | |
| `energy` | `MDE.A6`, `PDD.B3`, `GAD.C2` | |
| `self_worth` | `MDE.A7`; `PDD.B4` | v1 merged guilt and self-esteem |
| `concentration` | `MDE.A8`, `PDD.B5`, `GAD.C3`, `ANXD.3` | related but different: `MANIA.B5` |
| `psychomotor` | `MDE.A5` (observable); `GAD.C1`/`ANXD.2` (felt restlessness) | |
| `elevated_mood` | `MANIA.A`/`HYPO.A` (mood part) | |
| `irritability` | `MANIA.A`/`HYPO.A` (irritable period) or `GAD.C4` | context decides |
| `racing_thoughts_pressured_speech` | `MANIA.B3` and `MANIA.B4` | split |
| `increased_goal_directed_activity_risky_behaviour` | `MANIA.B6`, `MANIA.B7`, energy part of `MANIA.A` | split |
| `grandiosity` | `MANIA.B1` | |
| `worry` | `GAD.A`, `GAD.B` | |
| `restlessness_tension` | `GAD.C1`, `GAD.C5`, `ANXD.1`, `ANXD.2` | |
| `social_fear_avoidance` | `SAD.A`–`SAD.C` (fear) and `SAD.D` (avoidance) | split: avoidance alone is not A |
| `performance_fear` | `SAD.A` plus the `SAD.PERF` specifier | |
| `social_functioning` | impairment evidence `imp.social` | no DSM symptom equivalent |
| `academic_work_functioning` | `imp.occupational` | no DSM symptom equivalent |
| `self_care` | `imp.other` | no DSM symptom equivalent |

- v1 rows are **never converted**. On the dashboard they appear in a collapsed **"Earlier (non-DSM) tracking"** section
  (the existing domain grid, read-only labels, the review actions still working).
- **Re-analysis** (`POST /patients/{id}/dsm-reanalysis`) runs DSM Stage A on the chosen past sessions, then Stage B.
  Its rows carry the batch id. **Revert** deactivates that batch's DSM rows (kept in history, audited); v1 rows are never
  touched. Sessions with decided notes are skipped and listed (Q5).
- A test asserts that the mapping covers every v1 domain and that every target id exists.

---

## 7. UI (Phase 3)

- **Criteria grid:**
  - grouped condition → criterion letter → item;
  - rows show the DSM id, the paraphrase, the verification badge, and per-session status icons with text (present,
    subthreshold, absent, denied, insufficient, conflicting, stale) and the frequency text;
  - a toggle switches between grouping by condition and "shared findings once";
  - clicking a cell opens the evidence drawer.
- **Hypothesis cards:**
  - the label line, the footer, and the checklist with one row per rule element;
  - each row links to its evidence and counter-evidence (▶ plays the audio, "Open in transcript" focuses the turn: two
    clicks);
  - specifiers, codes, differentials and "information still needed".
- **`/workspace/registry`:** every item and rule with its id, paraphrase, chapter, ICD codes and verification status;
  filters; CSV and HTML export buttons; CSV import with a result summary.
- **Print view:** the DSM checklists and criteria grid replace the domain grid. Review badges and the two-click citation
  path are unchanged.

---

## 8. Tests

1. **Registry (Phase 1):**
   - unique ids;
   - every rule references existing items;
   - every feature link targets an existing item;
   - `same_as` symmetry;
   - **every paraphrase ≤ 20 words** (and a scan rejecting long verbatim-looking runs against a small list of banned
     phrases);
   - verification fields present on every item and rule;
   - ICD fields present;
   - v1 mapping complete;
   - instrument map complete, with PHQ item 9 → `MDE.A9` clinician-only;
   - disabled extensions are not loaded.
2. **Benchmark guard (Phase 1):**
   - the 14 bound files hash to the `val12` config values;
   - `services/progress/evaluation/*` never imports `services.progress.dsm`;
   - v1 tests unchanged and passing.
3. **Rule engine (Phase 1, hand-built fixtures):**
   - MDE with 4 vs 5 items;
   - missing core item (5 items without A1 or A2);
   - duration unmet (10 days);
   - substance cause cited → `possible`;
   - irritable-only mania with 3 vs 4 B-items;
   - hypomania vs mania (marked impairment or hospitalisation flips it; 5 days hypomanic; 5 days plus hospitalised is
     manic);
   - B-items in different periods do not combine;
   - BP2 needs both episode types and no manic episode;
   - BP1 from a past cited manic episode;
   - GAD at 5 months vs 6;
   - GAD.C3 not counted without the worry context;
   - SAD avoidance without fear ≠ A;
   - SAD.A denial → `not_met`;
   - denial → `conflicting` → not counted;
   - not-assessed exclusion goes to "information needed" without a downgrade;
   - specifier rules (anxious distress levels, severity suggestion);
   - PHQ derivation from findings.
4. **Registry export and import (Phase 1):** CSV round trip, a hash mismatch rejected, an unknown id rejected, `changed`
   needs a comment, ids unchanged, audit row written.
5. **Stage A and B with mocked model outputs (Phase 2):**
   - schema with enum-restricted refs;
   - `counter_check` required;
   - context gating;
   - `clinician_intensity` rejected from AI;
   - the other-person, quotation and name guards still apply;
   - truncation split and resume;
   - the model label ignored but logged;
   - risk categories, dedupe of duplicated turns, supporting notes, the ASR flag (if Q6 is approved).
6. **Role-play regression (Phase 2, mocked outputs; Phase 4 with the real model).** It uses the saved fictional two-session
   patient (transcripts only; fixture §9). Expected:
   - depression (`MDE`) `likely`;
   - **SAD not `possible`**: `not_met`, from the "it's not that I'm scared of them" denial;
   - bipolar episodes `insufficient_evidence` and BP1/BP2 not assessable;
   - the passive suicidal-ideation statement flagged once, with "no, not yet" recorded as `denial_of_plan_or_intent`
     plus an ambiguity note;
   - GAD not `likely`.
7. **Persistence (Phase 2):** staleness on segment edit, decided-note lock, review badges and audit, re-analysis plus
   revert leaving v1 rows intact.
8. **Frontend (Phase 3):**
   - the build passes;
   - Playwright (mocked API): the criteria grid renders with all statuses, a checklist line opens its cited turn, the
     registry export downloads, the import shows its summary, and the earlier-tracking section collapses and expands.

---

## 9. Role-play regression fixture

The fictional two-session role-play patient in the local DB (`session-019`, 100 turns; `session-020`, 13 turns; a third
session with no transcript) gives the current **v1 baseline**:

| Condition | v1 result (as of session 2) |
|---|---|
| Depressive episode | `likely`, 6 criteria met |
| Social anxiety | **`possible`, 4 met**: the false positive this redesign must fix |
| Bipolar | `insufficient_evidence`, depressive features only |
| GAD | `insufficient_evidence` |
| Risk | 3 rows (two `self_harm`, one `suicidal_ideation`), with duplicates |

Fixture plan (Q7): copy only the transcript turns (role, timestamps, text) into
`tests/fixtures/roleplay_dsm/`, with no intake data (the intake has a phone number and email) and no names (none appear
in the turns). Mark it as a fictional role-play.

---

## 10. Phases

| Phase | Work | Stop condition |
|---|---|---|
| 0 | This plan, `CLAUDE.md` rules, `.gitignore` for the DSM PDF | **Approval** |
| 1 | Registry JSON (items, features, conditions, maps), the `dsm/registry.py` loader, the `dsm/rules.py` rule engine, migration 0010 and ORM, contracts, verification export/import (CLI and API), benchmark-guard test, mapping doc, tests 1–4 | Tests plus build pass; commit; log |
| 2 | DSM prompts, Stage A and B, risk categories and dedupe, PHQ/GAD derivation, persistence, re-analysis and revert, jobs dispatch on `PROGRESS_REGISTRY`, the fixture, tests 5–7 with mocked outputs | Same |
| 3 | Criteria grid, checklist cards, `/workspace/registry`, print view, earlier-tracking section, Playwright; then the clinical default switches to `dsm` | Same |
| 4 | Real local-model role-play run: **I will state the runtime first**. Estimate: Stage A about 10 calls × 25–35 s per session ≈ 5–6 min, × 2 sessions; Stage B 4 groups ≈ 5–8 min; **about 20–30 min GPU in total**. Then the comparison with v1, `docs/PROGRESS_MONITORING.md`, the `PROJECT_STATUS.md` addendum and the final report | Final report |

---

## 11. Open questions

**For you (needed before or during Phase 1):**

- **Q1: Branch.** New branch `feat/progress-dsm` from the current `feat/internet-meetings` HEAD (which contains all of
  `progress-monitoring`), rather than `progress-monitoring` itself, so the work sits on the newest code. OK?
- **Q2: Not-assessed exclusions.** Recommended: unasked exclusions (substances, medical causes, past elevated periods
  for MDD.E) do **not** downgrade a label and are listed under "information still needed"; only cited evidence of a
  cause downgrades. The stricter alternative is that every exclusion must be cited as ruled out before `likely`, which
  would make `likely` rare in real sessions.
- **Q3: `MDE.A9`.** Recommended: it counts only from clinician-accepted risk statements or clinician-entered PHQ-9 item
  9, and is otherwise unknown. The alternative is to leave A9 out of the count entirely.
- **Q4: AI PHQ-9/GAD-7 estimates in DSM mode.** Recommended: derived in code from findings (§3.8), with no extra model
  calls. The alternative is to keep the v1 item-scoring calls (about 2 more calls per session).
- **Q5: Re-analysis of decided-note sessions.** Recommended: skip them and list them. The alternative is to analyse them
  but lock review.
- **Q6: ASR confidence for the "possible transcription error" flag.** Transcripts do not store confidence. Recommended:
  in Phase 2, whisper.cpp writes full JSON (`-ojf`, token probabilities) and the lowest-confidence words per segment
  are stored (additive column; new transcriptions only). This touches `services/speech/transcriber.py`, which is not in
  the benchmark. Older transcripts show no flag. The alternative is to drop the ASR flag.
- **Q7: Fixture.** OK to commit the role-play transcript turns (no intake, no names) under `tests/fixtures/`? Otherwise
  the regression test reads the local DB and is skipped on fresh clones.
- **Q8: Vignette labels.** The vignette schema (Phase 6) uses v1 domains and criteria. Should the labels move to DSM item
  ids, so the consultant labels against the new registry? Recommended: yes, as an additive `labels_dsm` block in Phase 2.
  The vignette *runner* stays v1 unless you say otherwise.
- **Q9: Condition list.** Enabled targets: MDE/MDD, manic/hypomanic episodes → BP1/BP2, GAD and SAD; differentials:
  PDD and cyclothymic. Should panic disorder and agoraphobia be added as differentials only (no tracking), since GAD.F
  and SAD.I refer to them?
- **Q10: Instrument terms.** WHODAS 2.0 (WHO permission process), the DSM-5-TR Level 1 Cross-Cutting Symptom Measure and
  a mania scale (for example ASRM) all stay disabled extension points until you confirm their terms. Do you have, or
  want to request, permission for any of them?

**For the clinical adviser:**

- Every paraphrase in §3, line by line, using the export.
- The context gating (GAD.C items and impairment only when linked to worry; SAD.D only with fear).
- The separation choices in §3.10.
- The severity suggestion rule (§3.1).
- The label definitions (§4.2), including the treatment of not-assessed exclusions.
- The A9 policy.
- How past episodes should be dated and accepted from client recall.
- Whether `subthreshold` needs a definition per item.
- Whether irritability in GAD and in mania can be told apart from transcripts.
- The handling of bereavement and recent loss for MDE.

---

## 12. Progress log

### Phase 0: 9 November 2026
- **Done:**
  - read `CLAUDE.md`, the whole progress plan and its log, `PROGRESS_MONITORING.md`, the freeze rule, the registry and
    loader, the extraction schema, the Stage B outline, trends, store, ORM, contracts and the UI file list;
  - checked the freeze and run status and the 14 bound hashes (all match);
  - read the DSM-5-TR criteria structure for MDE/MDD, manic and hypomanic episodes, BP1, BP2, cyclothymic, PDD, GAD,
    SAD, the anxious-distress and severity specifiers, and the ICD-10-CM coding notes, from the PDF's text (extracted to
    the scratchpad, not the repository);
  - looked up the role-play patient's v1 results;
  - wrote this plan and the `CLAUDE.md` rules;
  - git-ignored the DSM PDF.
- **Tests:** none run; no code changed.
- **Deviations:**
  - Plan-mode tool not used, because it blocks writing this file (same as earlier Phase 0s).
  - The hypomanic episode has criteria A–F, not only C/D, and all are modelled.
  - The PHQ-9 order differs from the MDE.A order (§3.8).
  - Usage terms for WHODAS 2.0 and the Level 1 measure could not be confirmed, so both are extension points.
- **Next:** wait for approval and answers to Q1–Q10, then Phase 1.

### Phase 0 addendum: approval, 9 November 2026
- **Approved.** Answers:
  - Q1 yes (branch `feat/progress-dsm`).
  - Q2 yes: unassessed exclusions go to "information still needed".
  - Q3 yes: `MDE.A9` only from PHQ-9 item 9 or an accepted risk statement.
  - **Q4 no:** PHQ-9 and GAD-7 AI estimates stay as their own separate calls; they are not derived from findings.
  - **Q5:** sessions with decided notes are not re-analysed. For re-analysed sessions, the earlier model output can be
    removed from the dashboard (implemented as "retired", restorable by revert, never deleted).
  - **Q6:** the ASR flag is dropped.
  - Q7 yes (fixture).
  - Q8 yes (vignette labels use DSM ids).
  - Q9 undecided: panic disorder and agoraphobia are text-only differentials, not tracked.
  - **Q10:** the user has permission for WHODAS 2.0, the Level 1 Cross-Cutting measure and a mania scale. These are
    enabled as clinician-administered instruments (no AI estimates): WHODAS 2.0 12-item, the DSM-5-TR Level 1 adult
    measure and the ASRM.
- The user asked for all phases in this session.

### Phase 1: 9 November 2026 (registry, rule engine, migration, contracts, export/import)
- **Built:**
  - `services/progress/registry/dsm/`:
    - `items.json`: 93 criterion items, all `pending`;
    - `features.json`: 46 features, with `separate_from` reasons;
    - `conditions.json`: 10 conditions, each a declarative rule;
    - `instruments.json`: WHODAS 2.0 12-item, Level 1 CCSM adult, ASRM; clinician-only, with short item descriptors,
      not official wording;
    - `instrument_map.json`, `v1_mapping.json`, `risk.json`.
  - `services/progress/dsm/registry.py`: Pydantic loader with cross-checks, a 20-word limit, content hashes and
    `PROGRESS_REGISTRY`.
  - `dsm/rules.py`: the rule engine:
    - element types: item, count, duration, exclusion, judgement, absence, episode, all_episodes, never_episode,
      subthreshold_course, note;
    - per-period episodes;
    - denial → conflicting;
    - context and degree gating;
    - labels, checklist lines, specifiers (severity, anxious distress, performance only), codes, information needed.
  - `dsm/verification.py`: CSV and printable HTML export, a CSV import bound to content hashes, and a CLI.
  - `dsm/store.py`: findings and versions, manual entry, review edit, risk persistence with categories, DSM
    predictions, A9 sources, batches with retire and revert, and the dashboard block (criteria grid, feature cells,
    hypotheses, timeline, verification summary).
  - Migration `0010_dsm_registry` (additive): `dsm_findings`, `dsm_finding_versions`, `dsm_predictions`,
    `registry_verifications`, `dsm_batches`; `retired_batch_id` on four v1 tables; risk `categories_json`,
    `note_kind`, `registry_kind`.
  - ORM; additive contracts (`DsmFinding`, `DsmFindingInput`, `DsmPrediction`, `RegistryImportResult`,
    `CounterEvidence`; widened `RiskCategory`, `PredictionLabel` and `ChangeEvent.measure_kind`; `PatientProgress.dsm`
    and `registry_kind`).
  - `services/api/dsm_routes.py`.
  - v1 store hooks: staleness owners, combined instrument catalogue with per-instrument item ranges, retired-row
    filtering, risk categories, DSM finding history on review.
  - trends: `criterion.status_change`, `condition.label_change`.
  - `docs/PROGRESS_REGISTRY_V1_TO_DSM.md`.
- **Tests:** 401 passed (+46).
  - Rule engine: MDE 4 vs 5, missing core, both cores denied, duration, substance cause vs unassessed, denial →
    conflicting, A9, irritable-only mania, hypomania vs mania, periods not combined, BP1 from a past episode, BP2,
    GAD 6 months, GAD context gating, SAD avoidance without fear, avoidance alone, full SAD with performance only,
    specifiers, footer, cyclothymic, empty input.
  - Registry: ids, 20 words, long-paraphrase rejection, verbatim tripwire, verification, cross-check rejection,
    shared and separated features, v1 mapping, instrument map, instruments.
  - Benchmark guard: 14 pinned hashes; evaluation modules never import `dsm`.
  - API: manual and AI findings, review edit with immutable original, lock, staleness, grid gaps and change events,
    export/import round trip, revert.
  - The frontend build passes.
- **Deviations:**
  - DSM hypotheses use a new `dsm_predictions` table instead of new columns on `condition_predictions`: v1 endpoints
    look every row up in the v1 registry, so sharing the table would break them.
  - The CSV import takes a JSON body (`{actor, csv}`), so no `python-multipart` dependency is needed.
  - `InstrumentScoreInput` allows 0–4, with the exact range checked per instrument in the store.
  - `PROGRESS_REGISTRY` defaults to `v1` until Phase 3.
- **No new dependencies.** None of the 14 benchmark-bound files changed (guard test).
- **Next:** Phase 2, the Stage A and B DSM prompts and pipeline, jobs, re-analysis, and the role-play fixture.

### Phase 2: 9 November 2026 (DSM Stage A and B, risk, jobs, re-analysis, regression)
- **Built:**
  - `dsm/prompts.py` (`progress-extract-dsm-v1`, `condition-predict-dsm-v1`).
  - `dsm/extraction.py`, Stage A:
    - six feature groups planned into chunks that fit the budget; full transcript or BM25 retrieval over feature and
      counter-evidence queries;
    - grammar-constrained schema with enum refs and typed counter refs;
    - validation reusing the v1 `Checker` (aliases, attribution, other-person, quotation and name guards), with a
      required `counter_check` for `present`, client-only denials, and qualifiers and degree checked against the
      registry;
    - DSM risk categories with supporting notes, de-duplicated by shared segment or near-identical overlapping turns;
    - PHQ-9 and GAD-7 items and plan follow-ups as **separate calls with the unchanged v2 prompt**, via v1 functions.
  - `dsm/prediction.py`, Stage B:
    - cross-session feature proposals with typed counter-evidence;
    - Stage A client denials added as counter-evidence;
    - a periods call that gets extra onset and duration retrieval and keeps those turns longest when fitting the
      budget;
    - the rule engine computes every label; the model's own labels are logged in `postprocess.model_label` only;
    - only cited hypotheses are stored.
  - `jobs.py`: dispatch on `PROGRESS_REGISTRY`; DSM extraction creates a batch, retires the session's unreviewed v1
    output and saves findings, questionnaires, plans and risk flags; a `reanalysis` job kind (decided-note sessions
    skipped and listed; Stage B as of the last analysed session); `POST /patients/{id}/dsm-reanalysis`.
  - Vignette labels: an optional `dsm` block (conditions with DSM labels and items; per-session items), validated
    against registry v2. The authoring guide is updated. The vignette runner stays v1.
  - Fixture `tests/fixtures/roleplay_dsm/` (two sessions, transcript turns only). Three spoken first names in the
    turns were replaced with `[name]`.
- **Tests:** 408 passed (+7).
  - Role-play regression with mocked outputs: MDE and MDD `likely` (8 of 9 items with A1 and A2; 60 days cited;
    substances and thyroid ruled out); **SAD `not_met`** (avoidance explained by low energy, fear denied; the model's
    own "possible" logged only); GAD `not_met`; bipolar I `insufficient_evidence`; MDD.E history listed as needed;
    severity specifier; passive suicidal-thoughts flags merged into one; recent self-harm flagged; protective factor
    and denial merged into one supporting note.
  - A Stage A denial contradicting a Stage B proposal.
  - Jobs end to end: findings, questionnaires as separate calls, risks, hypotheses with resolved citations, review
    edit with immutable original.
  - Re-analysis: decided session skipped, v1 retired, revert restores it.
  - Review acknowledgement required.
  - DSM vignette labels validated.
- **Deviations:**
  - Stage B proposes per *feature* (each feature maps to one or more criterion items in code), not per criterion
    item. It is cheaper, and shared items stay consistent.
  - Counter-evidence is a single pass (in the same call), as planned.
  - Plan follow-ups are re-evaluated in place on re-analysis, so a revert does not restore them.
- **No new dependencies.** Bound files unchanged.
- **Next:** Phase 3, the UI.

### Phase 3: 9 November 2026 (UI)
- **Built** (`apps/web/src/features/progress/`):
  - `Dsm.tsx`:
    - **hypothesis cards**: label line, fixed footer, review badge, required-criteria count, period, ICD codes; the DSM
      rule as a checklist. Each line opens its items, cited turns and counter-evidence (client denial or alternative
      explanation) with ▶ play and "Open in transcript": card → line → turn/audio in two clicks. Also specifiers,
      information still needed, differentials, and the model's own label shown as "logged, never used". Target
      conditions first; episodes and differentials collapsed.
    - **criteria grid**: condition → disorder → letter → item, with DSM id, label, paraphrase and verification badge;
      per-session cells for present, subthreshold, absent, denied, insufficient, conflicting, not linked and stale,
      with frequency, a review corner mark and gaps as "—". A "Shared findings once" toggle. A finding drawer with
      citations, counter-evidence, review and edit.
    - **hypothesis timeline**.
    - **re-analysis panel**: review acknowledgement, start, and a batch list with revert.
    - **Earlier (non-DSM) tracking**: a collapsed section holding the v1 hypotheses, domain grid and timeline.
  - `DsmSessionPanel.tsx`: the session page shows DSM findings when the active registry is `dsm`, with analyse,
    manual entry (clinician intensity 0–3 only here), review, questionnaire items and flagged statements.
  - `RegistryPage.tsx` at `/workspace/registry` (sidebar "DSM registry"): conditions with rule summaries and codes,
    items with filter and status, features with separation reasons, CSV export, printable review sheet, CSV import with
    a result summary.
  - Risk panel: several categories per statement, supporting notes, a flag count excluding supporting notes.
  - Instruments: per-instrument item ranges (0–4 for WHODAS, CCSM, ASRM), the licence note, a collapsed "More
    questionnaires" section, and CCSM domains for further inquiry.
  - Print view: DSM checklists with turn references and footer, the criteria grid with verification status, earlier
    tracking.
- **Rules tweaks found while checking the UI:**
  - A duration element is not evaluated until the condition's core criterion is met or partly met. A period's duration
    belongs to whatever was happening then, and GAD showed "6 weeks, needs 6 months" when nothing pointed to GAD.
  - The "not linked" wording now names both contexts ("linked to low mood, not worry or anxiety").
- **Default switched:** `PROGRESS_REGISTRY` defaults to `dsm` for the clinical app. The v1 Playwright suite and the v1
  job tests pin `v1`. The benchmark never reads the setting. `.env.example` documents it.
- **Tests:**
  - 408 backend tests passed; the frontend build passes.
  - New Playwright suite `npx playwright test -c playwright.dsm.config.ts` (fictional seed
    `tests/progress/dsm/e2e_seed_dsm.py`): 4/4. It checks the criteria grid statuses, gap and badges; the
    shared-findings toggle; a checklist line opening its cited turn; the denial giving SAD "Not met"; the footer;
    earlier tracking collapsed then expanded; a finding accept changing the badge; the registry page and CSV export.
  - The v1 progress suite (`playwright.progress.config.ts`) still passes 4/4.
  - Pages checked by screenshot.
- **Next:** Phase 4, the real-model role-play run.

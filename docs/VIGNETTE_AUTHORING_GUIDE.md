# Vignette authoring guide

The progress module predicts four conditions:

- bipolar disorder (manic, hypomanic and depressive episode features);
- generalized anxiety disorder (GAD);
- social anxiety disorder (SAD);
- major depressive episode.

LongCounsel-8 can only evaluate the depression track. The other three are **unvalidated** until a set of written cases
exists, labelled by the clinical consultant. This guide explains how to write those cases and their labels, and how to
run the evaluation.

The code is in `services/progress/evaluation/vignettes.py`. Cases live in `data/research/vignettes/`.

## 1. Roles and blinding

| Who | Does | Must not |
|---|---|---|
| Case writer (team member or consultant) | Writes `case.json` and the sessions | Name the condition in the case title, summary or folder name. Copy real patient material. Paste DSM or ICD text. |
| Consultant (labeller) | Writes `labels.json` for each case | See any model output for that case before the labels are final |
| Person running the evaluation | Runs `validate`, `run`, `report` | Edit cases or labels after a run (a new run-id is needed) |
| Anyone tuning prompts | – | Read `labels.json` or the vignette reports. Tuning on this set would make it a development set. |

`labels.json` must say `"blind_to_model_output": true`; the validator refuses anything else. The run never opens
`labels.json`. Labels are read only by `validate` and `report`. They never reach a prompt or the clinical database.

The set is evaluated once per prompt version. If cases are rewritten after seeing results, say so in the report and
treat the earlier numbers as development results.

## 2. Case mix

This is the mix proposed to the consultant (plan §13). Aim for **at least 3 cases per row**, about 24 cases, so that
each condition has both positive and negative examples.

| Case type | Purpose | Expected labels (examples) |
|---|---|---|
| Bipolar I, with a manic period | Bipolar I pattern from a described manic period (current or past) | bipolar present, subtype `bipolar_i_pattern` |
| Bipolar II, with a hypomanic period and depression | Bipolar II pattern; no manic period | bipolar present, `bipolar_ii_pattern`; depressive episode present |
| Unipolar depression (bipolar differential) | Depression without elevated periods; tests that bipolar is not over-called | depressive episode present; bipolar absent or `depressive_features_only` |
| GAD | Several worries, hard to control, physical symptoms, months of duration | gad present |
| SAD | Fear of scrutiny, avoidance, out of proportion | social_anxiety present |
| GAD + SAD | Co-occurring; tests that each is assessed separately | gad present, social_anxiety present |
| Subthreshold | Some features without the count, duration or impairment | condition `subthreshold` |
| No disorder (control) | Ordinary stress or life problems | all `absent` |

Also include, spread across the cases:

- **Denials:** "I sleep fine".
- **Third-person statements:** "my brother is depressed". These must not count for the client.
- **Past episodes:** e.g. two years ago, alongside the current presentation.
- **Indirect descriptions:** behaviour rather than symptom words.
- **Mixed content within a session:** one session that discusses more than one condition.
- **Risk statements:** at least one passive or historical one, so the flagged-statements panel is exercised. Do not
  write method detail.

## 3. Writing sessions

Each case has 3–5 sessions, about one week apart (`days_since_first`: 0, 7, 14, …). Each session has 12–40 turns,
alternating `CLINICIAN` and `CLIENT`.

- **Write ordinary counselling dialogue.** The clinician asks open questions; the client describes days, behaviour and
  reactions. Avoid questionnaire wording ("on a scale of 0–3", "PHQ") and diagnostic names in the client's turns,
  unless that is the point of the case.
- **Give timing in words when it matters:** "for about a year", "for four or five days last spring, barely slept and
  felt unstoppable". Duration and impairment decide between `likely` and `possible`.
- **Let symptoms change across sessions** when the case calls for it. This exercises the trend charts and change markers.
- **Keep it fictional.** Use no real names, places that identify anyone, or copied published vignettes. Turns are
  plain text, up to 4,000 characters each.
- **Language:** English for now. Prompts are English-only and Urdu is untested. Urdu or code-switched cases can be
  added later as a separate, clearly marked group.

## 4. File formats

```text
data/research/vignettes/
  bp-007/
    case.json
    sessions/s1.json  s2.json  s3.json
    labels.json
```

`case.json` (the `case_id` must equal the folder name: lowercase letters, digits and `-`):

```json
{"case_id": "bp-007", "title": "University student, sleep and energy changes", "synthetic": true, "language": "en",
 "author": "<writer>", "created": "2026-11-20", "summary": "Neutral description without a diagnosis."}
```

`sessions/s<n>.json`. Turns carry only `turn_id`, `speaker` and `text`; any other field is rejected as a possible label
leak:

```json
{"session_index": 1, "days_since_first": 0, "turns": [
  {"turn_id": 1, "speaker": "CLINICIAN", "text": "How have the last couple of weeks been?"},
  {"turn_id": 2, "speaker": "CLIENT", "text": "..."}]}
```

`labels.json`:

```json
{"case_id": "bp-007", "labelled_by": "<consultant>", "labelled_on": "2026-11-25", "blind_to_model_output": true,
 "conditions": {
   "bipolar": {"status": "present", "subtype_key": "bipolar_ii_pattern",
               "criteria": {"bipolar.hypomanic_period": "met", "bipolar.manic_period": "not_met", "mania.decreased_sleep_need": "met"}},
   "depressive_episode": {"status": "present", "criteria": {"mde.depressed_mood": "met", "mde.anhedonia": "met"}},
   "gad": {"status": "absent"},
   "social_anxiety": {"status": "absent"}},
 "session_domains": {"1": {"low_mood": 2, "sleep": 3, "elevated_mood": "insufficient_evidence"}},
 "notes": ""}
```

### What the labels mean

- **Judge only the transcript.** Label what the written sessions support, not what the writer intended. A condition the
  writer meant but the dialogue never shows is `subthreshold` or `absent`, with a note.
- **Condition `status`:**
  - `present`: the sessions support the condition's requirements.
  - `subthreshold`: some features are shown, but the count, duration or impairment is missing.
  - `absent`: not supported.
  - For sensitivity and specificity, only `present` counts as positive.
- **`subtype_key`** applies to bipolar only: `bipolar_i_pattern`, `bipolar_ii_pattern`, `cyclothymic_pattern` or
  `depressive_features_only`.
- **Criterion values:**
  - `met`: the sessions show it.
  - `not_met`: the sessions show its absence, for example a denial.
  - `insufficient_evidence`: not discussed.
  - Label the criteria you can judge; leave out the rest.
  - Criterion ids are in `services/progress/registry/conditions.json`. Their summaries are paraphrases pending your
    verification.
- **`session_domains`** rates the current period discussed in that session (past episodes are not rated here):
  - 0 not present;
  - 1 mild or occasional;
  - 2 moderate or frequent;
  - 3 severe or nearly constant;
  - or `insufficient_evidence` when the domain is not discussed.

  Domain keys are in `services/progress/registry/domains.json`. Only a few key domains per session are needed.

## 5. Running the evaluation

```bash
source .venv/bin/activate
python -m services.progress.evaluation.vignettes validate
python -m services.progress.evaluation.vignettes run --run-id vig-v2-retrieval     # GPU, resumable, Ctrl+C safe
python -m services.progress.evaluation.vignettes status --run-id vig-v2-retrieval
python -m services.progress.evaluation.vignettes report --run-id vig-v2-retrieval
```

**`validate`** checks the schema and the registry, then prints the case mix.

**`run`** works like the LongCounsel benchmark: same model, prompts and checkpoint behaviour.

- **What it runs:** Stage A on every session, then Stage B for all four conditions as of the last session, with
  prior-session history.
- **Evidence:** retrieval by default; `--evidence full` or `auto` are also accepted.
- **Expected time on a GTX 1650:** about 3.5 min of Stage A per session, plus about 1.5 min per condition per case
  (measured for depression only). A 24-case set of 4 sessions each comes to about 8 hours. It may take longer: Stage B
  for bipolar has 15 criteria and has not been timed.
- **Checkpoints and edits:** progress is saved per case. Editing a case after a run makes the same run-id refuse to
  continue; start a new run-id.

**`report`** writes `data/research/vignette-runs/<run-id>/report/` and the vignette section of
`/workspace/research-progress`:

- per-condition sensitivity, specificity, PPV, NPV and F1 at the `possible` and `likely` thresholds, with case-bootstrap
  95% intervals;
- subtype agreement;
- criterion agreement (confusion table, agreement when both decided, abstention, Cohen's κ for met vs not met);
- session domain agreement (MAE, exact, within 1, abstention);
- `citation_rating.csv`: 100 random cited findings, with empty rating columns for a human rater.

Cases edited after the run are excluded and listed.

## 6. Interpreting results

These numbers measure agreement with one consultant's labels on written cases. They do not measure diagnostic accuracy
on real patients.

- **Small sets give wide intervals.** Report them.
- **Abstention and errors are different.** The model abstains (`insufficient_evidence`) more often than it
  contradicts; read abstention rates alongside sensitivity.
- **Structural citation checks** confirm only that a cited turn exists in that session. Whether it supports the claim
  is what `citation_rating.csv` asks a human to judge.

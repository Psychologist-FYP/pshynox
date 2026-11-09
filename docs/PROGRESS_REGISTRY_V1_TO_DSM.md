# Progress registry v1 → DSM-5-TR registry (v2) mapping

Registry v1 tracked 20 ad-hoc *domains*. Registry v2 tracks DSM-5-TR *criterion items* (`docs/PROGRESS_DSM_REGISTRY_PLAN.md`).
This table, generated from `services/progress/registry/dsm/v1_mapping.json`, shows how earlier tracking relates to the new items.

- **v1 rows are never converted automatically.** They stay readable on the dashboard under the collapsed **Earlier (non-DSM) tracking** section.
- **Re-analysis** (Progress page → *Re-analyse earlier sessions with DSM criteria*) runs the DSM extraction on chosen past sessions. It skips sessions whose note is decided. For each re-analysed session, the earlier *unreviewed* model output (pending domain values, AI questionnaire estimates, flagged statements and hypotheses) leaves the dashboard. Clinician-entered and reviewed values stay.
- **Revert** undoes a re-analysis: its DSM rows are deactivated (kept in history) and the retired v1 rows come back. Plan follow-ups are re-evaluated in place and are not restored by a revert.
- Each condition's paraphrases and rules are pending clinical verification (export: `/workspace/registry`).

| v1 domain | DSM criterion items | Note |
|---|---|---|
| `low_mood` | `MDE.A1`, `PDD.A` |  |
| `interest` | `MDE.A2` |  |
| `sleep` | `MDE.A4`, `PDD.B2`, `MANIA.B2`, `GAD.C6` | The v1 direction decides: reduced/increased → MDE.A4/PDD.B2; reduced need without fatigue → MANIA.B2; disrupted with worry → GAD.C6. |
| `appetite` | `MDE.A3`, `PDD.B1` |  |
| `energy` | `MDE.A6`, `PDD.B3`, `GAD.C2` | GAD.C2 only when linked to worry. |
| `self_worth` | `MDE.A7`, `PDD.B4` | v1 merged guilt and self-esteem; DSM separates them. |
| `concentration` | `MDE.A8`, `PDD.B5`, `GAD.C3`, `ANXD.3` | Related but different: MANIA.B5 distractibility. |
| `psychomotor` | `MDE.A5`, `GAD.C1`, `ANXD.2` | Observable slowing or agitation → MDE.A5; felt restlessness → GAD.C1/ANXD.2. |
| `elevated_mood` | `MANIA.A`, `HYPO.A` | Mood part of criterion A. |
| `irritability` | `MANIA.A`, `HYPO.A`, `GAD.C4` | A distinct irritable period → MANIA.A/HYPO.A; ongoing irritability with worry → GAD.C4. |
| `racing_thoughts_pressured_speech` | `MANIA.B3`, `MANIA.B4` | Split into two DSM items. |
| `increased_goal_directed_activity_risky_behaviour` | `MANIA.B6`, `MANIA.B7`, `MANIA.A` | Split; the energy part belongs to criterion A. |
| `grandiosity` | `MANIA.B1` |  |
| `worry` | `GAD.A`, `GAD.B` |  |
| `restlessness_tension` | `GAD.C1`, `GAD.C5`, `ANXD.1`, `ANXD.2` |  |
| `social_fear_avoidance` | `SAD.A`, `SAD.B`, `SAD.C`, `SAD.D` | Split: fear → SAD.A–C; avoidance → SAD.D. Avoidance alone is not SAD.A. |
| `performance_fear` | `SAD.A`, `SAD.PERF` |  |
| `social_functioning` | impairment evidence (`imp.social`) | No DSM symptom equivalent: impairment evidence for each condition's impairment criterion. |
| `academic_work_functioning` | impairment evidence (`imp.occupational`) | No DSM symptom equivalent: impairment evidence. |
| `self_care` | impairment evidence (`imp.other`) | No DSM symptom equivalent: impairment evidence. |

## Questionnaire items

PHQ-9 and GAD-7 stay as separate questionnaires, with their own AI estimate calls. Their items map to DSM items for cross-reference only. The PHQ-9 order is not the DSM order.

| Instrument item | DSM items |
|---|---|
| phq9 1 | MDE.A2 |
| phq9 2 | MDE.A1 |
| phq9 3 | MDE.A4 |
| phq9 4 | MDE.A6 |
| phq9 5 | MDE.A3 |
| phq9 6 | MDE.A7 |
| phq9 7 | MDE.A8 |
| phq9 8 | MDE.A5 |
| phq9 9 | MDE.A9 |
| gad7 1 | GAD.A, GAD.C1 |
| gad7 2 | GAD.B |
| gad7 3 | GAD.A |
| gad7 4 | GAD.C1, GAD.C5 |
| gad7 5 | GAD.C1 |
| gad7 6 | GAD.C4 |
| gad7 7 | ANXD.4 |
| asrm 1 | MANIA.A |
| asrm 2 | MANIA.B1 |
| asrm 3 | MANIA.B2 |
| asrm 4 | MANIA.B3 |
| asrm 5 | MANIA.B6 |

PHQ-9 item 9 maps to `MDE.A9` and is clinician-entered only. `MDE.A9` counts toward the depressive item count only from clinician-entered PHQ-9 item 9 (≥ 1) or a clinician-accepted suicidal-thoughts or self-harm statement.

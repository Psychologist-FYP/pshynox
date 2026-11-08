# Vignette set (clinician-labelled synthetic cases)

This folder holds the written cases used to evaluate the bipolar, GAD, social-anxiety and depressive-episode hypotheses.
LongCounsel-8 covers depression only, so these cases are the only planned evaluation for the other three conditions.

```text
<case_id>/case.json            synthetic case metadata (no labels, no diagnosis words in the summary)
<case_id>/sessions/s1.json     ordered sessions: {"session_index", "days_since_first", "turns": [{"turn_id", "speaker", "text"}]}
<case_id>/labels.json          consultant labels, written without seeing model output
```

How to write cases and labels, the planned case mix and the blinding rules are in
[`docs/VIGNETTE_AUTHORING_GUIDE.md`](../../../docs/VIGNETTE_AUTHORING_GUIDE.md).

```bash
python -m services.progress.evaluation.vignettes validate          # schema + registry checks, case-mix summary
python -m services.progress.evaluation.vignettes run --run-id vig-v1   # GPU; resumable; never reads labels.json
python -m services.progress.evaluation.vignettes report --run-id vig-v1
```

Only this README is tracked by Git. Case folders stay local until the team agrees where labelled cases are stored and
who may see the labels (they must stay hidden from anyone tuning prompts). Runs are written to `data/research/vignette-runs/`.
No case content or labels exist yet; the only example is the fictional test fixture in `tests/fixtures/vignettes_mini/`.

# Reviewing AI retrieval suggestions

For the subsequent Anas bulk acceptance, label audit, direct-evidence metrics,
threshold experiments and blind final packet, see [review experiments](RETRIEVAL_REVIEW_EXPERIMENTS.md).

The initial development-only contribution uses policy `retrieval-ai-v1`. It contains
four new queries per development pilot conversation, separate from all original
human queries and drafts. No original query revisions were necessary; the new
questions are separate alternatives to the generic starter prompts. No category
substitutions were needed. No test transcripts, audio, reference notes, behavioral
labels, rankings, or benchmark scores informed this contribution.

The Codex session read complete conversations and authored each turn's grade vector
and evidence description. Local serialization expanded those authored judgments
into explicit query–turn records; it did not use keywords or retrieval scores to
assign grades. A serialization helper and session authoring inputs are retained
locally under ignored `data/research/ai-authoring/`. Checkpoints are the completed
JSON files, not requests to another model or API.

Policy rubric: 2 supplies all or a substantive part of the requested answer;
1 materially interprets or qualifies it; 0 contributes no evidence to the defined
question. Clinician reflections are distinguished from new client reports;
conditional options are distinguished from agreed or completed actions. Short
responses and fragments can supply direct evidence when their referent is clear.
Each record contains an authored turn description and its relation to the query;
query intent defines inclusion and exclusion boundaries. Flagged interpretations
and dependent context IDs require human scrutiny.

Files use the existing per-conversation collaboration convention, with a separate
contributor namespace: `data/research/ai-suggestions/annomi/<conversation-id>.json`.
Their provenance includes dataset version/hash, transcript hash, query revision,
policy, generation time, model label and unknown fields where exact model version
or reasoning setting was unavailable. Immutable AI suggestions stay pending even
when a separate human judgment accepts them. This session's validation is an
**AI consistency check**, not independent review.

## Review in the application

1. Run `./scripts/run.py`, open `/workspace/research`, and choose `annomi` and a
   development conversation. Start with **annomi-24**, then **annomi-50**.
2. The **AI suggestions · human review pending** panel appears above the existing
   human editor. Its counts distinguish AI suggestions from explicit human reviews.
   Read the complete transcript before accepting suggestions.
3. Enter your real reviewer identifier in the human query panel. Select an AI query
   and turn in the AI panel. Read the intent, original suggestion, ambiguity warning
   and context. Accept the grade/rationale or edit them, then press **Accept or save
   edited suggestion & next**. Each click records a human action; there is no bulk
   approval. Existing human judgments cannot be overwritten by this endpoint.
4. Reload saved progress and select that new query in the normal human editor.
   Explicitly approve the wording and intended evidence. Review every turn; modify
   accepted judgments using the normal editor when needed. Editing query wording
   invalidates its old grades and makes the original AI suggestions stale for that
   query; re-annotate the revised query rather than reusing those suggestions.
5. For an all-zero question, independently verify the full transcript, then use the
   existing explicit no-answer confirmation. AI all-zero suggestions never supply
   that confirmation. Original unfinished starter queries still block final export;
   these suggestions do not silently replace or approve those queries.

AI originals remain in separate JSON files; accepted/edited human judgments live
in normal conversation annotation files with reviewer, time, action and an AI
reference. Both survive restarts. Importing benchmark results does not replace
annotation or suggestion files. For transfer to another checkout, preserve both
folders with their dataset; stale transcript/policy/query revisions are rejected.
Repeated authoring with the same hashes/revisions reuses the checkpoint. Do not
mix overlapping contributions into a single file or remove AI provenance.

## Suggested review priorities

- **annomi-24**: information-seeking is not commitment to quit; coworker support is
  only a possibility. Its two existing human-reviewed queries remain intact.
- **annomi-50**: “get up the mid-morning” appears mistranscribed. Later dialogue
  supports trying to drop that cup, but the proposed change has not occurred.
- **annomi-97**: the 8:30 visit is proposed, not demonstrably booked; schedule
  changes remain tentative. Blood pressure causation is the client's attribution.
- **annomi-103**: looking at classes is not attendance; concern about rejection is
  not a diagnosis. Several transcript fragments are ordered across speaker turns.
- **annomi-19**: desire to stop shoplifting coexists with enjoyment and no clear
  method. Do not infer charges or a restitution agreement.
- **annomi-3**: sweating away harmful intake is a client belief, not a medical fact;
  accepting information does not establish diabetes or an agreed diet change.
- **annomi-40**: the opening three months is clarified as time since meth use;
  the ex-boyfriend is identified as a dealer, not every former friend.

Before reporting retrieval results, humans should review every included query and
turn, all ambiguity flags, and every absent-answer candidate. A second human should
check a sample and resolve disagreements, especially contextual positives and
no-answer cases. Keep held-out test annotation independent of these AI development
suggestions. Final evaluation retains the original human-completeness gate. No
AI-only benchmark was run.

## Full-pilot extension and exploratory evaluation

On 3 November 2026, the user explicitly expanded the scope to finish all ten pilot
conversations. Separate AI checkpoints now also cover `annomi-23`, `annomi-87` and
`annomi-21` (the original test split). The original development checkpoints and all
human annotation files remain unchanged. Dataset versions, IDs and split assignments
are preserved. These three test conversations are now AI-labelled exploratory data;
they cannot serve as an independent human-labelled test of this contribution.
The original development-only scope above describes the initial contribution.

The completed AI pilot has 40 queries and 2,416 query–turn suggestions. No nonpilot
conversation or audio was processed. Generic starter queries remain preserved;
the AI contribution supplies four separate fully graded questions per conversation.
It does not complete their human approval or replace existing human queries.

`python -m services.retrieval.ai_evaluation` exports the AI-only snapshots to
`data/research/ai-exploratory-evaluation/`. These exports retain `draft: true`,
`human_review_status: pending`, AI provenance and false human no-answer confirmations.
They are explicitly named **AI-labelled exploratory evaluation** and do not pass
through or loosen the normal human-validation export gate. The benchmark measures
agreement with these AI suggestions, not human-validated or clinical accuracy.
Use the same settings for both splits; do not tune settings on test-pilot results.

Review `annomi-87`'s ten-drink statement carefully: its Thursday-through-Sunday
wording does not clearly establish a per-day or whole-weekend total. In `annomi-23`,
negative judgments of the client by others are feared beliefs, not verified opinions.
In `annomi-21`, next-party abstinence and future follow-up are plans, not observed
outcomes; holding a drink is distinguished from actually consuming it. Full-pilot
counts and preservation checks are saved in `pilot-validation.json` alongside the
exploratory benchmark artifacts. Saved checkpoints are reused on reruns.

The pilot comparison uses BM25, TF-IDF, multilingual E5 and hybrid RRF, with three
warm repeats and the existing default settings. Reports are saved as
`data/research/ai-exploratory-evaluation/dev-results.json` and `test-results.json`.
The test run uses `frozen-dev-config.json` from the completed development report.
No threshold was selected and no settings were tuned against test results.
Review the generated `SUMMARY.md` in that folder for the comparison and caveats.

To reproduce the already completed comparison when needed (these are exploratory
exports, not the normal human-validation workflow):

```bash
.venv/bin/python -m services.retrieval.ai_evaluation
.venv/bin/python -m services.retrieval.benchmark \
  --dataset data/research/ai-exploratory-evaluation/dev-dataset.json --split dev \
  --methods bm25 tfidf e5 hybrid --repeats 3 \
  --output data/research/ai-exploratory-evaluation/dev-results.json
.venv/bin/python -m services.retrieval.benchmark \
  --dataset data/research/ai-exploratory-evaluation/test-dataset.json --split test \
  --methods bm25 tfidf e5 hybrid --repeats 3 \
  --config data/research/ai-exploratory-evaluation/frozen-dev-config.json \
  --output data/research/ai-exploratory-evaluation/test-results.json
```

Keep the matching frozen config with the development report. If you deliberately
change development settings for a later experiment, create a new report directory
and matching frozen configuration rather than overwriting this experiment.

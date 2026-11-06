# Structured psychology notes: v3 review guide

## Current behavior

Qwen remains the default local writer. The pipeline retains source-linked overview
extraction, predefined retrieval and bounded follow-up searches (the UI default), then
section drafting. Omi remains optional in the backend; its previously incompatible
local GGUF has not been repaired or tested here. No new model, fine-tuning, context-size
increase or refinement pass was added.

Overview and draft items now carry a type, original source IDs, attribution, timeframe,
uncertainty/negation, and—when applicable—formulation basis and missing information.
The eight types distinguish client report, clinician observation, documented assessment,
AI-proposed formulation, intervention discussed/delivered, proposed plan, agreed plan,
and unresolved information. Overview hints remain fallible: each writer input also
contains the original source text and roles. Prior draft statements are supplied to
reduce repetition. Exact duplicate statements retain the union of their source IDs.

New versions use seven sections: Subjective, Objective, Assessment, Functioning,
Progress, Interventions, Plan. Old five-section notes remain readable; there is no
migration of existing content. SOAP printing retains S/O/A/P and uses supplements for
interventions, functioning and progress. Empty Objective and Progress have specific
limitations rather than normal findings. Plan explicitly says when no agreed action
was documented. Absence of discussion never establishes absence of risk.

Professional chart voice and section placement are enforced through prompts and
structural type checks. Incorrect item types are withheld with a visible limitation.
Clinician observations/assessments and proposals require clinician sources; agreed
plans require both the proposal and client response. These are structural checks,
not proof of semantic support. In particular, two valid source IDs do not prove that
an acknowledgement constitutes agreement. Clinicians must still review context.

## Formulation review and preservation

An `ai_proposed_formulation` remains outside the recorded Assessment text while pending.
The separate editor panel shows the proposal, basis, uncertainties, original sources
and neighbouring dialogue. A named reviewer can accept, edit/adopt, or reject it.
Acceptance/revision adds the reviewed text to Assessment; rejection excludes it.
Original generated text, sources and the explicit reviewer/action history are retained.
Saving ordinary note text cannot alter that history or silently accept a proposal.
Final note approval is blocked while any formulation is pending. Stale source handling
and the existing clinician review gate remain active.

Regeneration preserves nonempty human-written fields and accepted formulations. A new
formulation can still be offered beside existing human Assessment text, without replacing
that text. The complete generated version is retained for comparison; previous AI fields
remain in the audit history. Ordinary saving cannot delete or forge generated item metadata.

Pending formulations are clearly marked in print/PDF exports. Approved notes use the
human-adopted text without intrusive technical metadata; the original AI provenance
remains available in the editor/history. Old approvals, notes and research annotations
are not rewritten by this change.

## Source review: demo-annomi-102

This conversation is in the **test split**. Its existing transcript and prior generated
artifact were inspected as requested, but it was not regenerated or used as a development
model regression. The findings below are an **AI consistency check**, not human or clinical
validation. The prior draft is `data/note-generation/20c4b18c55c04bc3950c863cd37a2ba5/draft.json`.

The source supports these distinctions:

- Turn 3 expresses interest in resuming exercise and initially mentions motivation.
- Turns 7 and 9 clarify that scheduling is the stronger concern; some motivation difficulty
  is still acknowledged. This clarification should be preserved.
- Turns 11 **and 13** establish the two-hour total including travel. Citing turn 11 alone
  does not support the complete numerical statement.
- Turn 10 is a therapist recollection of prior gym membership, not a direct observation
  or an explicit fresh client confirmation. It should not appear in Objective.
- Turn 10 proposes both three-to-four weekly sessions and daily hour-long exercise.
  These frequencies conflict and must not be silently reconciled.
- Turns 15 and 17 are brief acknowledgements. They do not clearly establish agreement to
  the proposed frequency or duration. Turn 16 proposes talking again tomorrow.
- The previous output incorrectly attributed scheduling explanations to the therapist,
  repeated time barriers, and placed advice in Assessment. It also repeated the client's
  name and used external-narrator phrasing.

Illustrative source-grounded rewrite—**not a newly generated model result**:

**Subjective (facts):** Client expressed interest in resuming exercise, initially citing
motivation but clarifying scheduling as the main difficulty. A one-hour workout was
described as requiring roughly two hours including travel, making three to four weekly
sessions difficult to accommodate. Sources: 3, 7, 9, 11, 13. Prior gym membership was
recalled by the therapist (10); current membership was not explicitly reconfirmed.

**Objective:** No clinician observations documented in the supplied material.

**Proposed formulation (AI interpretation, pending review):** Reported scheduling and
travel demands may limit exercise participation despite stated interest. A feasible
schedule and commitment to a specific action plan remain unclear. Basis: expressed
interest and practical constraints (3, 9, 11, 13), with ambiguous acknowledgements
(15, 17). Missing information: feasible frequency and explicit agreement.

**Interventions:** Urged resumption of exercise and discussed allocating time (10, 14).
This documents what was said; it does not endorse the therapist's criticism of the client.

**Plan:** Hour-long workouts were proposed with inconsistent frequency—three to four
weekly and daily (10). Immediate initiation and follow-up tomorrow were proposed
(14, 16). No specific action plan documented as agreed.

**Functioning:** Reported difficulty fitting gym attendance into available time (11, 13).

**Progress:** Change over time cannot be established from the available material.

## Verification and runtime cost

At the user's request, **no revised real-model generation was run**. Earlier successful
GPU smoke results belong to v2 and do not validate the new prompts. No clinician scores
or human quality ratings were fabricated. Full development transcripts 3, 19 and 40 were
read to identify appropriate later checks: no observations and explicit information-offer
agreement (3), ambivalence without a clear action plan (19), and reported longitudinal
change plus coping activities (40). Their notes/labels were not changed.

Automated synthetic regressions cover typed routing, report-versus-observation, proposals
without agreement, missing progress despite functioning evidence, insufficient assessment,
required evidence/basis, explicit acceptance/revision/rejection, immutable review history,
stale-source refusal and preservation of human text during regeneration. Browser tests
use mocked generation and check review controls, source display and pending-PDF labels.

There is no refinement pass. For a single evidence chunk, section drafting changes from
five to seven calls. Overview extraction now has richer metadata, with a 1,100-token
output allowance per chunk/call instead of 700. Exact native-token budgeting remains
in force at the existing 4,096-token context; longer inputs may require additional chunks.
Follow-up searches retain the previous bounds (at most two rounds, three requests per
round). Actual v3 latency and semantic quality remain to be measured by the user.

Limitations: type/source validation cannot prove factual support, tentative wording,
semantic deduplication or correct clinical interpretation. Conflicts spanning chunks and
important omissions still require review of the full transcript. If metadata and prior
statements exceed the context budget, generation fails visibly rather than silently
truncating evidence. No clinical accuracy claim is made.

## How to try it

1. Restart the application and refresh the browser.
2. Open **AnnoMI demo 3, 19 or 40** (development conversations); do not use test or blind-final
   conversations for this regression check.
3. Read the transcript and confirm the acknowledgement. Keep **Extended follow-up searches**
   selected, then click **Generate new draft version**.
4. Review the seven sections and their **Sources and attribution** details. Existing human
   fields remain intact; inspect **Generated version and source evidence** for the full new output.
5. In **AI-proposed formulation · clinician review**, enter your reviewer name. Inspect the
   basis and surrounding evidence, then **Accept formulation**, edit and **Save revised
   formulation**, or **Reject formulation**. Save any pending ordinary note edits first.
6. Save the note. Complete clinician review and explicitly approve only after all proposals
   are resolved. **Print / Save as PDF** shows pending AI interpretation distinctly until then.

On the separate development research-note page, use the proposal's accept/reject controls,
then **Save human edits** to record the action. Editing proposal text resets its review
status to pending. Version approval requires resolved proposals and explicit source review.

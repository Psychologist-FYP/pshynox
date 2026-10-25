# SOAP template preview walkthrough

Open the separate preview route:

```text
/soap-preview
```

The page loads the fictional `session-soap-demo-001` fixture and calls the non-persisting preview endpoint:

```text
GET /api/browse/soap-preview/session-soap-demo-001
```

The left side shows the five timestamped transcript segments. The right side shows the ephemeral structured draft.

## How the sections are populated

- **Subjective** uses deterministic lexical seed queries for `worry` and `sleep`, restricted to `CLIENT` segments. The resulting text is extractive and carries evidence IDs.
- **Functioning** uses `sleep` and `meals`, also restricted to `CLIENT` segments.
- **Objective**, **Assessment**, and **Plan** remain empty. The UI renders their placeholder as “Not documented”. No generated text is stored as clinician-entered content.
- **Risk review** is separate from `NoteField`. The historical/negated risk-language segment is displayed as a search candidate with `PENDING` review status.

Evidence chips jump from a note section to the source transcript segment. The trace under each section shows the seed queries, matched evidence IDs, and resulting extractive text.

This is a preview only. It does not create a database note, alter Talha’s note composer, complete risk review, or approve a session. A later clinician-confirmation flow can decide whether reviewed content should be copied into the existing note workflow.

Retrieval is lexical and deterministic. Paraphrases can be missed; semantic or hybrid retrieval is future work.

# Test the fictional retrieval patient

The local app database contains **Retrieval Demo — Fictional Patient**
(`patient-retrieval-demo`). It has two editable sessions with 22 and 6 turns.
All text and timestamps are invented engineering examples. No audio is attached.

Start the app with `./scripts/run.py`, then open **Patients → Retrieval Demo — Fictional Patient**.
Direct paths on your frontend's existing origin:

- Patient: `/workspace/patients/patient-retrieval-demo`
- Initial examples: `/workspace/sessions/session-retrieval-demo-001`
- Follow-up examples: `/workspace/sessions/session-retrieval-demo-002`

Use **Search this session** beside the transcript. The default provider is BM25.
Open **Surrounding conversation** to see neighboring turns, their speakers and
timestamps. Click an excerpt to jump to its source in the transcript.

## Searches to try in the first session

| Query | Expected result in the initial demo text |
| --- | --- |
| `"four hours"` | Client: “I sleep four hours because of night shifts.” Keep the explanation. |
| `anxious` | “I don't feel anxious anymore.” The denial/change wording stays intact. |
| `anxious AND NOT anymore` | No match: this literal Boolean exclusion removes that turn. |
| `"sister has depression"` | A mention about the sister; the app does not convert it into a patient diagnosis. |
| `sleeping` | Clinician's “Have you been sleeping less?” Open context to see the client's “No.” |
| `neend AND nahi` | “Neend nahi aati.” Literal Roman-Urdu matching. |
| `نیند` | “نیند نہیں آتی۔” Literal Urdu matching. |
| `"breathing exercise"` | Clinician intervention with the agreed bedtime routine. |
| `"follow up"` | Clinician's agreed follow-up next week. |
| `"two classes"` | Client's example of effects on studies and tiredness after work. |
| `"felt low"` | Client's wording with the two-week duration and cricket context. |
| `(worry OR exams) AND NOT medication` | Client's worry about exams, with frequency and difficulty stopping. |
| `hallucinations` | No match. This means not retrieved, not that a condition is absent. |
| `ink` | No match with BM25; the old substring fallback matches “drink.” |
| `watercolour` | No match in this session. The word appears only in the follow-up. |
| `"sleep` | A query-syntax error while the transcript remains available. |

In the **follow-up session**, try `watercolour`, `"six hours"`, or
`"breathing exercise"`. Each search stays in its selected session. The two sessions
are independent fictional examples, not a validated longitudinal assessment.

## Test transcript edits and note review

1. In the first session, search `"four hours"` and inspect the matching client turn.
2. Click **Populate / refresh patient excerpts** to create a source-linked draft.
3. Edit that client turn to “I now sleep six hours because my shifts changed.” and save.
4. The evidence refreshes. `"four hours"` now returns no match; `"six hours"` finds
   the edited turn. The segment ID stays the same and its revision increases.
5. The note shows stale sources and requires review/refresh. Changing a role also
   refreshes the index and preserves the review gates.

The seed command preserves existing edits and notes. It does not reset the demo:

```bash
.venv/bin/python scripts/seed_retrieval_demo.py
```

It defaults to `DATABASE_URL` or `data/pshynox.db`. If your app uses another database,
provide the same value with `--database-url sqlite:////absolute/path/to/your.db`.
Only reserved demo records are inserted; other patient records are untouched.

Before editing the initial examples, you can repeat the read-only BM25 API checks:

```bash
.venv/bin/python scripts/verify_retrieval_demo.py
```

The report is `data/retrieval-demo-checks.json`. These expected-result checks assume
the original demo wording; intentional edits can change their results.

## Compare retrieval methods

The UI uses the configured provider. Set `RETRIEVAL_METHOD` to `bm25`, `tfidf`,
`fixture`, `e5`, or `hybrid` in your backend environment and restart the API to
change it. Dense dependencies and the pinned E5 model were installed/cached in this
workspace. Full setup and filters are documented in [RETRIEVAL.md](RETRIEVAL.md).

For a repeatable comparison with labeled fictional engineering cases:

```bash
.venv/bin/python -m services.retrieval.benchmark \
  --dataset tests/retrieval/fixtures/engineering.json \
  --output data/retrieval-engineering-benchmark.json --require-all
```

The benchmark fixture is separate from these editable patient records, so your UI
experiments do not change its expected results. PriMock and AnnoMI files are now present and imported in the separate research
workspace. Human-reviewed relevance labels are still needed for dataset evaluation.
Open `/workspace/research` and follow [the research guide](RESEARCH_EVALUATION.md).
All 40 AnnoMI suggestions remain unapproved drafts; no labels were fabricated.

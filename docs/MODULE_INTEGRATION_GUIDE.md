# Pshynox — Module A/B Integration Guide

**Audience:** Anas Faiz (Module A), Muhammad Ayyan (Module B), and Muhammad Talha (Module C / integration owner).  
**Repository:** https://github.com/Psychologist-FYP/pshynox  
**Purpose:** Enable A and B to develop independently while preserving the shared contracts and making incremental pull requests into `main`.

> This guide documents the currently visible repository interfaces and the intended module responsibilities. Items explicitly labelled **proposed** require agreement in a pull request before being treated as an implemented API.

## 1. Architectural rule

Pshynox is a local modular monolith, not a set of separately deployed microservices:

```text
                React UI (apps/web/)
                         |
                    /api/v1 HTTP
                         |
               FastAPI orchestrator
                  (services/api/)
                         |
           +-------------+-------------+
           |             |             |
       Module A       Module B       Module C
   services/speech/ services/retrieval/ app + DB
           |             |
           +------ contracts/fixtures --+
```

- **A and B are Python capabilities, not separate end-user web apps.** Do not create separate React/Vite frontends for them.
- Module C owns the UI, API boundary, session IDs, persistence, user-facing errors, job/status workflow and integration points.
- A and B must also be callable and fixture-tested without the browser or a live API, as required by the project plan.
- The UI communicates with FastAPI only. A/B should not write SQLite records directly, access React state, or call the hosted internet from the runtime path.

## 2. Current repository boundary

| Concern                       | Current location                 | Rule for module PRs                                                                            |
| ----------------------------- | -------------------------------- | ---------------------------------------------------------------------------------------------- |
| Pydantic contract definitions | `services/api/schemas.py`        | Use the existing models; do not duplicate or change them unilaterally                          |
| Fictional JSON fixtures       | `packages/contracts/fixtures/`   | Add/update fictional fixtures with a clear purpose and stable IDs                              |
| FastAPI routes/orchestration  | `services/api/main.py`           | Module PRs should not bypass validation/persistence or create parallel APIs                    |
| Current retrieval seam        | `services/api/retrieval.py`      | Existing provider contract is `search(query, session_id, segments, k=10) -> list[EvidenceHit]` |
| SQLite and ORM                | `services/api/db.py`, `alembic/` | Module A/B should return data; Module C persists it through the API/service layer              |
| Backend tests                 | `tests/`                         | Add focused module tests and keep the full suite green                                         |
| Frontend                      | `apps/web/`                      | Module A/B should not add independent user-facing frontends                                    |

The repository includes a deterministic offline lexical browse scorer, fuzzy retrieval under `services/retrieval/fuzzy.py`, and a fixture-backed SOAP preview. Paraphrases can be missed; semantic or hybrid retrieval is future work. `FixtureRetrievalProvider` remains the separate `/api/v1` fallback seam for the existing session evidence route.

## 3. Shared contract definitions

The canonical Pydantic models currently live in `services/api/schemas.py`. The most important ones are below. The actual code is authoritative; this guide is a summary.

### TranscriptSegment

```json
{
  "id": "seg-001",
  "session_id": "session-001",
  "start_ms": 0,
  "end_ms": 1200,
  "speaker_role": "UNKNOWN",
  "text": "Fictional example only.",
  "revision": 1
}
```

Constraints:

- `id` and `session_id` are non-empty strings.
- `start_ms` and `end_ms` are non-negative integer milliseconds, with `end_ms >= start_ms`.
- `speaker_role` is exactly `CLIENT`, `CLINICIAN`, or `UNKNOWN`.
- `revision` is an integer greater than zero.
- Stable segment IDs are retained during text/speaker correction and index refresh.
- `session_id` must identify the active session. The current plan's example entry point is `transcribe(audio_path) -> TranscriptSegment[]`; because the shared segment model also requires `session_id`, the integration PR must clearly specify whether the API adapter binds the active session ID or passes it into the adapter. Do not change the contract ad hoc.

### EvidenceHit

```json
{
  "segment_id": "seg-001",
  "session_id": "session-001",
  "start_ms": 0,
  "end_ms": 1200,
  "excerpt": "Fictional example only.",
  "score": 1.0,
  "section": "subjective",
  "query": "example query"
}
```

Constraints:

- `segment_id` must reference a transcript segment in the same `session_id`.
- `start_ms`, `end_ms`, and excerpt must be grounded in that source; source IDs must be usable by the UI to jump to the right segment.
- `score` is a retrieval ranking score only, never a clinical risk score.
- A result list may legitimately be empty. Empty search results are not proof that a symptom, event or risk is absent.

### Other shared contracts

- `Session`: `id`, `pseudonym_id`, `session_type`, `consent`, `workflow_status`, `created_at`. `session_type` is `INITIAL_ASSESSMENT` or `FOLLOW_UP`.
- `NoteField`: `section`, `text`, `provenance`, `evidence_ids`, `review_status`. Provenance is `MANUAL`, `RETRIEVAL_CANDIDATE`, or `TEMPLATE`.
- Note sections currently supported: `subjective`, `objective`, `assessment`, `plan`, `functioning`.
- `evidence_ids` refer to source transcript segment IDs, not arbitrary excerpts.
- The API validates evidence IDs against the active session; do not weaken this check to make a module pass.

All request and response JSON uses `snake_case` under the `/api/v1` API prefix. See <http://127.0.0.1:8000/docs> when running locally.

## 4. Module A — Anas: Speech Processing

### Responsibilities

Implement the local audio processing capability described in the FYP plan:

1. Validate the supported input audio and provide useful errors for missing, unreadable, unsupported or empty input.
2. Decode/resample as required using the chosen and pinned local dependencies (e.g. FFmpeg plus the team-selected offline ASR baseline).
3. Produce timestamped transcript segments with stable IDs and explicit speaker-role values.
4. Support manual speaker-role correction in the transcript workflow by preserving the shared segment contract; actual edits/persistence remain Module C responsibilities.
5. Provide a Python CLI/function and documented fixture-based tests so it can be exercised without the web UI.
6. Document local model installation/setup, license, hardware requirements, measured latency/resource use, and known language limitations. Do not commit model weights or recordings.

### Intended package

```text
services/speech/
  __init__.py
  transcriber.py       # proposed: transcription entry point
  audio.py             # proposed: validation/decode/resample helpers
  README.md            # setup, model choice, CLI, limitations

tests/speech/          # proposed module tests and synthetic fixtures only
```

Names other than the package path are suggestions, not existing paths. Keep the public entry point small and documented. Expected contract from the implementation plan:

```python
transcribe(audio_path) -> list[TranscriptSegment]
```

Resolve how `session_id` is attached to each returned segment with Talha before merging. Do not return fabricated transcript text for invalid audio. If the local ASR runtime/model is unavailable, return a documented error rather than silently switching to a hosted service.

### Integration sequence

1. Deliver a standalone CLI smoke test that can process a short fictional/team-authored clip.
2. Return validated `TranscriptSegment` data and a documented error shape.
3. Open a PR containing package code, tests, dependency changes and a short README.
4. In a separate or agreed follow-up integration commit, Module C wires the service into the API job/session workflow and persists the returned segments.
5. Verify text/speaker edits persist and that Module B can refresh its index afterward.

**Proposed API orchestration endpoint:** `POST /api/v1/sessions/{session_id}/transcribe` may trigger the local pipeline; this route is not listed as implemented in the current README. Agree its request/response and job semantics before implementing it. Do not call Module A directly from React.

## 5. Module B — Ayyan: Information Retrieval

### Responsibilities

1. Port/adapt the existing 31-segment, 7-session fictional coursework corpus and five existing test queries, preserving corpus provenance.
2. Build and refresh a per-session local inverted index over the current transcript segments.
3. Implement BM25 ranking and the agreed Boolean/metadata filters (session/client, note section, speaker/time constraints as defined together).
4. Return top-k `EvidenceHit` values with source IDs and timestamps traceable to the exact stored segments.
5. Test ranking against the existing query set; report relevant hits/misses and measured precision/recall metrics without overstating the small pilot.
6. Provide standalone Python CLI functions and fixture-based tests. No UI, cloud search or clinical risk/diagnosis inference is in scope.

### Intended package

```text
services/retrieval/
  __init__.py
  index.py             # proposed: session-local index build/refresh
  search.py            # proposed: BM25 and Boolean/field filtering
  README.md            # corpus, CLI, index lifecycle, query tests

tests/retrieval/       # proposed module tests
```

### Compatibility with the current API

The currently checked-in adapter contract in `services/api/retrieval.py` is:

```python
search(query, session_id, segments, k=10) -> list[EvidenceHit]
```

It takes current `TranscriptSegment` values as input and returns the `EvidenceHit` schema. Keep this integration seam stable for the first Module B PR. Ayyan can implement the real engine under `services/retrieval/`; a thin adapter can call it. Any necessary signature/index-lifecycle change must be proposed to Talha and reviewed before merging.

The index must be rebuilt/refreshed when the transcript changes. A successful transcript edit increments `revision`; do not return stale excerpts or IDs. The UI expects each returned source to map to a transcript segment in the currently open session. Do not return hits from another session.

### Integration sequence

1. Expose a standalone `index(session_id, segments)` and query/search function with documented inputs/errors. The exact function shape should be included in the module README and agreed in the PR.
2. Test output against the supplied/owned fictional corpus and query set.
3. Open a PR with no unrelated changes to Module C routes or UI.
4. Connect through the existing API retrieval adapter with a fixture fallback still available for deterministic UI demos.
5. Test top-k results, filters, empty queries, empty results, stable source IDs, and index refresh after transcript edits.

## 6. Module C — Talha: integration ownership

Module C remains responsible for:

- React UI and user-visible statuses/errors;
- session lifecycle, local API endpoints, SQLite persistence and migrations;
- validating Module A outputs before storing them;
- preserving stable segment IDs and revisions on manual corrections;
- passing the current transcript and scope to Module B, validating every evidence source ID, and persisting/retrieving note drafts;
- consent metadata, explicit clinician risk review, approval/rejection guards and audit events;
- keeping deterministic fixture modes available when A or B is unavailable.

Module C should not duplicate the ASR/IR algorithms inside API route functions. Keep route handlers thin and delegate to module service interfaces.

## 7. Git workflow and merge rules

### Start cleanly from `main`

Run from the repository root:

```powershell
git switch main
git pull origin main
git switch -c feat/speech-pipeline
# or: git switch -c feat/ir-bm25
```

Replace branch names with a short, specific task. Do not work directly on `main` and do not start a long-lived branch from another teammate's feature branch unless explicitly coordinating that dependency.

### Before a pull request

```powershell
py -m pytest
py -m alembic upgrade head
git diff --check
```

For frontend-impacting changes, also run:

```powershell
cd apps\web
npm run build
```

Then inspect `git status`, confirm no `.env`, local SQLite database, recordings, model binaries, credentials or private clinical content are staged, and document the exact test commands/results in the PR.

### PR scope and review

- One main-purpose change per PR; include tests and a concise README for each module.
- Module A interface/contract changes require review from A and C owners.
- Module B interface/contract changes require review from B and C owners; retrieval provenance/index integrity must be checked.
- Breaking changes to `services/api/schemas.py` or the JSON fixtures must be reviewed before dependent code is merged.
- Merge only after CI/checks pass and at least one teammate has reviewed the PR. Protect `main` in GitHub settings if the repository permissions allow it.
- After merging, each teammate updates their branch from `main` and runs their module tests plus the project test suite.

## 8. Acceptance checklist for each module PR

### Module A

- [ ] Python entry point and CLI work independently of the web frontend.
- [ ] A short fictional audio clip can be processed with the documented local runtime/model.
- [ ] Output validates as `TranscriptSegment[]`, with valid millisecond ranges and roles.
- [ ] Missing, invalid and empty audio paths fail clearly without fabricated output.
- [ ] Model download/setup, license, runtime and limitations are documented.
- [ ] Fixture/unit tests pass; no audio/model files are committed.

### Module B

- [ ] Index is scoped by session and rebuild/refresh is documented.
- [ ] BM25 ranking and required Boolean/metadata filters work.
- [ ] Existing 31-segment / 7-session corpus and five queries are tested where available.
- [ ] Every `EvidenceHit` resolves to the correct `TranscriptSegment` and session.
- [ ] Empty results are returned as empty results, never interpreted as a safety conclusion.
- [ ] Fixture/unit tests pass and relevance results are reported honestly.

### Module C integration

- [ ] API accepts module output and validates it against the shared contract.
- [ ] Transcript segments and edits persist across API restart.
- [ ] Evidence refreshes after transcript edits; all references jump to the correct source.
- [ ] Note drafts preserve manual content/provenance and source IDs.
- [ ] Approval remains blocked while risk review is pending.
- [ ] The full demo remains runnable without internet after setup and still has fixture fallback paths.

## 9. Immediate next actions

1. Anas and Ayyan: pull the updated `main`, confirm the contracts above, and report any required changes before coding against them.
2. Each module owner: open a focused feature branch, implement the standalone module plus fixtures/tests/README, then submit a PR.
3. Talha: integrate each module behind its existing boundary; avoid postponing integration to the final demo day.

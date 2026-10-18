# Pshynox

**Offline-first clinical documentation support for psychological sessions.**

Pshynox is a university Final Year Project prototype for reviewing a session transcript, retrieving source-linked excerpts, preparing a structured note, and recording an explicit clinician review before approval.

> **Clinical scope:** This application is a documentation aid. It does not diagnose, autonomously assess risk, detect mental states, or recommend treatment. Search results are evidence candidates, and an empty result does not establish that risk is absent.

## Project status

The current repository contains the Module C application foundation: React/Vite UI, FastAPI orchestration, SQLite persistence, editable session transcripts, a fixture-backed evidence provider, saved note drafts, and a clinician review/approval workflow.

Real local speech processing (Module A) and the real BM25/Boolean retrieval engine (Module B) still need to be integrated and validated. The current retrieval provider is a simple offline fallback, not the finished retrieval engine. New fictional sessions may contain synthetic transcript segments for testing; this is not audio transcription.

This is an MVP prototype. Local SQLite persistence does not by itself provide production encryption, authentication, or clinical-compliance guarantees.

## Architecture

Pshynox is a **local modular monolith**, not a collection of separately deployed microservices. The React frontend communicates with one local FastAPI process, which validates module outputs and owns application persistence and workflow rules.

```text
React + TypeScript + Vite  (apps/web/)
              |
          /api/v1 HTTP
              |
FastAPI orchestration + Pydantic contracts  (services/api/)
              |
       +------+------+------------------+
       |             |                  |
   Module A      Module B          SQLite database
 services/      services/          sessions, transcript,
 speech/        retrieval/         notes and audit events
       \             /
        packages/contracts/fixtures/
```

### Team ownership

| Owner | Module | Responsibility |
|---|---|---|
| Anas Faiz | A — Speech Processing | Local audio decode/resampling, ASR, timestamped transcript segments, documented errors, CLI and performance report |
| Muhammad Ayyan | B — Information Retrieval | Per-session index, BM25 ranking, Boolean/metadata filters, evidence results and relevance tests |
| Muhammad Talha | C — Clinical Application | UI, API orchestration, SQLite persistence, sessions, notes, evidence navigation, consent metadata, clinician review and audit workflow |

A and B are backend capabilities; they do **not** need separate end-user frontend applications. See [`docs/MODULE_INTEGRATION_GUIDE.md`](docs/MODULE_INTEGRATION_GUIDE.md) for exact integration boundaries, contracts and pull-request steps.

### Repository map

```text
apps/web/                         React + TypeScript + Vite
services/api/                     FastAPI routes, schemas, orchestration, DB and retrieval adapter
services/speech/                  Module A package to be added if absent
services/retrieval/               Module B engine package to be added if absent
packages/contracts/fixtures/      Canonical fictional JSON fixtures
alembic/                           Database migrations
tests/                             Backend and contract tests
docs/                              Architecture and integration documentation
```

The speech/retrieval packages and `docs/` are intended integration locations; check current `main` before creating them.

## Quick start (Windows PowerShell)

Requirements: Python 3.11 or compatible version supported in `pyproject.toml`, Node.js/npm, and Git.

From the repository root:

```powershell
git pull origin main
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
py -m alembic upgrade head
py -m uvicorn services.api.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd apps\web
npm install
npm run dev -- --host 127.0.0.1 --port 5173
```

Open the UI at <http://127.0.0.1:5173/>. API health is at <http://127.0.0.1:8000/api/v1/health>; interactive OpenAPI docs are at <http://127.0.0.1:8000/docs>. Vite proxies `/api` calls to FastAPI on port `8000`.

## Configuration and local data

`.env.example` documents the current process settings:

```dotenv
APP_ENV=development
FIXTURE_MODE=true
DATABASE_URL=sqlite:///./data/pshynox.db
LOCAL_DATA_DIR=./data
```

The current application reads process environment variables; do not assume a `.env` file is loaded automatically. In PowerShell, set an override in the same terminal before starting the API, e.g. `$env:FIXTURE_MODE = "false"`.

- `FIXTURE_MODE=true` enables the checked-in fictional sample and fixture-compatible behavior.
- `FIXTURE_MODE=false` exercises the persisted-only path.
- SQLite defaults to `data/pshynox.db`; use a separate temporary database for isolated persistence tests.
- The API applies migrations at startup; migrations can also be applied explicitly with `py -m alembic upgrade head`.

Keep `.env`, local databases, recordings, model binaries, caches, credentials and confidential notes out of Git.

## Development checks

Run from the repository root:

```powershell
py -m pytest
py -m alembic upgrade head
git diff --check
```

For the frontend build and TypeScript checks:

```powershell
cd apps\web
npm run build
```

Before opening a pull request, run relevant module tests plus the full checks and include actual commands/results in the PR description.

## API overview

All endpoints are versioned under `/api/v1`; request/response JSON uses `snake_case`.

| Capability | Routes |
|---|---|
| Health | `GET /health` |
| Sessions | `GET /sessions`, `POST /sessions`, `GET /sessions/{session_id}` |
| Transcript | `GET /sessions/{session_id}/transcript`, `PATCH /sessions/{session_id}/transcript/{segment_id}` |
| Evidence | `GET /sessions/{session_id}/evidence?query=...` |
| Notes | `GET` and `PUT /sessions/{session_id}/note-draft` |
| Review and audit | `GET /sessions/{session_id}/review-state`, `POST /sessions/{session_id}/risk-review/complete`, `POST /sessions/{session_id}/approve`, `POST /sessions/{session_id}/reject`, `GET /sessions/{session_id}/audit-events` |

Use `/docs` for full request/response schemas and error definitions. Review and approval guards must be enforced server-side, not only by the frontend.

## Shared contracts — freeze before integration

The current canonical Pydantic models are in `services/api/schemas.py`; fictional example payloads are under `packages/contracts/fixtures/`. Use these rather than making module-specific copies. Moving contracts to a neutral package can be proposed later as a coordinated change.

- **`TranscriptSegment`**: `id`, `session_id`, `start_ms`, `end_ms`, `speaker_role`, `text`, `revision`. Roles: `CLIENT`, `CLINICIAN`, `UNKNOWN`. IDs remain stable when text or speaker labels are corrected.
- **`EvidenceHit`**: `segment_id`, `session_id`, `start_ms`, `end_ms`, `excerpt`, `score`, `section`, `query`. The source ID must resolve to a transcript segment in the same session; scores are retrieval scores, not clinical risk scores.
- **`Session`**: `id`, `pseudonym_id`, `session_type`, `consent`, `workflow_status`, `created_at`. Session types: `INITIAL_ASSESSMENT`, `FOLLOW_UP`.
- **`NoteField`**: `section`, `text`, `provenance`, `evidence_ids`, `review_status`. Provenance: `MANUAL`, `RETRIEVAL_CANDIDATE`, `TEMPLATE`. Supported sections: `subjective`, `objective`, `assessment`, `plan`, `functioning`.

Evidence references must resolve to source segments from the same session. Retrieval suggestions must not silently overwrite clinician-entered content. Risk review starts as `PENDING`; approval requires a saved note and explicit completed review. Contract changes require a reviewed PR before dependent module code is merged.

## End-to-end milestone target

1. Create/open a fictional session and record consent metadata.
2. Import a short fictional audio recording and run local speech processing.
3. Review timestamped segments and manually correct text/speaker roles.
4. Search the current transcript and inspect ranked source-linked excerpts.
5. Compose a structured note, keeping undocumented content blank/not documented.
6. Save and reopen the note after restarting the application.
7. Confirm approval is blocked while review is pending; complete clinician review and then approve, or reject with a reason.
8. Inspect audit events and demonstrate invalid-audio/zero-result error paths without invented clinical data.

The final MVP must work without networking after setup. The fixture path must remain available when either module is not yet integrated.

## Git workflow and data policy

- Do not work directly on `main`. Start from the latest `main`, create a short-lived branch (e.g. `feat/speech-pipeline`, `feat/ir-bm25`, `feat/clinical-*`), and open a pull request.
- One focused purpose per PR; include tests, a short module README and actual test results.
- Ask at least one teammate to review. Contract changes require review from all affected module owners. Protect `main` in GitHub settings if permissions allow.
- Use fictional data only in tests, fixtures, CI and demo assets. Never commit real recordings, patient identities, private notes, model binaries, credentials or local DB files.
- Document dataset source, license, consent/limitations and permitted use before using an external dataset. Do not scrape therapist/client conversations indiscriminately.
- Do not make the MVP depend on a hosted model/API at runtime or send session content to an external service without an explicit, reviewed design decision.

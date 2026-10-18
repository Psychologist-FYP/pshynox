# Psychologist-FYP Module C

Offline-first clinical documentation MVP owned by Module C. The application is
documentation support only; it does not diagnose, assess risk, or recommend
treatment.

## Quick start

### Backend

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -e ".[dev]"
py -m uvicorn services.api.main:app --reload
```

The API is available at `http://127.0.0.1:8000`, with OpenAPI docs at
`/docs`. The health endpoint is `/api/v1/health`.

### Frontend

```powershell
cd apps\web
npm install
npm run dev
```

The Vite development server proxies `/api` to the local FastAPI process.

### Tests

```powershell
pytest
cd apps\web
npm run build
```

The checked-in data is fictional and exists only for contract and UI smoke
testing. Real recordings, identities, credentials, model files, and local
database files must not be committed.

## Local persistence

The API uses SQLite at `data/pshynox.db` by default. Apply the migration with:

```powershell
py -m alembic upgrade head
```

Set `FIXTURE_MODE=false` to run the persisted-only path. With fixture mode
enabled, the fictional sample session remains available while newly created
fictional sessions are stored in SQLite. New demo sessions receive synthetic
transcript segments so the correction workflow can be exercised; this is not
audio transcription.

API operations include:

- `GET /api/v1/sessions`
- `POST /api/v1/sessions`
- `GET /api/v1/sessions/{session_id}`
- `GET /api/v1/sessions/{session_id}/transcript`
- `PATCH /api/v1/sessions/{session_id}/transcript/{segment_id}`

An edit body is `{"text":"corrected text","speaker_role":"CLIENT"}`. The
server returns the saved segment and increments `revision` exactly once.

## Contract policy

The API boundary uses `snake_case` JSON and is versioned under `/api/v1`.
`packages/contracts/fixtures` contains canonical fictional examples. Required
contract fields and enum values are changed only through a reviewed integration
change.

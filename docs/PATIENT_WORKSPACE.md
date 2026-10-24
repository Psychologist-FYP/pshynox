# Patient workspace

The workspace is organized around patient records. Add a patient from the workspace
or sidebar, enter available intake details, then open their record to start a
session. The name is required; other intake fields are optional. A date of birth
is used to calculate current age. If it is unknown, the entered age is explicitly
shown as age at intake.

Each patient page provides session history (most recent first), an initial/follow-up
session selector, and editable intake details. Each session retains its existing
recordings, transcript, notes and review workflow. Recording consent is collected
per session; creating a patient does not imply recording consent.

Existing sessions are preserved with no patient assigned. Find them under
“Unassigned sessions” on the workspace, open a session, and link it to a patient.
The API rejects moving an already-linked session to a different patient.

## Persistence and routes

Migration `0006_patients` adds the `patients` table and nullable indexed
`sessions.patient_id` foreign key. Patient names are not unique identifiers.
The migration runs at backend startup and does not invent profiles for existing
sessions or change their transcripts.

- `GET/POST /api/v1/patients`: list/create patient profiles.
- `GET/PUT /api/v1/patients/{patient_id}`: read/update intake details.
- `GET /api/v1/patients/{patient_id}/sessions`: patient-scoped session history.
- `POST /api/v1/sessions` accepts optional `patient_id` and validates it.
- `PATCH /api/v1/sessions/{session_id}/patient`: link an unassigned session.

Frontend routes: `/workspace`, `/workspace/patients/new`,
`/workspace/patients/{id}`, `/workspace/patients/{id}/edit`, and the existing
`/workspace/sessions/{id}`. Patient details and navigation use the current theme.

## Verification

`tests/api/test_patients.py` covers validation, save/reopen, edits, patient-specific
session history, unknown IDs, distinct identities with the same name, and linking
legacy sessions without changing transcript content. The full backend test suite
uses a temporary database. Build the frontend with `npm run build --prefix apps/web`.

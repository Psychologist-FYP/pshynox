# Pshynox

Offline-first psychological session documentation with a React interface, FastAPI,
SQLite and local Whisper transcription. The current workspace supports patient
intake records, session history, microphone recording/WAV import, editable
transcripts, source-linked excerpt drafts and clinician review.

This is an FYP prototype for documentation support. It does not diagnose or
recommend treatment. Use fictional data for development and demonstrations.

## What you need

| Requirement | Purpose |
|---|---|
| Linux, or a Linux distribution inside WSL2 | Current background speech worker uses Linux process/file locking. Native Windows and macOS are not supported by this worker yet. |
| Python **3.11+**, pip and virtual environment support | Backend and tests |
| Node.js/npm | Frontend; development here uses Node **22** |
| Git, curl, C/C++ compiler, make and CMake | Download/build the local speech runtime |
| FFmpeg **and ffprobe** on `PATH` | Validate and decode recordings |
| `whisper.cpp` **v1.8.3** CPU build | Local speech inference |
| Multilingual **`ggml-base.bin`** model | English/Urdu transcription; downloaded separately, excluded from Git |
| Browser and microphone, or a WAV recording | Record or import audio |

A GPU and API key are not required. Internet access is needed for initial installs
and model downloads; transcription runs locally afterward. Jetson deployment
still requires testing on the exact board; these commands describe the laptop setup.

**Dependency files:** [`requirements.txt`](requirements.txt) installs the Python
application and test dependencies through [`pyproject.toml`](pyproject.toml).
Frontend versions are recorded in [`apps/web/package-lock.json`](apps/web/package-lock.json).
System programs, the C++ runtime and model weights require the separate steps below;
`pip install` does not install them.

## First-time setup

Run the following steps once. Commands assume Bash on Linux and start from the
repository root. If you already have this checkout, skip the clone step.

### 1. Install system programs

Ubuntu/Debian (also applicable inside Ubuntu on WSL2):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl build-essential cmake ffmpeg nodejs npm
```

Fedora:

```bash
sudo dnf install -y python3 python3-pip git curl gcc gcc-c++ make cmake ffmpeg-free nodejs npm
```

Check versions before continuing. If your distribution supplies an older Python or
Node version, install a compatible version first. Python must be at least 3.11;
Node 22 is the development version used for this project.

```bash
python3 --version
node --version
npm --version
cmake --version
ffmpeg -version
ffprobe -version
```

On WSL2, run the backend/build/install commands inside Linux and open the UI in
Windows at the localhost address below. Microphone access depends on the browser;
WAV import is available as an alternative.

### 2. Clone and install Python/frontend dependencies

```bash
git clone https://github.com/Psychologist-FYP/pshynox.git
cd pshynox
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm ci --prefix apps/web
```

`requirements.txt` includes the development extra, so it installs `pytest` and
`httpx` as well as FastAPI, SQLAlchemy, Alembic, Pydantic and Uvicorn. It uses the
project's declared version constraints rather than duplicating them.

[`scripts/requirements-laptop-tested.txt`](scripts/requirements-laptop-tested.txt)
is a snapshot of one development environment, including platform-specific
packages. Use the root `requirements.txt` for normal installation; that snapshot
is not a portable Jetson lockfile.

### 3. Build the local speech runtime

From the repository root:

```bash
mkdir -p data/tools
git clone --depth 1 --branch v1.8.3 https://github.com/ggml-org/whisper.cpp.git data/tools/whisper.cpp
cmake -S data/tools/whisper.cpp -B data/tools/whisper.cpp/build \
  -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DWHISPER_BUILD_TESTS=OFF
cmake --build data/tools/whisper.cpp/build --config Release -j 4
```

If `data/tools/whisper.cpp` already exists, skip cloning and run the CMake commands.
The expected executable is:

```text
data/tools/whisper.cpp/build/bin/whisper-cli
```

The tested v1.8.3 source commit is `2eeeba56e9edd762b4b38467bab96c2517163158`.
The source and build stay under the ignored `data/` directory. A fresh checkout
needs its own native build; do not copy x86 binaries to an ARM64 Jetson.

### 4. Download the required local model

**The model is not uploaded to GitHub. Every fresh installation must download it.**
Use multilingual `base`, rather than `base.en`, for English and Urdu.

```bash
mkdir -p models
curl -L --fail --retry 3 \
  -o models/ggml-base.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
sha256sum models/ggml-base.bin
```

The expected SHA-256 is:

```text
60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe
```

The file is about 142 MiB. If its hash does not match, download it again before
using it. Model files belong under `models/` and are ignored by Git, along with
common weight formats placed elsewhere. This download is also documented in
`requirements.txt` because the model is required to transcribe audio.

Optional: produce a smaller quantized model with the runtime you just built:

```bash
data/tools/whisper.cpp/build/bin/whisper-quantize \
  models/ggml-base.bin models/ggml-base-q5_0.bin q5_0
```

To use it, change `WHISPER_MODEL` in `.env` to `./models/ggml-base-q5_0.bin`, then
restart the backend. Quantized-model language quality still needs evaluation.

### 5. Create local configuration

```bash
cp .env.example .env
```

Do this once; keep your existing `.env` if you have already configured it.
The supplied defaults use your saved records, CPU inference and the model/runtime
paths created above. `.env` is private and ignored by Git.

## Run the application

Open **two terminals**. Replace `/path/to/pshynox` with your checkout path. Run the
backend from the repository root so its relative paths resolve correctly.

**Terminal 1 — backend and automatic transcription worker:**

```bash
cd /path/to/pshynox
source .venv/bin/activate
python -m uvicorn services.api.main:app --env-file .env --reload --host 127.0.0.1 --port 8000
```

SQLite migrations run automatically at startup. The backend also starts one local
speech worker; a third terminal is not needed. Restart this terminal after changing
model settings or installing a new runtime. Press `Ctrl+C` to stop it.

**Terminal 2 — frontend:**

```bash
cd /path/to/pshynox/apps/web
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open **<http://127.0.0.1:5173/workspace>** in your browser.

- API health: <http://127.0.0.1:8000/api/v1/health>
- API documentation: <http://127.0.0.1:8000/docs>
- The frontend proxies `/api` requests to backend port `8000`.

On later runs, use just these two terminal commands; dependency installation,
runtime builds and model downloads do not need repeating.

## Use the workspace

1. Select **Add patient**, enter intake details and save. Only the name is required;
   the remaining details can be collected and edited later.
2. Open the patient from the workspace or left sidebar. Their page contains intake
   details, previous sessions and **Start new session**.
3. Choose initial assessment or follow-up and start a session.
4. Confirm recording consent. Select a microphone and record, or import a WAV.
   Each recording can be up to five minutes and 100 MB in this MVP.
5. Click **Transcribe locally** on a saved recording. Every recording has its own
   status; additional transcripts append without replacing earlier text edits.
6. Review the transcript and set speaker labels to `CLIENT`, `CLINICIAN` or
   `UNKNOWN`. Timestamp buttons play the corresponding source recording.
7. Select **Populate / refresh patient excerpts** to fill the draft's subjective
   section from confirmed client excerpts. Review and save the note.
8. Complete clinician review before approval. If source text changes, review or
   refresh the affected excerpts first.

Stereo WAV files are mixed to mono for transcription; originals retain their
channels. This does not identify speakers automatically. A future dedicated
patient/psychologist two-microphone mode requires separate implementation.

Earlier sessions appear under **Unassigned sessions**. Open one and link it to a
patient to include it in their history. See [patient workspace details](docs/PATIENT_WORKSPACE.md).

## Configuration and storage

The backend reads process environment variables. The documented Uvicorn command
loads `.env` explicitly with `--env-file`; plain Python/CLI commands do not load it
automatically. Already-exported environment variables take precedence over `.env`.

| Variable | Default / example | Meaning |
|---|---|---|
| `FIXTURE_MODE` | `false` in `.env.example` | `true` additionally exposes the checked-in demo session |
| `DATABASE_URL` | `sqlite:///./data/pshynox.db` | Local SQLite database |
| `LOCAL_DATA_DIR` | `./data` | Recordings and worker lock files |
| `SPEECH_WORKER_ENABLED` | `true` | Start the local worker with the Linux backend |
| `WHISPER_BINARY` | `./data/tools/whisper.cpp/build/bin/whisper-cli` | Native runtime executable |
| `WHISPER_MODEL` | `./models/ggml-base.bin` | Local multilingual model |
| `WHISPER_LANGUAGE` | `auto` | Automatic detection; `en` or `ur` can be tested explicitly |
| `WHISPER_THREADS` | `4` | CPU inference threads |
| `WHISPER_TIMEOUT_SECONDS` | `1800` | Maximum inference time per recording |

Without a `.env` file, code defaults still enable fixture mode; follow the setup
above to use the persisted-only workspace.

Patient intake, sessions, transcripts, notes and job status are stored in SQLite.
Recordings stay under `data/recordings/`; the DB stores their paths and source
links. Preserve the database and its recordings together when backing up. SQLite
alone does not provide encryption or authenticated access.

`.gitignore` excludes models, local runtime builds, recordings, databases, virtual
environments and `.env`. Check before pushing:

```bash
git check-ignore models/ggml-base.bin models/ggml-base-q5_0.bin data/pshynox.db .env
git status --short
```

## Tests and useful commands

From the repository root with the virtual environment activated:

```bash
python -m pytest -q
npm run build --prefix apps/web
git diff --check
```

Tests use a temporary database and disable the automatic worker. The frontend
build checks TypeScript and writes `apps/web/dist/`. Uvicorn currently serves the
API; the two-terminal instructions above are the supported application run path.

Optional real-model check using the public sample bundled with whisper.cpp:

```bash
python -m services.speech data/tools/whisper.cpp/samples/jfk.wav
python scripts/smoke_speech.py data/tools/whisper.cpp/samples/jfk.wav
```

The CLI uses default model paths or exported environment variables. The integration
smoke test starts a worker, saves transcripts and a draft in a temporary database,
and checks persistence after restart. No personal recording is needed.

To apply migrations explicitly (the default database path):

```bash
python -m alembic upgrade head
```

Alembic does not automatically read `.env`. Export `DATABASE_URL` first if you use
a custom database; the normal Uvicorn startup applies migrations with its loaded
configuration.

## Troubleshooting

| Problem | What to check |
|---|---|
| Missing runtime/model | Complete setup steps 3–4. Verify `.env` paths and restart the backend. |
| Audio cannot be decoded | Confirm both `ffmpeg` and `ffprobe` run in the backend terminal. Use a valid WAV or browser audio recording within the duration/size limits. |
| Microphone access denied | Allow microphone access on localhost in your browser, check the selected input, or import a WAV. |
| Job stays queued | Ensure `SPEECH_WORKER_ENABLED=true`, use Linux/WSL2, and check backend terminal output. Restart the backend if its worker stopped. |
| Job failed during restart | Use **Retry transcription** on the existing recording. |
| Port already in use | Stop the earlier server with `Ctrl+C`; keep backend `8000` and frontend `5173` for these instructions. |
| Frontend cannot reach API | Start both terminals and check the health URL. |
| Changed `.env` has no effect | Restart the backend and check for exported variables overriding the file. |
| Browser shows old UI | Refresh the page after starting the current frontend server. |

## Architecture and project status

```text
React / TypeScript / Vite (apps/web)
                 |
         FastAPI (services/api)
          |               |
     SQLite + audio   local speech worker
                          |
                  whisper.cpp CPU process
```

Module A provides audio preparation and local speech transcription. Module C owns
patient records, sessions, persistence, UI, review and orchestration. The current
evidence provider is still a simple local fallback; the full Module B BM25/Boolean
retrieval engine remains to be integrated. Draft population uses exact excerpts,
so a chat LLM or vector database is not required.

| Owner | Module |
|---|---|
| Anas Faiz | A — Speech Processing |
| Muhammad Ayyan | B — Information Retrieval |
| Muhammad Talha | C — Clinical Application |

The canonical API schemas are in `services/api/schemas.py`, migrations in
`alembic/versions/`, and fictional fixtures in `packages/contracts/fixtures/`.
See [module integration guide](docs/MODULE_INTEGRATION_GUIDE.md) for module
boundaries, and [Plan.txt](Plan.txt) for the implementation plan. Work on a feature
branch and include relevant verification in pull requests. Do not commit personal
records, model weights or local databases.

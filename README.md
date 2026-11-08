# Pshynox

Offline-first documentation support for psychological sessions. It has a React interface, a FastAPI backend, SQLite
storage, local Whisper transcription and a local LLM (Qwen3-4B through llama.cpp). Everything runs on the laptop: no
hosted inference, no API keys, and no network access after setup.

The workspace covers:

- patient intake and session history;
- microphone recording, WAV import and same-Wi-Fi online meetings;
- editable transcripts with speaker roles;
- source-linked evidence retrieval;
- AI note drafts;
- longitudinal progress monitoring with evidence-cited condition hypotheses.

> **FYP prototype.** Pshynox supports documentation. Its condition hypotheses (bipolar, GAD, social anxiety,
> depression) are AI-generated, criterion by criterion with cited transcript turns, for clinician review. They are
> not diagnoses. It does not score risk or recommend treatment. Every AI output is labelled *AI-generated · human
> review pending* until a clinician reviews it. Use fictional or synthetic data only.

**Status:** [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) (dated assessment, latest update 8 November 2026).
**Active branch:** `progress-monitoring`.

---

## Quick start on a new laptop

Requirements: Linux or WSL2 (Ubuntu 22.04+/Debian 12+/Fedora 39+), about **5 GiB** free disk, 8 GB RAM (16 GB
recommended), and internet access for the first setup. An NVIDIA GPU with 4 GB of VRAM or more is optional; the AI
features also run on the CPU, only more slowly.

```bash
git clone https://github.com/Psychologist-FYP/pshynox.git
cd pshynox
git checkout progress-monitoring           # until it is merged into main
./scripts/setup.sh                         # asks once for sudo to install system packages
./scripts/run.py --no-reload
```

Then open **<http://127.0.0.1:5173/workspace>**. Press **Ctrl+C** in that terminal to stop everything.

`scripts/setup.sh` does the whole installation. Each step is skipped when it is already done, so it is safe to re-run.

| Step | What it installs or creates | Size |
|---|---|---|
| 1. System packages | Python 3, venv, git, curl, C/C++ compiler, CMake, FFmpeg, zstd, sqlite3, Node.js/npm (apt or dnf) | – |
| 2. Python and frontend | `.venv` from `requirements.txt`, `apps/web/node_modules` from `package-lock.json` | ~0.5 GiB |
| 3. Configuration | `.env` from `.env.example` (an existing `.env` is kept) | – |
| 4. Speech runtime | builds whisper.cpp **v1.8.3** (CPU) in `data/tools/whisper.cpp` | – |
| 5. Speech model | `models/ggml-base.bin`, multilingual Whisper base | 142 MiB |
| 6. Local LLM | llama.cpp **b12079** release: CUDA 12.8 build if `nvidia-smi` works, otherwise the CPU build. Writes `NOTE_LLAMA_SERVER`, `NOTE_LIBRARY_PATH` and `NOTE_GPU_LAYERS` into `.env`. Also downloads `Qwen3-4B-Instruct-2507-Q4_K_M.gguf`. | 0.75 GiB CUDA or 22 MiB CPU, plus 2.4 GiB |
| 7. Research dataset | LongCounsel-8 primary edition from Hugging Face (`hiddensev/LongCounsel-8`), then `longcounsel prepare` | 3 MiB |
| 8. Database and demo patients | migrates `data/pshynox.db` and imports 16 synthetic **LongCounsel demo** patients (5 sessions each) | – |
| 9. Verification | backend tests, frontend build, then `--check` | – |

Every download is pinned to a fixed revision and checked by SHA-256 before use. Useful options:

```bash
./scripts/setup.sh --check          # report what is installed or missing; changes nothing
./scripts/setup.sh --gpu cpu        # force the CPU LLM runtime (or --gpu cuda)
./scripts/setup.sh --no-system      # you installed system packages yourself (no sudo)
./scripts/setup.sh --no-llm         # skip llama.cpp and Qwen (AI drafts and progress analysis unavailable)
./scripts/setup.sh --demo-limit 30  # more demo patients
./scripts/setup.sh --dense          # optional E5 dense retrieval (installs PyTorch)
./scripts/setup.sh --skip-tests -y  # unattended, no tests
./scripts/setup.sh --help
```

**Python or Node too old?**

- The script stops with a fix if Python is older than 3.11 or Node older than 20. Ubuntu 22.04 ships both too old.
- Install Node 22 with [nvm](https://github.com/nvm-sh/nvm) (`nvm install 22`), and Python 3.12 from the deadsnakes PPA.
- Then re-run `./scripts/setup.sh --no-system`.

**NVIDIA GPU:**

- The CUDA build needs a driver that supports CUDA 12.8 or newer; `nvidia-smi` must work.
- On Secure Boot laptops the driver may be installed but not loaded. See [docs/NVIDIA_LOCAL_FIX.md](docs/NVIDIA_LOCAL_FIX.md).
- Without a working driver the script uses the CPU build automatically (`NOTE_GPU_LAYERS=0`).

**WSL2:** run everything inside Linux and open the URL in a Windows browser. If the microphone is unavailable there,
import a WAV instead.

---

## What you can try

| Page | URL | What to do |
|---|---|---|
| Workspace | `/workspace` | Add a patient, start a session, record or import a WAV, **Transcribe locally**, edit speaker roles, draft and review the note. |
| Patient progress | `/workspace/patients/<id>/progress` (the **Progress** tab on a patient) | Questionnaire trends (PHQ-9/PHQ-8, GAD-7), symptom and functioning heatmap, change markers, risk statements, condition hypotheses with criteria and cited source turns. |
| Session observations | **Observations** panel inside a session | **Analyse this session** runs Stage A extraction. Values can be entered manually and reviewed (accept, edit or reject). |
| Research: retrieval | `/workspace/research` | AnnoMI/PriMock dataset browser, relevance annotation, retrieval benchmarks. |
| Research: notes | `/workspace/research-notes` | AI note drafting on development conversations. |
| Research: progress benchmark | `/workspace/research-progress` | LongCounsel-8 benchmark reports (after a report is generated, see below). |
| API docs | <http://127.0.0.1:8000/docs> | All endpoints. Health check: `/api/v1/health`. |

### Demo patients (LongCounsel-8)

Setup imports synthetic patients named **"LongCounsel demo · &lt;trajectory&gt;"**, each with five ordered sessions of
counselling transcript from the LongCounsel-8 **validation** split. They are research data, clearly labelled, and not
real patients. Only transcripts are imported: no ground-truth labels or scores.

To fill a demo patient's dashboard:

1. Open the patient and one of its sessions.
2. Tick the transcript review and click **Analyse this session**.
3. Then use **Re-run hypotheses** on the Progress page.

On a GTX 1650, Stage A takes about 3.5 minutes per session and the depression hypothesis about 1.5 minutes. It is
slower on the CPU (not measured).

```bash
.venv/bin/python -m services.api.import_longcounsel_demo --split validation --limit 16   # add more (never overwrites)
.venv/bin/python -m services.api.import_longcounsel_demo --with-results <run-id>          # attach a local benchmark run's AI outputs
```

`--with-results` only works on a machine that has run the benchmark: run folders stay local and are not in Git. The
database is backed up to `data/backups/` before every import. The held-out test split is always refused.

---

## Progress monitoring and condition hypotheses

All phases of the plan (0–6) are built on `progress-monitoring`. User guide:
[docs/PROGRESS_MONITORING.md](docs/PROGRESS_MONITORING.md).

- **Target conditions:**
  - bipolar disorder (manic, hypomanic and depressive episode features);
  - generalized anxiety disorder;
  - social anxiety disorder;
  - major depressive episode.

  The list is configurable in the registry (`services/progress/registry/`).
- **Stage A** extracts per-session observations from the transcript: symptom and functioning domains, PHQ-9/GAD-7 item
  estimates, risk statements and plan follow-ups. Every value cites transcript segment IDs.
- **Stage B** assesses each condition criterion by criterion across all sessions. A prediction is built only from cited
  findings. Abstention ("insufficient evidence") is explicit, never turned into "absent".
- **Display and review rules:**
  - AI values appear in charts and trends at full weight with the badge *AI-generated · human review pending*.
  - Rejected values leave the active series.
  - Edited values replace the AI value, and the original is kept.
  - A changed source turn marks dependent values *stale*.
- **Risk statements** are flagged for clinician review. They are never scored and never summarised as "no risk".
- **Evaluation** uses LongCounsel-8, which is synthetic and covers depression only. Bipolar, GAD and social-anxiety
  hypotheses are **unvalidated**. Criterion summaries are paraphrased and await clinical consultant verification.

Documents:

- [module guide](docs/PROGRESS_MONITORING.md): what the clinician sees, rules, pipeline, API, evaluation status;
- [plan, principles and progress log](docs/PROGRESS_MONITORING_PLAN.md);
- [benchmark runbook](docs/PROGRESS_BENCHMARK_RUNBOOK.md): exact commands for subsets, arms, reports, freeze, calibration and the final test run;
- [prompt v2 pilot](docs/PROGRESS_PROMPT_V2_PILOT.md);
- [freeze rule](docs/PROGRESS_FREEZE_RULE.md);
- [vignette authoring guide](docs/VIGNETTE_AUTHORING_GUIDE.md): the clinician-labelled cases needed to evaluate bipolar, GAD and social anxiety.

Benchmark runs are long GPU jobs: on a GTX 1650 they take about 4.8 h for `retrieval-current` and 1.4 h for
`retrieval-prior`. They checkpoint every session and
resume automatically. Example:

```bash
source .venv/bin/activate
python -m services.progress.evaluation.benchmark run \
  --subset data/research/progress-subsets/val-arms-12.json --arm retrieval-current --run-id val12-v2-retrieval-current
python -m services.progress.evaluation.report --run-id val12-v2-retrieval-current   # then open /workspace/research-progress
```

Read the runbook before running anything. The held-out test split runs only through `run_final` after the
configuration is frozen.

---

## Manual setup (what the script does, step by step)

Use this when you cannot run `scripts/setup.sh` or want to control each step. Commands run from the repository root.

<details>
<summary><b>1. System packages</b></summary>

```bash
# Ubuntu / Debian / WSL2 Ubuntu
sudo apt update
sudo apt install -y python3 python3-venv python3-pip python3-dev git curl ca-certificates build-essential cmake ffmpeg zstd sqlite3 nodejs npm
# Fedora
sudo dnf install -y python3 python3-pip python3-devel git curl gcc gcc-c++ make cmake ffmpeg-free zstd sqlite nodejs npm
```

Python must be **3.11+** and Node **20+** (22 is used in development). Check them with `python3 --version`,
`node --version`, `ffmpeg -version` and `ffprobe -version`.
</details>

<details>
<summary><b>2. Python and frontend dependencies</b></summary>

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt     # project + dev extra (pytest, httpx) from pyproject.toml
npm ci --prefix apps/web
cp -n .env.example .env
```

[`scripts/requirements-laptop-tested.txt`](scripts/requirements-laptop-tested.txt) is a snapshot of one development
environment. It is not a portable lockfile.
</details>

<details>
<summary><b>3. Speech runtime and model</b></summary>

```bash
mkdir -p data/tools models
git clone --depth 1 --branch v1.8.3 https://github.com/ggml-org/whisper.cpp.git data/tools/whisper.cpp
cmake -S data/tools/whisper.cpp -B data/tools/whisper.cpp/build -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DWHISPER_BUILD_TESTS=OFF
cmake --build data/tools/whisper.cpp/build --config Release -j 4
curl -L --fail -o models/ggml-base.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin
sha256sum models/ggml-base.bin   # 60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe
```

Use multilingual `base`, not `base.en`, for English and Urdu. Tested whisper.cpp commit:
`2eeeba56e9edd762b4b38467bab96c2517163158`. Build natively on each machine; do not copy x86 binaries to an ARM board.
</details>

<details>
<summary><b>4. Local LLM: llama.cpp and Qwen3-4B</b></summary>

```bash
B=https://github.com/ggml-org/llama.cpp/releases/download/b12079
# NVIDIA GPU (driver for CUDA 12.8+):
mkdir -p models/llama-cuda-b12079 && cd models/llama-cuda-b12079
curl -L --fail -o runtime.tar.gz $B/llama-b12079-bin-ubuntu-cuda-12.8-x64.tar.gz   # sha256 7965e1f2…3f668f
curl -L --fail -o cudart.tar.gz  $B/cudart-llama-b12079-bin-ubuntu-cuda-12.8-x64.tar.gz   # sha256 d810d53b…99f413
tar -xzf runtime.tar.gz && tar -xzf cudart.tar.gz && cd ../..
# CPU only: llama-b12079-bin-ubuntu-x64.tar.gz (sha256 52e7d5b6…4039c7) into models/llama-cpu-b12079

mkdir -p models/note_generation/qwen
curl -L --fail -o models/note_generation/qwen/Qwen3-4B-Instruct-2507-Q4_K_M.gguf \
  https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf
sha256sum models/note_generation/qwen/Qwen3-4B-Instruct-2507-Q4_K_M.gguf   # 3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597
```

Then add these lines to `.env`, using absolute paths:

```bash
NOTE_LLAMA_SERVER='/abs/path/pshynox/models/llama-cuda-b12079/llama-b12079/llama-server'
NOTE_LIBRARY_PATH='/abs/path/pshynox/models/llama-cuda-b12079/llama-b12079:/abs/path/pshynox/models/llama-cuda-b12079/cudart-llama-b12079-bin-ubuntu-cuda-12.8-x64'
NOTE_GPU_LAYERS='99'        # 0 for the CPU build
NOTE_BATCH_SIZE='256'
NOTE_UBATCH_SIZE='128'
```

The full SHA-256 values are in `scripts/setup.sh`. Details, measurements and the from-source CPU build are in
[docs/NOTE_GENERATION.md](docs/NOTE_GENERATION.md).
</details>

<details>
<summary><b>5. LongCounsel-8 dataset and demo patients</b></summary>

```bash
R=https://huggingface.co/datasets/hiddensev/LongCounsel-8/resolve/ad75f7e2e4248b055764487586a0d6590877fd5f
D=data/research/datasets/longcounsel-8/raw
mkdir -p $D/longcounsel-8-gpt-5.4-mini-low
for f in README.md LICENSE manifest.json longcounsel-8-gpt-5.4-mini-low/data.tar.zst longcounsel-8-gpt-5.4-mini-low/train_val_test_split.json; do
  curl -L --fail -o $D/$f $R/$f; done
.venv/bin/python -m services.progress.evaluation.longcounsel prepare          # verifies the manifest SHA-256 values
.venv/bin/python -m services.api.import_longcounsel_demo --split validation --limit 16
```

LongCounsel-8 is licensed CC BY-NC 4.0 (non-commercial, attribution; [paper](https://arxiv.org/abs/2609.03507)). Its
raw and prepared copies, benchmark runs and calibration stay local and are not committed. Only the fixed subset
manifests in `data/research/progress-subsets/` are in Git.
</details>

<details>
<summary><b>6. Optional: dense retrieval (E5)</b></summary>

```bash
.venv/bin/python -m pip install -e '.[dense]'
.venv/bin/python -m services.retrieval.cache_model --revision 614241f622f53c4eeff9890bdc4f31cfecc418b3
```

Then set `RETRIEVAL_METHOD=e5` or `hybrid` in `.env`. See [docs/RETRIEVAL.md](docs/RETRIEVAL.md).
</details>

---

## Running

```bash
./scripts/run.py               # backend :8000 + frontend :5173 with auto-reload, one terminal, Ctrl+C stops both
./scripts/run.py --no-reload   # recommended when using the local LLM (long-running jobs)
./scripts/run.py --api-port 8001 --web-port 5174
python scripts/run_lan.py      # HTTPS on the local Wi-Fi for two-device meetings (docs/ONLINE_MEETINGS.md)
```

The launcher uses `.venv/bin/python`, creates `.env` if it is missing, checks the ports and runs the database
migrations at backend startup. Restart it after changing `.env`.

## Tests

```bash
.venv/bin/python -m pytest -q                     # backend (temporary DB, fake LLM runtime; no GPU needed)
npm run build --prefix apps/web                   # TypeScript check + production build
cd apps/web && npx playwright test -c playwright.progress.config.ts   # progress browser tests; need Google Chrome
git diff --check
```

On a fresh clone, 5 retrieval tests are skipped. They need files that are not in Git: the AnnoMI/PriMock starter pack
under `docs/Pshynox_Retrieval_Starter/data/` and `data/research/review-experiments/`. Copy those folders from a
machine that has them to run the tests.

Optional real-model checks:

- `python -m services.speech data/tools/whisper.cpp/samples/jfk.wav`
- `python scripts/smoke_speech.py data/tools/whisper.cpp/samples/jfk.wav`

## Configuration and storage

`.env` is private and ignored by Git; [`.env.example`](.env.example) documents every setting. The main ones:

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/pshynox.db` | Local SQLite database |
| `LOCAL_DATA_DIR` / `RESEARCH_DATA_DIR` | `./data`, `./data/research` | Recordings, jobs, research data |
| `FIXTURE_MODE` | `false` | `true` also exposes the checked-in demo session |
| `WHISPER_BINARY`, `WHISPER_MODEL` | `./data/tools/whisper.cpp/build/bin/whisper-cli`, `./models/ggml-base.bin` | Speech runtime and model |
| `WHISPER_LANGUAGE`, `WHISPER_THREADS` | `auto`, `4` | Language detection (`en`/`ur` to force) and CPU threads |
| `RETRIEVAL_METHOD` | `bm25` | `bm25`, `tfidf`, `e5`, `hybrid` or `fixture` |
| `NOTE_LLAMA_SERVER`, `NOTE_LIBRARY_PATH` | set by setup | llama-server binary and its shared libraries |
| `NOTE_GPU_LAYERS` | `0` | `99` = all layers on the GPU; `0` = CPU |
| `NOTE_CONTEXT` | `4096` | Prompt + output tokens per call |
| `PROGRESS_EXTRACT_PROMPT` | `v2` | Stage A prompt version (`v1` kept for reproducibility) |

The following never enter Git:

- recordings (`data/recordings/`);
- the database;
- models and weights;
- runtime builds;
- benchmark runs;
- `.env`.

Back up `data/pshynox.db` together with `data/recordings/`. SQLite provides no encryption or authentication.

Before pushing, check:

```bash
git check-ignore models/ggml-base.bin data/pshynox.db .env
git status --short
```

## Troubleshooting

| Problem | What to check |
|---|---|
| Anything missing | `./scripts/setup.sh --check` lists each part. Re-run `./scripts/setup.sh`; finished steps are skipped. |
| "Missing llama-server" / "Missing model" | Re-run setup without `--no-llm`, or check `NOTE_LLAMA_SERVER` in `.env`. Restart `run.py`. |
| "GPU offload was requested but no layers were confirmed" | The driver is not loaded ([NVIDIA_LOCAL_FIX.md](docs/NVIDIA_LOCAL_FIX.md)). Set `NOTE_GPU_LAYERS=0` to run on the CPU. |
| `MODEL_BUSY` / memory slot busy | Another model job (transcription, note draft, analysis, benchmark) is running. Wait for it to finish. |
| "The local API could not complete that request." | Look at the backend output in the `run.py` terminal for the traceback. |
| Audio cannot be decoded | `ffmpeg` and `ffprobe` must run in the same terminal. Use a valid WAV within 5 min / 100 MB. |
| Microphone denied | Allow it for localhost in the browser, or import a WAV. |
| Transcription job stays queued | `SPEECH_WORKER_ENABLED=true`, Linux/WSL2, and check the backend output. |
| Port already in use | Stop the earlier `run.py`, or pass `--api-port` / `--web-port`. |
| `.env` change has no effect | Restart `run.py`. Exported shell variables override `.env`. |
| LongCounsel prepare fails on `.tar.zst` | Install `zstd` (or use Python 3.14+). |

## Architecture

```text
React 19 / TypeScript / Vite / Recharts (apps/web)
                     |
            FastAPI (services/api) ── SQLite (Alembic migrations) + local audio files
          /          |            \
 speech worker   retrieval       note generation / progress (Stage A, Stage B)
 whisper.cpp     BM25/TF-IDF/E5         |
                                 llama-server (llama.cpp) + Qwen3-4B GGUF, one model at a time
```

| Path | Contents |
|---|---|
| `apps/web/src/features/` | UI: patients, sessions, notes, research, progress |
| `services/api/` | routes, `schemas.py` (canonical API contracts), DB models, importers |
| `services/speech/`, `services/retrieval/`, `services/note_generation/` | Modules A and B, note drafting, shared LLM runtime |
| `services/progress/` | registry, Stage A/B prompts, post-processing, trends, `evaluation/` benchmark tooling |
| `alembic/versions/` | database migrations |
| `tests/` | backend tests (pytest) with fixtures and a scripted fake LLM |
| `docs/` | plans, status, runbooks and module documentation |
| `scripts/` | `setup.sh`, `run.py`, `run_lan.py`, benchmark and verification helpers |

| Owner | Module |
|---|---|
| Anas Faiz | A — Speech Processing |
| Muhammad Ayyan | B — Information Retrieval |
| Muhammad Talha | C — Clinical Application |

## Documentation

| Document | Topic |
|---|---|
| [PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | What is built, verified and outstanding |
| [PROGRESS_MONITORING.md](docs/PROGRESS_MONITORING.md) | Progress monitoring and condition hypotheses: user and team guide |
| [PROGRESS_MONITORING_PLAN.md](docs/PROGRESS_MONITORING_PLAN.md) | Progress-monitoring design, principles, progress log, open questions |
| [VIGNETTE_AUTHORING_GUIDE.md](docs/VIGNETTE_AUTHORING_GUIDE.md) | Writing and labelling vignette cases; running their evaluation |
| [PROGRESS_BENCHMARK_RUNBOOK.md](docs/PROGRESS_BENCHMARK_RUNBOOK.md) | LongCounsel-8 benchmark commands and runtimes |
| [NOTE_GENERATION.md](docs/NOTE_GENERATION.md), [NOTE_FORMULATION_REVIEW.md](docs/NOTE_FORMULATION_REVIEW.md) | Local AI note drafts |
| [RETRIEVAL.md](docs/RETRIEVAL.md), [RESEARCH_EVALUATION.md](docs/RESEARCH_EVALUATION.md), [TEAM_ANNOTATION.md](docs/TEAM_ANNOTATION.md) | Module B retrieval, research workspace, annotation workflow |
| [PATIENT_WORKSPACE.md](docs/PATIENT_WORKSPACE.md), [ONLINE_MEETINGS.md](docs/ONLINE_MEETINGS.md) | Workspace and LAN meetings |
| [MODULE_INTEGRATION_GUIDE.md](docs/MODULE_INTEGRATION_GUIDE.md), [Plan.txt](docs/Plan.txt) | Module boundaries and the original plan |
| [NVIDIA_LOCAL_FIX.md](docs/NVIDIA_LOCAL_FIX.md) | NVIDIA driver and Secure Boot on Fedora |

Work on a feature branch and include verification results in pull requests. Never commit personal records, model
weights, local databases or LongCounsel-8 data.

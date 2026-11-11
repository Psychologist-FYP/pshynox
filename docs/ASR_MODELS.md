# Speech recognition models

Pshynox transcribes locally with a model chosen by the session's **language mode**. All engines return the same
transcript segments (ids, `start_ms`/`end_ms` in original recording time, speaker roles), so nothing after
transcription changes. Code: `services/speech/asr/`. Manifest of every model file: `services/speech/asr/models.json`.

## Modes

| Mode | Engine | Model | Language |
|---|---|---|---|
| `english` | Parakeet TDT 0.6B v2 through ONNX Runtime (`onnx-asr`) | `models/asr/parakeet-v2/onnx-int8/` | English |
| `urdu_mixed` (default) | whisper.cpp | Urdu Sukhan ASR v1, `models/asr/urdu-sukhan/ggml/ggml-urdu-sukhan-asr-v1-q8_0.bin` | `ur` (never `auto`) |
| fallback for either | whisper.cpp | multilingual Whisper base, `models/ggml-base.bin` | `en` or `ur`, from the mode |

- **Choosing a mode.** The session's mode is set on the session page, under "Record or upload a conversation", before
  recording. A meeting can override it per voice ("Clinician track" and "Patient track") when it is created.
  `PUT /api/v1/meetings/{id}/asr-modes` changes it for chunks saved afterwards.
- **What is recorded.** Every recording stores the mode it was saved with. Every transcription job stores the mode and
  the model it actually used (`asr_mode`, `asr_model`). Its metrics hold the engine, the model's name, SHA-256 and
  licence, the device (GPU backend and name), VAD use and any notices. `GET /api/v1/sessions/{id}/recordings` returns
  `transcribed_with` per recording (and each job `produced_by`): mode, model name, engine, device, licence, fallback.
  The UI shows it on each recording ("Transcribed with Urdu Sukhan ASR v1 (q8_0) · Urdu or mixed Urdu–English (ur) ·
  whisper.cpp · GPU (Vulkan, …) · VAD on · CC BY NC 4.0: non-commercial use only") and above the transcript.
- **Fallback is never silent.** If the mode's model or runtime is missing, the job uses Whisper base and records a
  notice. The UI shows it next to the mode picker and on the recording, for example "Urdu model not installed — using
  fallback" or "English model not installed — using fallback". `GET /api/v1/asr/status` reports what each mode would
  use right now.
- **VAD** (Silero v5.1.2) is on by default. whisper.cpp runs with `--vad`. For Parakeet, the same model finds speech
  regions, which are packed into windows of at most 25 s; long speech is cut at its quietest frame. Timestamps are
  shifted back by each window's start. If the VAD model is missing, transcription runs without it and records a notice.
  Parakeet then cuts audio with an energy detector.
- **Device.** The two engines take turns with the LLM in the shared memory slot.
  - whisper.cpp tries its GPU builds first, in order: the CUDA build at `ASR_WHISPER_CUDA_BINARY`, then the Vulkan
    build at `ASR_WHISPER_GPU_BINARY`. A missing build is skipped. A failed one is followed by the next, with a notice
    ("CUDA transcription failed — used the Vulkan build"), and finally by the CPU build ("GPU transcription failed —
    used CPU"). The job records `gpu_backend` (`cuda` / `vulkan`) and the GPU's name in `gpu_device`. For Vulkan, a
    one-off 3 s probe per worker process picks the discrete GPU. **This laptop has no CUDA toolkit (`nvcc`), so there
    is no CUDA build yet and the Vulkan build runs on the GTX 1650**; the build command is below.
  - Parakeet uses ONNX Runtime's CUDA provider when `onnxruntime-gpu` is installed, else the CPU.
  - `ASR_DEVICE=cpu` forces the CPU for both.

## Measured on this laptop (11 November 2026; synthetic espeak-ng speech, so accuracy is not measured here)

| Run | Result |
|---|---|
| Parakeet int8, CPU, 3.6-minute English sample | 42.8 s with the model load (real-time factor 0.19); 10 VAD windows, 70 segments with continuous timestamps. A 45-minute recording would take about 7–8 min. |
| Parakeet, later jobs in the same worker | Model kept in RAM: 2.4 s instead of 12.4 s for a 9 s clip. |
| whisper.cpp Vulkan (GTX 1650), Whisper base, 9 s clip | Runs on the NVIDIA GPU. The first job per worker adds a 3 s device probe. |
| whisper.cpp Vulkan (GTX 1650), **Urdu Sukhan q8_0**, `urdu_mixed`, 14.3 s synthetic mixed Urdu/English clip (12 Nov 2026) | 1 segment; real-time factor 0.75 (10.7 s including model load and the 3 s probe); peak VRAM 1,182 MiB of 4,096 (1,169 MiB above idle). The espeak-ng English words came back in Urdu script (آفس, پانک اٹکس), so synthetic speech says nothing about how it writes real English words. |

## Models and licences

| File(s) | Size | SHA-256 | Licence | Source |
|---|---|---|---|---|
| Parakeet ONNX int8: `encoder-model.int8.onnx`, `decoder_joint-model.int8.onnx`, `nemo128.onnx`, `vocab.txt`, `config.json` | 652.2 + 9.0 + 0.1 MB + small files | in `models.json` | **CC BY 4.0** (attribution: NVIDIA) | `istupakov/parakeet-tdt-0.6b-v2-onnx` @ `0bbb45a` |
| `parakeet-tdt-0.6b-v2.nemo` (provenance only; the app does not load it) | 2,472 MB | `d99e3995…` | CC BY 4.0 | `nvidia/parakeet-tdt-0.6b-v2` |
| `ggml-urdu-sukhan-asr-v1-q8_0.bin` (Urdu Sukhan ASR v1, fine-tuned Whisper large-v3-turbo, q8_0) | 874.2 MB (874,188,075 bytes) | `a3cff426…b71b355` (full in `models.json`) | **CC BY-NC 4.0: non-commercial use only** (`models/asr/urdu-sukhan/LICENSE`, `NOTICE.md`) | Urdu Sukhan ASR v1 release (whisper-lite run 6, step 2775; `ggml-r6c2775-q8_0.bin`, renamed) |
| `ggml-base.bin` | 148 MB | `60ed5bc3…` | MIT | `ggerganov/whisper.cpp` |
| `ggml-silero-v5.1.2.bin` | 0.9 MB | `29940d98…` | MIT | `ggml-org/whisper-vad` @ `9ffd54a` |

Each file is checked against `models.json` before use. A file of the wrong size counts as not installed, so a
partial download falls back with a notice. A file of the right size with a different SHA-256 is refused
(`MODEL_CHANGED`).

Runtime packages: `onnxruntime` 1.31 (CPU, 23.8 MB) and `onnx-asr` 0.12 (MIT, 4.0 MB), both in `pyproject.toml`.

## Download commands

From the repository root. `-C -` resumes an interrupted download.

```bash
# English: Parakeet TDT 0.6B v2, ONNX int8 (about 662 MB)
mkdir -p models/asr/parakeet-v2/onnx-int8
H=https://huggingface.co/istupakov/parakeet-tdt-0.6b-v2-onnx/resolve/0bbb45a3365852604aef28b538a8f066f4ccaa85
for f in config.json vocab.txt nemo128.onnx decoder_joint-model.int8.onnx encoder-model.int8.onnx; do
  curl -fL -C - -o models/asr/parakeet-v2/onnx-int8/$f $H/$f
done

# VAD for both engines (0.9 MB)
mkdir -p models/asr/vad
curl -fL -C - -o models/asr/vad/ggml-silero-v5.1.2.bin \
  https://huggingface.co/ggml-org/whisper-vad/resolve/9ffd54a1e1ee413ddf265af9913beaf518d1639b/ggml-silero-v5.1.2.bin

# Runtime (CPU)
.venv/bin/pip install "onnxruntime==1.31.1" "onnx-asr==0.12.0"

# Check every file against the manifest
.venv/bin/python -c "from services.speech.asr import manifest; [print(f['path'], manifest.verify(manifest.ROOT / f['path'])['manifest']) for m in manifest.entries() for f in m['files'] if (manifest.ROOT / f['path']).exists()]"
```

**Urdu Sukhan** is installed at `models/asr/urdu-sukhan/ggml/ggml-urdu-sukhan-asr-v1-q8_0.bin` with its `LICENSE`,
`NOTICE.md` and `VERIFY.md` one folder up (all git-ignored under `models/`). On 12 Nov 2026 its SHA-256
(`a3cff426de79e98ffe4525d3b011c38b6e5265d7b27c9ceea85680e28b71b355`) was checked against `VERIFY.md`. **`VERIFY.md` gives the
checksum only shortened (`a3cff426…b71b355`, for the original file name `ggml-r6c2775-q8_0.bin`), so only its first 8
and last 7 hex digits could be compared. Both match.** The full hash and size are now pinned in `models.json`, so a
changed file is refused (`MODEL_CHANGED`). Ask the model's author for the full checksum to close this gap.

**whisper.cpp CUDA build (not built yet; needs the CUDA toolkit, several GB).** It is used before Vulkan when present:

```bash
sudo dnf install cuda-toolkit        # NVIDIA's repository; provides nvcc
cmake -S data/tools/whisper.cpp -B data/tools/whisper.cpp/build-cuda -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
cmake --build data/tools/whisper.cpp/build-cuda -j --config Release --target whisper-cli
```

**whisper.cpp GPU build (Vulkan),** kept next to the CPU build, which stays as the fallback:

```bash
sudo dnf install vulkan-headers vulkan-loader-devel glslc
cmake -S data/tools/whisper.cpp -B data/tools/whisper.cpp/build-vulkan -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build data/tools/whisper.cpp/build-vulkan -j --config Release --target whisper-cli
```

**Parakeet on the GPU (optional, about 3.75 GB).**
- Install `onnxruntime-gpu==1.31.1` in place of `onnxruntime`, with `nvidia-cudnn-cu12`, `nvidia-cufft-cu12` and
  `nvidia-curand-cu12`.
- Download the fp32 files (`encoder-model.onnx` + `encoder-model.onnx.data`, `decoder_joint-model.onnx`) into
  `models/asr/parakeet-v2/onnx-fp32/`, and set `ASR_ENGLISH_MODEL` to that folder. int8 runs poorly on the CUDA
  provider.

## Switching models

All settings are in `.env` (template: `.env.example`) and are read when each job starts:

| Variable | Default |
|---|---|
| `ASR_ENGLISH_MODEL` | `./models/asr/parakeet-v2/onnx-int8` (an onnx-asr Parakeet folder; int8 used when present, else fp32) |
| `ASR_URDU_MODEL` | `./models/asr/urdu-sukhan/ggml/ggml-urdu-sukhan-asr-v1-q8_0.bin` |
| `ASR_FALLBACK_MODEL` | `./models/ggml-base.bin` (`WHISPER_MODEL` is still read if this is unset) |
| `ASR_VAD_MODEL` | `./models/asr/vad/ggml-silero-v5.1.2.bin` |
| `WHISPER_BINARY` / `ASR_WHISPER_CUDA_BINARY` / `ASR_WHISPER_GPU_BINARY` | CPU build / CUDA build (`build-cuda`) / Vulkan build (`build-vulkan`) of whisper.cpp |
| `ASR_DEVICE` | `auto` (GPU first, CPU fallback) or `cpu` |
| `ASR_VULKAN_DEVICE` | unset: the first discrete GPU found by a one-off probe (on this laptop the NVIDIA GTX 1650 is Vulkan device 1; device 0 is the Intel iGPU). Set an index to force one. |

To use another model for a mode, point its variable at the file and add an entry for it to `models.json` (path,
size, SHA-256, licence, source). A file missing from the manifest still runs, but its metrics say
`model_manifest: unlisted`. The old Whisper base model is kept as the fallback; never delete it.

## Urdu Sukhan output: storage, editing, search and display

Urdu Sukhan writes Urdu in Urdu script, English words in Latin script, and no punctuation, so one segment can mix
both directions ("مجھے آج کل بہت anxiety ہوتی ہے").

- **Storage and editor** (`services/speech/text.py`). ASR output and clinician edits are saved in one form: Unicode NFC,
  no bidirectional control characters (a stray RLE/LRM from ASR or a paste would reorder the line), single spaces.
  Zero-width non-joiners are kept, because they are part of Urdu spelling. Letters are never changed. A segment that is
  empty after this is dropped.
- **Search** (`services/retrieval/query.py`, used by BM25, TF-IDF and Boolean filters). Only the tokens are folded,
  so a query matches however the word was typed: Arabic ي/ى → ی, ك → ک, ه → ہ (ھ stays distinct); Arabic-Indic and
  Urdu digits → 0–9; tatweel, ZWNJ and ZWJ are ignored inside a word; a change between Latin and Urdu letters ends a
  word ("depressionکی" → `depression`, `کی`). Vowel marks (aerab) are still kept in tokens, as before. English-only
  text tokenizes as before.
- **Display** (`apps/web/src/lib/bidi.ts`). Each transcript line, editor box, evidence excerpt, citation and risk
  statement gets `dir` from the script with more letters (a tie counts as Urdu). `dir="auto"` would instead follow the
  first word, so "panic attacks آتے ہیں جب…" would come out left-to-right. Urdu lines also get `lang="ur"`, which
  selects a local Urdu font (Noto Nastaliq Urdu or Jameel Noori Nastaleeq if installed, else Noto Naskh Arabic or
  PakType Naskh) and a taller line height. The browser's bidi algorithm then places the English words inside the RTL
  line.

## Smoke tests (one command per mode)

The command uses a synthetic fictional sample made with espeak-ng, or your own WAV with `--wav`. The `urdu_mixed`
sample is Urdu phrases with English words in between, spoken by the Urdu and English voices and joined. It prints the
model (name, file, SHA-256, licence), engine, language, device, VAD, notices, segment count, real-time factor
(including model load), peak VRAM (whole GPU, sampled every 100 ms with `nvidia-smi`, plus the rise above idle) and
the segments. It saves nothing.

```bash
.venv/bin/python -m services.speech.smoke --mode urdu_mixed --wav path/to/sample.wav
.venv/bin/python -m services.speech.smoke --mode urdu_mixed
.venv/bin/python -m services.speech.smoke --mode english
```

`python -m services.speech <file> --mode english|urdu_mixed` prints full segments and metrics as JSON.

## Database migration and other branches

Migration `0010_asr_language_models` follows `0009_internet_meetings`. The DSM branches (`feat/progress-dsm`,
`feat/progress-fast`) also add a migration after `0009` (`0010_dsm_registry`). A database upgraded on one of them
therefore cannot start on this branch. Start-up says so instead of failing with a traceback.

- **While the branches stay separate,** run this branch on its own copy: `data/pshynox-asr.db`. It was copied from
  `data/pshynox.db` on 11 November 2026 and marked as being at `0009`; the DSM tables and columns stay in it, unused.
  ```bash
  DATABASE_URL=sqlite:///./data/pshynox-asr.db .venv/bin/python scripts/run.py
  ```
- **When both reach `main`,** the second merge needs an Alembic merge revision (`alembic merge -m "asr + dsm" heads`)
  or this migration re-parented onto `0010_dsm_registry`. Both migrations are additive, so the order does not matter.

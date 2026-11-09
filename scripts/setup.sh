#!/usr/bin/env bash
# Pshynox: one-command setup for a fresh Linux (or WSL2) laptop.
#
#   ./scripts/setup.sh                 # everything, GPU auto-detected
#   ./scripts/setup.sh --check         # only report what is installed / missing
#   ./scripts/setup.sh --help          # all options
#
# Installs system packages, the Python and frontend dependencies, builds whisper.cpp, downloads the speech model,
# the llama.cpp runtime and the Qwen3-4B model, fetches and prepares the LongCounsel-8 research dataset, creates the
# database, imports labelled demo patients and runs the tests. Every download is pinned and checked by SHA-256.
# Safe to re-run: finished steps are detected and skipped. Nothing here uploads data or uses hosted inference.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------- pinned sources
WHISPER_TAG="v1.8.3"
WHISPER_COMMIT="2eeeba56e9edd762b4b38467bab96c2517163158"
WHISPER_MODEL_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/ggml-base.bin"
WHISPER_MODEL_SHA="60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"

LLAMA_BUILD="b12079"
LLAMA_URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_BUILD}"
LLAMA_CUDA_TAR="llama-${LLAMA_BUILD}-bin-ubuntu-cuda-12.8-x64.tar.gz"
LLAMA_CUDA_SHA="7965e1f2e8c097c08383af781808e671d875306b60112e2f2e5eb6bbbc3f668f"
LLAMA_CUDART_TAR="cudart-llama-${LLAMA_BUILD}-bin-ubuntu-cuda-12.8-x64.tar.gz"
LLAMA_CUDART_SHA="d810d53bbe90cddaf6de3bfb350c9071f76cd1cf97ec5ee9062318adb599f413"
LLAMA_CPU_X64_TAR="llama-${LLAMA_BUILD}-bin-ubuntu-x64.tar.gz"
LLAMA_CPU_X64_SHA="52e7d5b6502c7757f09e939f99369138fb8bbbd8e623475951ed8f05fa4039c7"
LLAMA_CPU_ARM_TAR="llama-${LLAMA_BUILD}-bin-ubuntu-arm64.tar.gz"
LLAMA_CPU_ARM_SHA="191e74d5610dbb4736df8d7b642125593e7b771246629b8cad7694d7f0e94364"

QWEN_URL="https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
QWEN_SHA="3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597"
QWEN_PATH="models/note_generation/qwen/Qwen3-4B-Instruct-2507-Q4_K_M.gguf"

LC8_REPO="https://huggingface.co/datasets/hiddensev/LongCounsel-8/resolve/ad75f7e2e4248b055764487586a0d6590877fd5f"
LC8_EDITION="longcounsel-8-gpt-5.4-mini-low"
LC8_RAW="data/research/datasets/longcounsel-8/raw"
LC8_MANIFEST_SHA="efe4637d654e036371ad98dce1ceedd3c81c02f4eb6d6c9105dfc54009a02fb6"

E5_REVISION="614241f622f53c4eeff9890bdc4f31cfecc418b3"

# ---------------------------------------------------------------- options
GPU="auto"; SYSTEM=1; LLM=1; DATASET=1; DEMO=1; DEMO_LIMIT=16; TESTS=1; DENSE=0; QUANT=0; CHECK=0; YES=0
usage() {
  cat <<EOF
Usage: ./scripts/setup.sh [options]

  --check            Report what is installed or missing; change nothing.
  --gpu auto|cuda|cpu
                     llama.cpp runtime for the local LLM (default auto: CUDA 12.8 build when nvidia-smi works on x86_64,
                     otherwise the CPU build).
  --no-system        Skip installing system packages with apt/dnf (install them yourself).
  --no-llm           Skip llama.cpp and the Qwen model (no AI notes, no progress extraction or hypotheses).
  --no-dataset       Skip downloading and preparing LongCounsel-8 (implies --no-demo).
  --no-demo          Do not create LongCounsel demo patients.
  --demo-limit N     Number of transcript-only demo patients from the validation split (default ${DEMO_LIMIT}).
  --dense            Also install the optional E5 dense-retrieval extra and cache its model (~0.5 GB + PyTorch).
  --quantize         Also build a smaller q5_0 copy of the speech model.
  --skip-tests       Do not run the backend tests and frontend build at the end.
  -y, --yes          Do not ask before installing system packages.
  -h, --help         Show this help.

Downloads (first run): speech model 142 MiB, Qwen3-4B GGUF 2.4 GiB, llama.cpp CPU 22 MiB or CUDA 0.75 GiB,
LongCounsel-8 primary edition 3 MiB, npm and pip packages ~0.5 GiB. Disk: about 5 GiB in total.
EOF
}
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK=1 ;;
    --gpu) GPU="${2:?--gpu needs auto, cuda or cpu}"; shift ;;
    --gpu=*) GPU="${1#*=}" ;;
    --no-system) SYSTEM=0 ;;
    --no-llm) LLM=0 ;;
    --no-dataset) DATASET=0; DEMO=0 ;;
    --no-demo) DEMO=0 ;;
    --demo-limit) DEMO_LIMIT="${2:?--demo-limit needs a number}"; shift ;;
    --dense) DENSE=1 ;;
    --quantize) QUANT=1 ;;
    --skip-tests) TESTS=0 ;;
    -y|--yes) YES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done
[[ "$GPU" =~ ^(auto|cuda|cpu)$ ]] || { echo "--gpu must be auto, cuda or cpu" >&2; exit 2; }

# ---------------------------------------------------------------- helpers
if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else B=""; G=""; Y=""; R=""; N=""; fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
fail() { printf '  %s✗%s %s\n' "$R" "$N" "$*"; }
die()  { printf '\n%sSetup stopped:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
sha_of() { sha256sum "$1" | cut -d' ' -f1; }
ARCH="$(uname -m)"

# download URL DEST SHA256: resumable, verified before it replaces DEST; an existing verified DEST is kept.
download() {
  local url="$1" dest="$2" sha="$3"
  if [[ -f "$dest" ]] && [[ "$(sha_of "$dest")" == "$sha" ]]; then ok "$dest (already present, SHA-256 verified)"; return; fi
  mkdir -p "$(dirname "$dest")"
  echo "  downloading $(basename "$dest") ..."
  curl -L --fail --retry 5 --retry-delay 3 -C - --progress-bar -o "$dest.part" "$url" \
    || { rm -f "$dest.part"; die "download failed: $url"; }
  local got; got="$(sha_of "$dest.part")"
  if [[ "$got" != "$sha" ]]; then
    rm -f "$dest.part"; die "SHA-256 mismatch for $dest (expected $sha, got $got). Re-run to download again."
  fi
  mv "$dest.part" "$dest"; ok "$dest (SHA-256 verified)"
}

# set_env KEY VALUE: write KEY='VALUE' into .env, replacing an existing KEY line.
set_env() {
  python3 - "$1" "$2" <<'PY'
import pathlib, sys
key, value = sys.argv[1], sys.argv[2]
path = pathlib.Path(".env")
lines = path.read_text().splitlines() if path.exists() else []
line = f"{key}='{value}'"
for i, existing in enumerate(lines):
    if existing.split("=", 1)[0].strip() == key:
        lines[i] = line
        break
else:
    lines.append(line)
path.write_text("\n".join(lines) + "\n")
PY
}
env_value() { [[ -f .env ]] && grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e "s/^['\"]//" -e "s/['\"]$//" || true; }

version_ge() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" == "$2" ]]; }

nvidia_ok() { have nvidia-smi && nvidia-smi -L >/dev/null 2>&1; }
# Older drivers print "CUDA Version: 12.8", newer ones "CUDA UMD Version: 13.4".
cuda_driver_version() { nvidia-smi 2>/dev/null | sed -n 's/.*CUDA \(UMD \)\{0,1\}Version: *\([0-9.]*\).*/\2/p' | head -1; }

pick_gpu() {
  if [[ "$GPU" == "auto" ]]; then
    if [[ "$ARCH" == "x86_64" ]] && nvidia_ok; then GPU="cuda"; else GPU="cpu"; fi
  fi
  if [[ "$GPU" == "cuda" ]]; then
    [[ "$ARCH" == "x86_64" ]] || die "the pinned CUDA build is x86_64 only; use --gpu cpu on $ARCH"
    nvidia_ok || die "--gpu cuda needs a working NVIDIA driver (nvidia-smi fails). See docs/NVIDIA_LOCAL_FIX.md, or use --gpu cpu."
    local v; v="$(cuda_driver_version)"
    if [[ -n "$v" ]] && ! version_ge "$v" "12.8"; then
      die "the NVIDIA driver supports CUDA $v; the pinned llama.cpp build needs a driver for CUDA 12.8 or newer. Update the driver or use --gpu cpu."
    fi
  fi
}

# ---------------------------------------------------------------- --check
check() {
  step "Checking this checkout ($ROOT)"
  local missing=0
  for tool in python3 node npm git curl cmake ffmpeg ffprobe zstd sha256sum; do
    if have "$tool"; then ok "$tool"; else fail "$tool not found"; missing=1; fi
  done
  if have python3; then
    local pv; pv="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    if version_ge "$pv" "3.11"; then ok "Python $pv"; else fail "Python $pv (needs 3.11+)"; missing=1; fi
  fi
  if have node; then
    local nv; nv="$(node --version | tr -d v)"
    if version_ge "$nv" "20.0"; then ok "Node $nv"; else fail "Node $nv (needs 20+, 22 recommended)"; missing=1; fi
  fi
  [[ -x .venv/bin/python ]] && .venv/bin/python -c "import fastapi, sqlalchemy, sklearn, pytest" 2>/dev/null \
    && ok "Python environment (.venv)" || { fail "Python environment (.venv) missing or incomplete"; missing=1; }
  [[ -d apps/web/node_modules ]] && ok "Frontend packages" || { fail "Frontend packages (apps/web/node_modules)"; missing=1; }
  [[ -x data/tools/whisper.cpp/build/bin/whisper-cli ]] && ok "whisper.cpp runtime" || { fail "whisper.cpp runtime"; missing=1; }
  [[ -f models/ggml-base.bin ]] && ok "Speech model models/ggml-base.bin" || { fail "Speech model models/ggml-base.bin"; missing=1; }
  [[ -f .env ]] && ok ".env" || { fail ".env (copy .env.example)"; missing=1; }
  local server; server="$(env_value NOTE_LLAMA_SERVER)"; server="${server:-$ROOT/models/llama.cpp/build/bin/llama-server}"
  [[ -x "$server" ]] && ok "llama-server ($server), NOTE_GPU_LAYERS=$(env_value NOTE_GPU_LAYERS)" || warn "llama-server not found at $server (AI notes and progress analysis unavailable)"
  [[ -f "$QWEN_PATH" ]] && ok "Qwen3-4B model" || warn "Qwen3-4B model missing ($QWEN_PATH)"
  [[ -f "$LC8_RAW/manifest.json" ]] && ok "LongCounsel-8 raw release" || warn "LongCounsel-8 raw release missing"
  [[ -f "data/research/datasets/longcounsel-8/prepared/$LC8_EDITION/manifest.json" ]] && ok "LongCounsel-8 prepared" || warn "LongCounsel-8 not prepared"
  if [[ -f data/pshynox.db ]] && have sqlite3; then
    ok "Database data/pshynox.db ($(sqlite3 data/pshynox.db "select count(*) from patients where id like 'lc8-demo-%'" 2>/dev/null || echo '?') LongCounsel demo patients)"
  elif [[ -f data/pshynox.db ]]; then ok "Database data/pshynox.db"; else warn "Database not created yet (created on first start)"; fi
  if nvidia_ok; then ok "NVIDIA GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1), driver CUDA $(cuda_driver_version)"; else warn "No working NVIDIA GPU (CPU inference only)"; fi
  echo
  if [[ $missing -eq 0 ]]; then ok "Required parts are present. Start with ./scripts/run.py"; else fail "Some required parts are missing. Run ./scripts/setup.sh"; fi
  return $missing
}
if [[ $CHECK -eq 1 ]]; then check; exit $?; fi

# ---------------------------------------------------------------- 0. platform
step "Platform"
[[ "$(uname -s)" == "Linux" ]] || die "Pshynox runs on Linux or a Linux distribution inside WSL2 (found $(uname -s))."
grep -qi microsoft /proc/version 2>/dev/null && ok "WSL2 detected: run the app here and open the printed URL in a Windows browser"
ok "Linux $ARCH, repository $ROOT"
[[ $LLM -eq 1 ]] && pick_gpu && ok "Local LLM runtime: $GPU"

# ---------------------------------------------------------------- 1. system packages
step "1/9 System packages"
if [[ $SYSTEM -eq 1 ]]; then
  SUDO=(); [[ $EUID -ne 0 ]] && SUDO=(sudo)
  if have apt-get; then
    PKGS=(python3 python3-venv python3-pip python3-dev git curl ca-certificates build-essential cmake ffmpeg zstd sqlite3 nodejs npm)
    INSTALL=("${SUDO[@]}" apt-get install -y "${PKGS[@]}"); UPDATE=("${SUDO[@]}" apt-get update)
  elif have dnf; then
    PKGS=(python3 python3-pip python3-devel git curl gcc gcc-c++ make cmake ffmpeg-free zstd sqlite nodejs npm)
    INSTALL=("${SUDO[@]}" dnf install -y "${PKGS[@]}"); UPDATE=(true)
  else
    PKGS=(); warn "No apt-get or dnf found: install Python 3.11+, Node 22, git, curl, a C/C++ compiler, make, CMake, FFmpeg, zstd yourself."
  fi
  if [[ ${#PKGS[@]} -gt 0 ]]; then
    echo "  will install: ${PKGS[*]}"
    if [[ $YES -eq 0 && -t 0 ]]; then read -r -p "  Install these now (uses sudo)? [Y/n] " answer; [[ "${answer:-y}" =~ ^[Yy] ]] || die "skipped by user (re-run with --no-system to continue without)"; fi
    "${UPDATE[@]}" && "${INSTALL[@]}" || die "system package installation failed"
    ok "system packages installed"
  fi
else
  warn "skipped (--no-system)"
fi
for tool in python3 git curl cmake make ffmpeg ffprobe npm node; do have "$tool" || die "$tool is not on PATH"; done
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
version_ge "$PYV" "3.11" || die "Python $PYV found; Pshynox needs 3.11 or newer (Ubuntu 22.04: sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12 python3.12-venv, then re-run with PATH pointing python3 at it)"
NODEV="$(node --version | tr -d v)"
version_ge "$NODEV" "20.0" || die "Node $NODEV found; the frontend needs Node 20+ (22 recommended). Install it with nvm: curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash, open a new terminal, nvm install 22, then re-run with --no-system."
ok "Python $PYV, Node $NODEV, $(cmake --version | head -1), $(ffmpeg -version | head -1 | cut -d' ' -f1-3)"
have zstd || warn "zstd not found: needed to read LongCounsel-8 unless Python is 3.14+"

# ---------------------------------------------------------------- 2. python + frontend
step "2/9 Python environment and frontend packages"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt   # an "Editable installation" warning here is harmless
ok "backend dependencies installed in .venv ($(.venv/bin/python --version))"
if [[ $DENSE -eq 1 ]]; then
  .venv/bin/python -m pip install --quiet -e '.[dense]'
  ok "optional dense-retrieval extra installed"
fi
npm ci --prefix apps/web --no-audit --no-fund --loglevel=error
ok "frontend packages installed (apps/web/node_modules)"

# ---------------------------------------------------------------- 3. configuration
step "3/9 Local configuration (.env)"
if [[ -f .env ]]; then ok ".env already exists; only missing runtime paths are added"; else cp .env.example .env; ok ".env created from .env.example"; fi

# ---------------------------------------------------------------- 4. whisper.cpp
step "4/9 Speech runtime (whisper.cpp $WHISPER_TAG, CPU)"
WHISPER_DIR="data/tools/whisper.cpp"
if [[ -x "$WHISPER_DIR/build/bin/whisper-cli" ]]; then
  ok "already built: $WHISPER_DIR/build/bin/whisper-cli"
else
  mkdir -p data/tools
  [[ -d "$WHISPER_DIR/.git" ]] || git clone --quiet --depth 1 --branch "$WHISPER_TAG" https://github.com/ggml-org/whisper.cpp.git "$WHISPER_DIR"
  head="$(git -C "$WHISPER_DIR" rev-parse HEAD)"
  [[ "$head" == "$WHISPER_COMMIT" ]] || warn "whisper.cpp is at $head, tested commit is $WHISPER_COMMIT"
  log="data/tools/whisper-build.log"; echo "  building (a few minutes; log: $log) ..."
  { cmake -S "$WHISPER_DIR" -B "$WHISPER_DIR/build" -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DWHISPER_BUILD_TESTS=OFF \
      && cmake --build "$WHISPER_DIR/build" --config Release -j "$(nproc)"; } >"$log" 2>&1 \
    || { tail -20 "$log"; die "whisper.cpp build failed (full log: $log)"; }
  [[ -x "$WHISPER_DIR/build/bin/whisper-cli" ]] || die "whisper.cpp build did not produce build/bin/whisper-cli"
  ok "built $WHISPER_DIR/build/bin/whisper-cli"
fi

# ---------------------------------------------------------------- 5. speech model
step "5/9 Speech model (multilingual Whisper base, English/Urdu)"
download "$WHISPER_MODEL_URL" models/ggml-base.bin "$WHISPER_MODEL_SHA"
if [[ $QUANT -eq 1 && ! -f models/ggml-base-q5_0.bin ]]; then
  "$WHISPER_DIR/build/bin/whisper-quantize" models/ggml-base.bin models/ggml-base-q5_0.bin q5_0 >/dev/null
  ok "models/ggml-base-q5_0.bin (set WHISPER_MODEL in .env to use it)"
fi

# ---------------------------------------------------------------- 6. llama.cpp + Qwen
step "6/9 Local LLM (llama.cpp $LLAMA_BUILD + Qwen3-4B-Instruct-2507 Q4_K_M)"
if [[ $LLM -eq 1 ]]; then
  if [[ "$GPU" == "cuda" ]]; then
    dir="models/llama-cuda-$LLAMA_BUILD"
    download "$LLAMA_URL/$LLAMA_CUDA_TAR" "$dir/runtime.tar.gz" "$LLAMA_CUDA_SHA"
    download "$LLAMA_URL/$LLAMA_CUDART_TAR" "$dir/cudart.tar.gz" "$LLAMA_CUDART_SHA"
    [[ -x "$dir/llama-$LLAMA_BUILD/llama-server" ]] || tar -xzf "$dir/runtime.tar.gz" -C "$dir"
    [[ -f "$dir/cudart-llama-$LLAMA_BUILD-bin-ubuntu-cuda-12.8-x64/libcudart.so.12" ]] || tar -xzf "$dir/cudart.tar.gz" -C "$dir"
    server="$ROOT/$dir/llama-$LLAMA_BUILD/llama-server"
    libs="$ROOT/$dir/llama-$LLAMA_BUILD:$ROOT/$dir/cudart-llama-$LLAMA_BUILD-bin-ubuntu-cuda-12.8-x64"
    layers=99
  else
    dir="models/llama-cpu-$LLAMA_BUILD"
    if [[ "$ARCH" == "x86_64" ]]; then tarball="$LLAMA_CPU_X64_TAR"; sha="$LLAMA_CPU_X64_SHA"
    elif [[ "$ARCH" == "aarch64" ]]; then tarball="$LLAMA_CPU_ARM_TAR"; sha="$LLAMA_CPU_ARM_SHA"
    else die "no pinned llama.cpp CPU build for $ARCH; build it from source (docs/NOTE_GENERATION.md) and set NOTE_LLAMA_SERVER"; fi
    download "$LLAMA_URL/$tarball" "$dir/runtime.tar.gz" "$sha"
    found="$(find "$dir" -name llama-server -type f 2>/dev/null | head -1)"
    if [[ -z "$found" ]]; then tar -xzf "$dir/runtime.tar.gz" -C "$dir"; found="$(find "$dir" -name llama-server -type f | head -1)"; fi
    [[ -n "$found" ]] || die "llama-server not found in $tarball"
    server="$ROOT/$found"; libs="$ROOT/$(dirname "$found")"; layers=0
  fi
  chmod +x "$server"
  LD_LIBRARY_PATH="$libs" "$server" --version >/dev/null 2>&1 && ok "llama-server runs: $server" \
    || warn "llama-server did not start for --version (check missing system libraries with: ldd $server)"
  current="$(env_value NOTE_LLAMA_SERVER)"
  if [[ -z "$current" || ! -x "$current" ]]; then
    set_env NOTE_LLAMA_SERVER "$server"; set_env NOTE_LIBRARY_PATH "$libs"; set_env NOTE_GPU_LAYERS "$layers"
    set_env NOTE_BATCH_SIZE 256; set_env NOTE_UBATCH_SIZE 128
    ok ".env: NOTE_LLAMA_SERVER, NOTE_LIBRARY_PATH, NOTE_GPU_LAYERS=$layers, batch 256/128"
  else
    ok ".env already points NOTE_LLAMA_SERVER at an existing runtime ($current); left unchanged"
  fi
  download "$QWEN_URL" "$QWEN_PATH" "$QWEN_SHA"
else
  warn "skipped (--no-llm): AI note drafts, progress extraction and hypotheses will report a missing runtime"
fi
if [[ $DENSE -eq 1 ]]; then
  .venv/bin/python -m services.retrieval.cache_model --revision "$E5_REVISION" && ok "E5 model cached under models/retrieval"
fi

# ---------------------------------------------------------------- 7. LongCounsel-8
step "7/9 Research dataset LongCounsel-8 (CC BY-NC 4.0, synthetic, primary edition $LC8_EDITION)"
if [[ $DATASET -eq 1 ]]; then
  download "$LC8_REPO/manifest.json" "$LC8_RAW/manifest.json" "$LC8_MANIFEST_SHA"
  read -r ARCHIVE_SHA SPLIT_SHA < <(python3 - "$LC8_RAW/manifest.json" "$LC8_EDITION" <<'PY'
import json, sys
entry = json.load(open(sys.argv[1]))["datasets"][sys.argv[2]]
print(entry["archive_sha256"], entry["split_sha256"])
PY
)
  download "$LC8_REPO/$LC8_EDITION/data.tar.zst" "$LC8_RAW/$LC8_EDITION/data.tar.zst" "$ARCHIVE_SHA"
  download "$LC8_REPO/$LC8_EDITION/train_val_test_split.json" "$LC8_RAW/$LC8_EDITION/train_val_test_split.json" "$SPLIT_SHA"
  for f in README.md LICENSE; do
    [[ -s "$LC8_RAW/$f" ]] || curl -sL --fail --retry 5 -o "$LC8_RAW/$f" "$LC8_REPO/$f" || die "download failed: $f"
  done
  ok "$LC8_RAW (README, LICENSE, manifest, archive, split)"
  if [[ -f "data/research/datasets/longcounsel-8/prepared/$LC8_EDITION/manifest.json" ]]; then
    ok "already prepared"
  else
    .venv/bin/python -m services.progress.evaluation.longcounsel prepare >/dev/null
    ok "prepared transcripts and separate label files (data/research/datasets/longcounsel-8/prepared/)"
  fi
else
  warn "skipped (--no-dataset)"
fi

# ---------------------------------------------------------------- 8. database + demo patients
step "8/9 Database and demo patients"
.venv/bin/python - <<'PY'
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(".env"))
from services.api.main import migrate
migrate()
PY
ok "database migrated to the latest schema"
if [[ ! -f data/retrieval-engineering-benchmark.json ]]; then
  .venv/bin/python -m services.retrieval.benchmark --dataset tests/retrieval/fixtures/engineering.json \
    --output data/retrieval-engineering-benchmark.json >/dev/null 2>&1 \
    && ok "fictional retrieval benchmark report (research page and its tests)" || warn "fictional retrieval benchmark could not be generated"
fi
if [[ $DEMO -eq 1 ]]; then
  # A finished validation run on this machine (not in Git) lets the demo patients open with AI results attached.
  for run in val12-v2-retrieval-current val12-v2-retrieval-prior; do
    if [[ -f "data/research/progress-runs/$run/status.json" ]]; then
      .venv/bin/python -m services.api.import_longcounsel_demo --with-results "$run" >/dev/null
      ok "demo patients with AI results from local run $run"
      break
    fi
  done
  .venv/bin/python -m services.api.import_longcounsel_demo --split validation --limit "$DEMO_LIMIT" >/dev/null
  ok "up to $DEMO_LIMIT transcript-only LongCounsel demo patients (existing ones are never overwritten)"
else
  warn "skipped"
fi

# ---------------------------------------------------------------- 9. verification
step "9/9 Verification"
if [[ $TESTS -eq 1 ]]; then
  if .venv/bin/python -m pytest -q -p no:cacheprovider >data/setup-pytest.log 2>&1; then ok "backend tests: $(tail -1 data/setup-pytest.log)"
  else tail -15 data/setup-pytest.log; warn "backend tests failed (log: data/setup-pytest.log)"; fi
  if npm run build --prefix apps/web --silent >data/setup-build.log 2>&1; then ok "frontend build passed"
  else tail -15 data/setup-build.log; warn "frontend build failed (log: data/setup-build.log)"; fi
else
  warn "skipped (--skip-tests)"
fi
check || true

cat <<EOF

${B}Setup finished.${N}
  Start the app:     ./scripts/run.py --no-reload
  Open:              http://127.0.0.1:5173/workspace
  Demo patients:     names start with "LongCounsel demo · " (synthetic, not real patients)
  Research results:  http://127.0.0.1:5173/workspace/research-progress
  Re-check later:    ./scripts/setup.sh --check
EOF

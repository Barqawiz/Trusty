#!/usr/bin/env bash
# Download all model weights Trusty needs.
# Idempotent: skips files that already exist.
# Reads HF_TOKEN from .env (or env) for the Gemma download.
#
# Usage:  bash scripts/download_models.sh

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# Load .env if present (do not echo the token).
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . .env
  set +a
fi

# Resolve venv python and hf CLI.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
  HF_BIN=".venv/bin/hf"
  if [ ! -x "$HF_BIN" ]; then HF_BIN=".venv/bin/huggingface-cli"; fi
else
  PY="python3"
  HF_BIN="hf"
  command -v hf >/dev/null 2>&1 || HF_BIN="huggingface-cli"
fi

mkdir -p models/gemma models/whisper models/kokoro models/wakeword music data

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*" >&2; }
skip()  { printf "  \033[2mskip\033[0m %s (exists)\n" "$*"; }

# ---------- Whisper models ----------
# small.en (~466 MB) is the default; much better with accents than base.en.
# base.en (~141 MB) is downloaded as a fallback for memory-constrained setups.
bold "==> Whisper models (base.en + small.en)"
if [ ! -d external/whisper.cpp ]; then
  red "external/whisper.cpp missing. Run setup first (clone whisper.cpp)."
  exit 1
fi
for variant in base.en small.en; do
  TARGET="models/whisper/ggml-${variant}.bin"
  if [ -f "$TARGET" ]; then
    skip "$TARGET"
    continue
  fi
  ( cd external/whisper.cpp && bash ./models/download-ggml-model.sh "$variant" )
  cp "external/whisper.cpp/models/ggml-${variant}.bin" "$TARGET"
  green "  saved $TARGET"
done

# ---------- Kokoro int8 + voices ----------
bold "==> Kokoro int8 ONNX + voices"
KOKORO_MODEL="models/kokoro/kokoro-v1.0.int8.onnx"
KOKORO_VOICES="models/kokoro/voices-v1.0.bin"
KOKORO_BASE="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

if [ -f "$KOKORO_MODEL" ]; then
  skip "$KOKORO_MODEL"
else
  curl -L --fail --progress-bar -o "$KOKORO_MODEL" "$KOKORO_BASE/kokoro-v1.0.int8.onnx"
  green "  saved $KOKORO_MODEL"
fi

if [ -f "$KOKORO_VOICES" ]; then
  skip "$KOKORO_VOICES"
else
  curl -L --fail --progress-bar -o "$KOKORO_VOICES" "$KOKORO_BASE/voices-v1.0.bin"
  green "  saved $KOKORO_VOICES"
fi

# ---------- openWakeWord defaults ----------
bold "==> openWakeWord pretrained models"
"$PY" - <<'PY'
import sys
try:
    from openwakeword.utils import download_models
    download_models()
    print("  ok: openwakeword default models present in package resources")
except Exception as e:
    print(f"  warning: {e}", file=sys.stderr)
    sys.exit(0)
PY

# ---------- Moonshine ONNX (only when STT_BACKEND=moonshine) ----------
# Pulls the encoder + decoder ONNX from Hugging Face *once* and copies them
# into the project tree at `models/moonshine/<size>/`. After this, the
# voice loop loads from disk and never contacts the hub again.
if [ "${STT_BACKEND:-whisper}" = "moonshine" ]; then
  MOON_NAME="${MOONSHINE_MODEL:-base}"
  bold "==> Moonshine ONNX (model=${MOON_NAME})"
  MOON_DIR="models/moonshine/${MOON_NAME}"
  MOON_ENC="${MOON_DIR}/encoder_model.onnx"
  MOON_DEC="${MOON_DIR}/decoder_model_merged.onnx"
  mkdir -p "$MOON_DIR"
  if [ -f "$MOON_ENC" ] && [ -f "$MOON_DEC" ]; then
    skip "$MOON_ENC"
    skip "$MOON_DEC"
  else
    # Allow network for this download even if HF_HUB_OFFLINE is set in .env.
    HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 \
    MOON_NAME="$MOON_NAME" MOON_DIR="$MOON_DIR" "$PY" - <<'PY'
import os, shutil, sys
from pathlib import Path
try:
    from huggingface_hub import hf_hub_download
    name = os.environ["MOON_NAME"]
    dst = Path(os.environ["MOON_DIR"])
    dst.mkdir(parents=True, exist_ok=True)
    for fname in ("encoder_model.onnx", "decoder_model_merged.onnx"):
        path = hf_hub_download(
            repo_id="UsefulSensors/moonshine",
            filename=f"onnx/merged/{name}/float/{fname}",
        )
        shutil.copy(path, dst / fname)
        print(f"  saved {dst / fname}")
except Exception as e:
    print(f"  warning: {e}", file=sys.stderr)
    sys.exit(0)
PY
  fi
fi

# ---------- Silero VAD (ONNX) ----------
# Tiny (~2 MB) speech / non-speech classifier used by the recorder to
# replace fragile RMS thresholds. Operates on 32 ms chunks of 16 kHz
# audio. Same upstream as silero-vad PyPI package; we use the raw ONNX
# directly so no extra runtime dependency.
bold "==> Silero VAD ONNX"
mkdir -p models/vad
SILERO_VAD="models/vad/silero_vad.onnx"
SILERO_URL="https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
if [ -f "$SILERO_VAD" ]; then
  skip "$SILERO_VAD"
else
  curl -L --fail --progress-bar -o "$SILERO_VAD" "$SILERO_URL"
  green "  saved $SILERO_VAD"
fi

# ---------- Gemma 4 E2B IT GGUF ----------
# GEMMA_VARIANT=tuned (default): Trusty-tuned Q4_K_S from barqawiz/trusty-gemma-4-e2b-home-assistant.
# GEMMA_VARIANT=untuned: original Google Gemma (unsloth/gemma-3n-E2B-it-GGUF, needs HF_TOKEN).
GEMMA_VARIANT="${GEMMA_VARIANT:-tuned}"

if [ "$GEMMA_VARIANT" = "tuned" ]; then
  HF_REPO="barqawiz/trusty-gemma-4-e2b-home-assistant"
  GEMMA_FILE="trusty-gemma-4-e2b-tuned-q4_k_s.gguf"
  GEMMA_TARGET="models/gemma/${GEMMA_FILE}"
  bold "==> Gemma 4 E2B IT GGUF (Trusty-tuned, Q4_K_S, ~3.1 GB)"
elif [ "$GEMMA_VARIANT" = "untuned" ]; then
  GEMMA_QUANT="${GEMMA_QUANT:-Q6_K}"
  case "$GEMMA_QUANT" in
    Q8_0|Q6_K|Q5_K_M|Q4_K_M) ;;
    *) red "Unsupported GEMMA_QUANT=$GEMMA_QUANT (allowed: Q8_0, Q6_K, Q5_K_M, Q4_K_M)"; exit 1 ;;
  esac
  HF_REPO="unsloth/gemma-3n-E2B-it-GGUF"
  GEMMA_FILE="gemma-3n-E2B-it-${GEMMA_QUANT}.gguf"
  GEMMA_QUANT_LC=$(printf '%s' "$GEMMA_QUANT" | tr '[:upper:]' '[:lower:]')
  GEMMA_TARGET="models/gemma/gemma-4-e2b-it-${GEMMA_QUANT_LC}.gguf"
  bold "==> Gemma 4 E2B IT GGUF (un-tuned, ${GEMMA_QUANT})"
else
  red "Unsupported GEMMA_VARIANT=$GEMMA_VARIANT (allowed: tuned, untuned)"
  exit 1
fi

if [ -f "$GEMMA_TARGET" ]; then
  skip "$GEMMA_TARGET"
else
  bold "  fetching $HF_REPO/$GEMMA_FILE  (cli: $HF_BIN)"
  HF_ARGS=("$HF_REPO" "$GEMMA_FILE" --local-dir models/gemma)
  if [ "$GEMMA_VARIANT" = "untuned" ]; then
    if [ -z "${HF_TOKEN:-}" ]; then
      red "HF_TOKEN empty in .env: required to fetch the un-tuned Gemma (gated repo)."
      exit 1
    fi
    HF_ARGS+=(--token "$HF_TOKEN")
  fi
  set +e
  HF_OUTPUT=$("$HF_BIN" download "${HF_ARGS[@]}" 2>&1)
  HF_RC=$?
  set -e
  echo "$HF_OUTPUT" | tail -20
  if echo "$HF_OUTPUT" | grep -qi "deprecated and no longer works"; then
    HF_RC=2
    red "huggingface-cli is deprecated; install or alias 'hf' and retry."
  fi
  if [ $HF_RC -ne 0 ]; then
    red ""
    red "Gemma download failed."
    red "Repo tried: huggingface.co/$HF_REPO"
    red "Confirm the correct repo and re-run."
    exit $HF_RC
  fi
  # Tuned download lands at the target filename directly; un-tuned needs rename.
  if [ "$GEMMA_VARIANT" = "untuned" ] && [ -f "models/gemma/$GEMMA_FILE" ]; then
    mv "models/gemma/$GEMMA_FILE" "$GEMMA_TARGET"
  fi
  if [ -f "$GEMMA_TARGET" ]; then
    green "  saved $GEMMA_TARGET"
  else
    red "Download reported success but $GEMMA_TARGET is missing. Inspect models/gemma/."
    exit 1
  fi
fi

bold "==> All downloads complete"
ls -lh models/gemma models/whisper models/kokoro 2>/dev/null

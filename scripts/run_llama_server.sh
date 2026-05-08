#!/usr/bin/env bash
# Start the llama.cpp HTTP server for Trusty.
# Reads paths and tuning knobs from .env. Defaults are safe for any host;
# Pi 5 overrides via .env (LLAMA_THREADS=3, LLAMA_THREADS_BATCH=4).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a; . .env; set +a
fi

LLAMA_BIN="${LLAMA_CPP_DIR:-$PROJECT_DIR/external/llama.cpp}/build/bin/llama-server"
MODEL="${GEMMA_MODEL_PATH:-$PROJECT_DIR/models/gemma/gemma-4-e2b-it.gguf}"
HOST="${LLAMA_HOST:-127.0.0.1}"
PORT="${LLAMA_PORT:-8080}"

if [ ! -x "$LLAMA_BIN" ]; then
  echo "llama-server binary not found at $LLAMA_BIN" >&2
  echo "Build llama.cpp:" >&2
  echo "  cd external/llama.cpp && cmake -B build && cmake --build build --config Release -j" >&2
  exit 1
fi
if [ ! -f "$MODEL" ]; then
  echo "Gemma model not found at $MODEL" >&2
  echo "Run: bash scripts/download_models.sh" >&2
  exit 1
fi

# Auto-detect default thread count: nproc - 1, leaving one core for the
# orchestrator + voice loop. .env LLAMA_THREADS overrides explicitly.
if command -v nproc >/dev/null 2>&1; then
  AUTO_THREADS="$(nproc)"
elif command -v sysctl >/dev/null 2>&1; then
  AUTO_THREADS="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
else
  AUTO_THREADS=4
fi
AUTO_THREADS=$(( AUTO_THREADS > 1 ? AUTO_THREADS - 1 : 1 ))

THREADS="${LLAMA_THREADS:-$AUTO_THREADS}"
# Prefill (compute-bound) can use one more core than decode (memory-bound).
# Pi 5: LLAMA_THREADS=3, LLAMA_THREADS_BATCH=4. On Mac default is THREADS.
THREADS_BATCH="${LLAMA_THREADS_BATCH:-$THREADS}"

CTX_SIZE="${LLAMA_CTX_SIZE:-4096}"
# KV cache quantization. q8_0 is effectively lossless on a 4B routing model,
# halves KV memory bandwidth, and gives 5-10% decode speedup on Pi 5. Set to
# f16 in .env to revert.
CACHE_TYPE_K="${LLAMA_CACHE_TYPE_K:-q8_0}"
CACHE_TYPE_V="${LLAMA_CACHE_TYPE_V:-q8_0}"
# Parallel sequence slots. Recent llama.cpp builds default this to >1, which
# divides the KV cache among slots and overflows our ~3000-token system
# prompt. Pin to 1 unless you have a specific reason to allow concurrent
# requests.
PARALLEL="${LLAMA_PARALLEL:-1}"

ARGS=(
  --model "$MODEL"
  --host "$HOST"
  --port "$PORT"
  --ctx-size "$CTX_SIZE"
  --parallel "$PARALLEL"
  --threads "$THREADS"
  --threads-batch "$THREADS_BATCH"
  --cache-type-k "$CACHE_TYPE_K"
  --cache-type-v "$CACHE_TYPE_V"
  --jinja
)

# Optional: pin pages in RAM (avoids eviction under Docker memory pressure).
if [ "${LLAMA_MLOCK:-0}" = "1" ]; then
  ARGS+=(--mlock)
fi

# Optional: flash attention. Off by default; flip in .env if your llama.cpp
# build supports it on this host (mostly a GPU flag, marginal on ARM CPU).
if [ "${LLAMA_FLASH_ATTN:-0}" = "1" ]; then
  ARGS+=(--flash-attn)
fi

# Optional: server-side default cap on generated tokens. Per-request
# max_tokens (set by app/model_client.py) takes precedence.
if [ -n "${LLAMA_N_PREDICT:-}" ]; then
  ARGS+=(--n-predict "$LLAMA_N_PREDICT")
fi

echo "Starting llama-server"
echo "  bin:           $LLAMA_BIN"
echo "  model:         $MODEL"
echo "  bind:          $HOST:$PORT"
echo "  ctx-size:      $CTX_SIZE"
echo "  threads:       $THREADS (decode)"
echo "  threads-batch: $THREADS_BATCH (prefill)"
echo "  cache-type-k:  $CACHE_TYPE_K"
echo "  cache-type-v:  $CACHE_TYPE_V"
echo "  parallel:      $PARALLEL"
echo "  mlock:         ${LLAMA_MLOCK:-0}"
echo "  flash-attn:    ${LLAMA_FLASH_ATTN:-0}"
echo "  cpu-affinity:  ${LLAMA_CPU_AFFINITY:-<none>}"

# Optional: pin llama threads to specific cores (e.g. "0-2" on Pi 5 to leave
# core 3 for runner / voice / pipewire). Empty = no pinning.
if [ -n "${LLAMA_CPU_AFFINITY:-}" ] && command -v taskset >/dev/null 2>&1; then
  exec taskset -c "$LLAMA_CPU_AFFINITY" "$LLAMA_BIN" "${ARGS[@]}"
else
  exec "$LLAMA_BIN" "${ARGS[@]}"
fi

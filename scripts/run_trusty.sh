#!/usr/bin/env bash
# Run the Trusty FastAPI orchestrator.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a; . .env; set +a
fi

if [ -x ".venv/bin/uvicorn" ]; then
  UVICORN=".venv/bin/uvicorn"
else
  UVICORN="uvicorn"
fi

HOST="${TRUSTY_HOST:-0.0.0.0}"
PORT="${TRUSTY_PORT:-8090}"

echo "Starting Trusty on $HOST:$PORT"
exec "$UVICORN" app.main:app --host "$HOST" --port "$PORT"

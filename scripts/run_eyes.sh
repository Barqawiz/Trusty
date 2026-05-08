#!/usr/bin/env bash
# Serve the Trusty Eyes static UI on EYES_PORT.
# The page connects back to the API server (port 8090) for /ws/state.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a; . .env; set +a
fi

PORT="${EYES_PORT:-8091}"
DIR="ui/eyes"

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

echo "Serving $DIR at http://0.0.0.0:$PORT"
exec "$PY" -m http.server "$PORT" -d "$DIR"

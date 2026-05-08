#!/usr/bin/env bash
# Run end-to-end smoke tests against a running Trusty + llama-server.
# Assumes:
#   - llama-server on $LLAMA_BASE_URL  (default http://127.0.0.1:8080/v1)
#   - Trusty FastAPI on $TRUSTY_HOST:$TRUSTY_PORT  (default 0.0.0.0:8090)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a; . .env; set +a
fi

LLAMA="${LLAMA_BASE_URL:-http://127.0.0.1:8080/v1}"
API_HOST="${TRUSTY_HOST:-0.0.0.0}"
[ "$API_HOST" = "0.0.0.0" ] && API_HOST="127.0.0.1"
API="http://${API_HOST}:${TRUSTY_PORT:-8090}"

bold() { printf "\n\033[1m== %s ==\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*"; FAILED=$((FAILED+1)); }

FAILED=0

bold "1. llama-server health"
if curl -s -m 5 "${LLAMA%/v1}/health" | grep -q '"ok":true\|"status":"ok"\|^{'; then
  ok "llama-server responds"
else
  fail "llama-server not reachable at $LLAMA"
fi

bold "2. llama-server chat completion"
RESP=$(curl -s -m 30 "${LLAMA}/chat/completions" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemma","messages":[{"role":"user","content":"Reply with the single word: ready."}],"max_tokens":16}' || echo "")
if echo "$RESP" | grep -qi "ready"; then
  ok "Gemma responded"
else
  fail "Gemma did not respond as expected: $(echo "$RESP" | head -c 200)"
fi

bold "3. Trusty health"
if curl -s -m 5 "${API}/health" | grep -q '"ok":true'; then
  ok "Trusty /health"
else
  fail "Trusty /health failed"
fi

bold "4. /chat — local answer"
RESP=$(curl -s -X POST "${API}/chat" \
  -H 'Content-Type: application/json' \
  -d '{"text":"What is the capital of Jordan?"}')
TOOL=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["plan"]["tool"])' 2>/dev/null || echo "?")
if [ "$TOOL" = "local.answer" ]; then
  ok "tool=local.answer"
else
  fail "expected local.answer, got: $TOOL"
fi

bold "5. /chat — privacy block"
RESP=$(curl -s -X POST "${API}/chat" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Send my microphone audio to the internet."}')
BLOCKED=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["ledger"]["blocked"])' 2>/dev/null || echo "?")
if [ "$BLOCKED" = "True" ] || [ "$BLOCKED" = "true" ]; then
  ok "blocked=true"
else
  fail "expected blocked=true, got: $BLOCKED"
fi

bold "6. /chat — weather without location (with empty memory)"
# Clear memory via the admin endpoint so we test the empty-memory branch.
# Either ask_for_location or local.answer is acceptable; weather.live with no
# location_text would be the failure (silent default to a wrong city).
BACKUP=$(curl -s "${API}/admin/memory")
curl -s -X POST "${API}/admin/memory/clear" >/dev/null
RESP=$(curl -s -X POST "${API}/chat" \
  -H 'Content-Type: application/json' \
  -d '{"text":"Will it rain today?"}')
TOOL=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["plan"]["tool"])' 2>/dev/null || echo "?")
ACTION=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["plan"]["action"])' 2>/dev/null || echo "?")
# Best-effort restore of memory.json (tests shouldn't trample real state).
[ -n "$BACKUP" ] && printf '%s' "$BACKUP" > data/memory.json
if [ "$ACTION" = "ask_for_location" ] || [ "$TOOL" = "local.answer" ]; then
  ok "tool=$TOOL action=$ACTION (acceptable)"
else
  fail "expected ask_for_location or local.answer, got tool=$TOOL action=$ACTION"
fi

bold "7. /chat — vacuum stop routes to home.vacuum return_to_dock"
RESP=$(curl -s -X POST "${API}/chat" \
  -H 'Content-Type: application/json' \
  -d '{"text":"roborock back to the dock"}')
TOOL=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["plan"]["tool"])' 2>/dev/null || echo "?")
ACTION=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["plan"]["action"])' 2>/dev/null || echo "?")
if [ "$TOOL" = "home.vacuum" ] && [ "$ACTION" = "return_to_dock" ]; then
  ok "tool=home.vacuum action=return_to_dock"
else
  fail "expected home.vacuum/return_to_dock, got tool=$TOOL action=$ACTION"
fi

bold "8. ledger tail"
ENTRIES=$(curl -s "${API}/privacy/ledger?limit=5" | python3 -c 'import sys,json; print(len(json.loads(sys.stdin.read())["entries"]))' 2>/dev/null || echo 0)
if [ "$ENTRIES" -gt 0 ]; then
  ok "ledger has $ENTRIES entries"
else
  fail "ledger empty"
fi

echo
if [ "$FAILED" -eq 0 ]; then
  printf "\033[1;32mAll smoke tests passed.\033[0m\n"
else
  printf "\033[1;31m%d test(s) failed.\033[0m\n" "$FAILED"
  exit 1
fi

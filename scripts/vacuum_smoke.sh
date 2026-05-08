#!/usr/bin/env bash
# End-to-end check that voice/text commands for the vacuum reach the right
# Home Assistant service. Hits the real /chat endpoint, then confirms the
# vacuum's actual state via the HA REST API.
#
# Pre-reqs:
#   - llama-server running (LLAMA_BASE_URL)
#   - Trusty FastAPI running (TRUSTY_HOST:TRUSTY_PORT)
#   - HA_URL and HA_TOKEN populated in .env
#   - VACUUM_ENTITY_ID matching a real vacuum in HA
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a; . .env; set +a
fi

API_HOST="${TRUSTY_HOST:-0.0.0.0}"
[ "$API_HOST" = "0.0.0.0" ] && API_HOST="127.0.0.1"
API="http://${API_HOST}:${TRUSTY_PORT:-8090}"
HA="${HA_URL:-http://localhost:8123}"
ENTITY="${VACUUM_ENTITY_ID:-vacuum.s6_pure}"

bold() { printf "\n\033[1m== %s ==\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*"; FAILED=$((FAILED+1)); }

FAILED=0

bold "0. Tool registry sees home.vacuum"
TOOLS=$(curl -s "${API}/admin/tools")
if echo "$TOOLS" | grep -q '"home.vacuum"'; then
  ok "home.vacuum is registered"
else
  fail "home.vacuum NOT registered — restart Trusty and re-run."
  echo "  registry says:"
  echo "$TOOLS" | python3 -m json.tool | sed 's/^/    /'
  exit 1
fi

ha_state() {
  curl -s -H "Authorization: Bearer ${HA_TOKEN}" "${HA}/api/states/${ENTITY}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])'
}

run_chat() {
  local text="$1"
  local resp
  resp=$(curl -s -X POST "${API}/chat" \
    -H 'content-type: application/json' \
    -d "$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$text")")
  CHAT_TOOL=$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["plan"]["tool"])')
  CHAT_ACTION=$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["plan"]["action"])')
  CHAT_REPLY=$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["final_response"])')
  CHAT_BLOCKED=$(printf '%s' "$resp" | python3 -c 'import sys,json; print(json.loads(sys.stdin.read())["ledger"]["blocked"])')
}

assert_route() {
  local label="$1" expected_action="$2"
  if [ "$CHAT_TOOL" = "home.vacuum" ] && [ "$CHAT_ACTION" = "$expected_action" ]; then
    ok "$label -> tool=home.vacuum action=$CHAT_ACTION"
  else
    fail "$label -> tool=$CHAT_TOOL action=$CHAT_ACTION blocked=$CHAT_BLOCKED reply=$CHAT_REPLY"
  fi
}

bold "1. Initial vacuum state"
START_STATE=$(ha_state)
ok "vacuum is currently: $START_STATE"

bold "2. \"send the vacuum back to the dock\" routes to return_to_dock"
run_chat "send the vacuum back to the dock"
assert_route "dock command" "return_to_dock"

bold "3. \"clean my living room\" routes to start"
run_chat "clean my living room"
assert_route "start command" "start"

bold "4. \"stop cleaning\" routes to return_to_dock (per user request)"
run_chat "stop cleaning"
assert_route "stop->dock" "return_to_dock"

bold "5. STT mishearing — \"Trust me, vacuum my room\" still routes (Trusty/vacuum normalization)"
run_chat "Trust me, vacuum my room"
assert_route "Trust-me cleanup" "start"

bold "6. STT mishearing — \"Vokyo back to dock\" still routes (Vokyo->vacuum)"
run_chat "Vokyo back to dock"
assert_route "Vokyo cleanup" "return_to_dock"

bold "7. Vacuum acted on the last command"
sleep 2
END_STATE=$(ha_state)
ok "vacuum state after dock command: $END_STATE"
case "$END_STATE" in
  returning|docked|cleaning|paused|idle) ok "HA reports a known state ($END_STATE)" ;;
  *) fail "unexpected vacuum state: $END_STATE" ;;
esac

echo
if [ "$FAILED" -eq 0 ]; then
  printf "\033[1;32mAll vacuum smoke tests passed.\033[0m\n"
else
  printf "\033[1;31m%d test(s) failed.\033[0m\n" "$FAILED"
  exit 1
fi

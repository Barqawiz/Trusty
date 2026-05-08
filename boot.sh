#!/usr/bin/env bash
# Trusty boot — single command to bring everything up after a Pi reboot.
#
#   bash boot.sh
#
# Idempotent: re-running just rebuilds the tmux session.
# Docker stack auto-restarts on Pi boot (restart: unless-stopped) — this
# script verifies it and re-ups if anything is missing, then starts the
# three Trusty processes inside a single detachable tmux session.
set -euo pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
green() { printf "  \033[32m%s\033[0m\n" "$*"; }
red()   { printf "  \033[31m%s\033[0m\n" "$*" >&2; }

# ---------- 1. docker stack ----------
bold "==> docker stack (HA · Music Assistant · SearXNG)"
if ! command -v docker >/dev/null 2>&1; then
  red "docker not installed — see ahmad_pi.html section 8"
  exit 1
fi
# Use sudo only if the current user can't talk to docker directly.
DOCKER="docker"
if ! docker ps >/dev/null 2>&1; then
  DOCKER="sudo docker"
fi
$DOCKER compose -f "$PROJECT_DIR/docker-compose.yml" up -d \
  homeassistant music-assistant searxng > /dev/null
$DOCKER compose -f "$PROJECT_DIR/docker-compose.yml" ps \
  --format 'table {{.Name}}\t{{.Status}}'

# ---------- 2. tmux session ----------
bold "==> tmux session 'trusty' (3 panes)"
if ! command -v tmux >/dev/null 2>&1; then
  red "tmux not installed — sudo apt install -y tmux"
  exit 1
fi

# Wipe any previous session so panes always start clean.
tmux kill-session -t trusty 2>/dev/null || true

# Pane 1 (top-left): llama-server
tmux new-session -d -s trusty -n main \
  "cd '$PROJECT_DIR' && bash scripts/run_llama_server.sh; exec bash"

# Pane 2 (top-right): Trusty FastAPI
tmux split-window -h -t trusty:main \
  "cd '$PROJECT_DIR' && bash scripts/run_trusty.sh; exec bash"

# Pane 3 (bottom): voice loop. Wait a beat so llama has a head start.
tmux split-window -v -t trusty:main \
  "cd '$PROJECT_DIR' && sleep 8 && bash scripts/run_voice.sh; exec bash"

tmux select-layout -t trusty:main tiled >/dev/null
green "tmux session 'trusty' started"

# ---------- 3. summary ----------
bold "==> ready"
cat <<EOF
  docker:  HA · Music Assistant · SearXNG
  tmux:    trusty (3 panes — llama-server, FastAPI, voice loop)

  attach to watch:  tmux attach -t trusty
  detach inside:    Ctrl-b  d
  kill everything:  tmux kill-session -t trusty

  Wake phrase: "Hey Trusty …"
EOF

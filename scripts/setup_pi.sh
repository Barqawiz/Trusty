#!/usr/bin/env bash
# Idempotent setup script for Raspberry Pi 5 (Bookworm 64-bit).
# Run from the Trusty project root after copying it to the Pi.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }

bold "==> apt packages"
sudo apt update
sudo apt install -y \
  git cmake build-essential \
  python3-venv python3-pip \
  ffmpeg portaudio19-dev espeak-ng \
  curl ca-certificates \
  mpg123

bold "==> external/llama.cpp"
if [ ! -d external/llama.cpp ]; then
  git clone https://github.com/ggml-org/llama.cpp external/llama.cpp
fi
rm -rf external/llama.cpp/build
( cd external/llama.cpp && cmake -B build && cmake --build build --config Release -j "$(nproc)" )

bold "==> external/whisper.cpp"
if [ ! -d external/whisper.cpp ]; then
  git clone https://github.com/ggml-org/whisper.cpp external/whisper.cpp
fi
rm -rf external/whisper.cpp/build
( cd external/whisper.cpp && cmake -B build && cmake --build build --config Release -j "$(nproc)" )

bold "==> Python venv"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
. .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

bold "==> Folders"
mkdir -p models/gemma models/whisper models/kokoro models/wakeword music data

bold "==> Done. Next:"
echo "  1. cp .env.example .env   # then edit HA_TOKEN, LG_TV_ENTITY_ID, etc."
echo "  2. bash scripts/download_models.sh"
echo "  3. (optional) docker compose up -d homeassistant music-assistant searxng"
echo "  4. bash scripts/run_llama_server.sh   (in one terminal)"
echo "  5. bash scripts/run_trusty.sh         (in another)"
echo "  6. bash scripts/run_eyes.sh           (third terminal)"
echo "     then open http://raspberrypi.local:8091/"

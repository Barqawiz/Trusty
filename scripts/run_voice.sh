#!/usr/bin/env bash
# Run the local voice loop on this host (Mac or Pi).
# Requires Trusty + llama-server already running.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -f .env ]; then
  set -a; . .env; set +a
fi

# Mic-agnostic ALSA setup on Linux hosts (Pi). Walks every USB-Audio card
# with a capture device, switches off Automatic Gain Control if that
# control exists, and pushes capture controls to the env-tunable level.
# AGC + max gain on cheap USB mics clips speech — Moonshine returns
# empty STT, Whisper hallucinates. Most pro mics ignore these knobs (no
# such controls). Every command falls through silently with `|| true` so
# the block is a no-op on Mac (no amixer) or with an exotic mic.
#
# Tune via env:
#   MIC_CAPTURE_PCT=100   # 0..100, default 100 (max). Lower if speech clips.
#   MIC_DISABLE_AGC=1     # set to 0 to leave AGC on if you actually want it.
if command -v amixer >/dev/null 2>&1 && [ -r /proc/asound/cards ]; then
  pct="${MIC_CAPTURE_PCT:-100}"
  # POSIX-portable: lines starting with "<digit> [" identify a card.
  for n in $(awk '/^[ ]*[0-9]+[ ]+\[/ {print $1}' /proc/asound/cards 2>/dev/null); do
    # Only touch cards that expose a capture device.
    [ -e "/proc/asound/card${n}/pcm0c/info" ] || continue
    applied=""
    if [ "${MIC_DISABLE_AGC:-1}" = "1" ]; then
      amixer -c "$n" sset 'Auto Gain Control' off >/dev/null 2>&1 && applied="${applied} AGC=off"
    fi
    for ctl in 'Mic' 'Capture' 'Mic Boost'; do
      amixer -c "$n" sset "$ctl" "${pct}%" >/dev/null 2>&1 && applied="${applied} ${ctl}=${pct}%"
    done
    [ -n "$applied" ] && echo "  alsa: card $n applied:${applied}"
  done
fi

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

exec "$PY" -m voice.loop

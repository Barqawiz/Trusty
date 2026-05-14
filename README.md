# Trusty (Gemma)

### Privacy-first local voice assistant for Mac & Raspberry Pi 5.

> Wake phrase: **"Hey Trusty"**.

Microphone audio never leaves the device. Only approved text tool requests may, and every turn is recorded in a local privacy ledger.

## Why Trusty ?
Most offline assistants (Rhasspy, OpenVoiceOS, Home Assistant Assist) match voice against trained intent templates, so only pre-trained phrasings work. **Trusty uses Gemma 4 itself as the orchestrator**, picking tools and arguments by reasoning. The LLM is the brain of the home, not just the chat layer.


## Features

- **Local Gemma 4 (brain and orchestration)** (E2B IT GGUF, Q6_K by default) running on `llama.cpp` switch via `GEMMA_QUANT` in `.env` (Q8_0 / Q6_K / Q5_K_M)
- **Local STT** via Moonshine ONNX (default, ~3× faster than whisper.cpp) or whisper.cpp switch via `STT_BACKEND` in `.env`
- **Local TTS** via Kokoro ONNX (natural voice, ~80 MB int8 model)
- **Local wake word** via openWakeWord
- **Vacuum control** through Home Assistant
- **Music** via Music Assistant + a local folder
- **Live weather** via Open-Meteo (location text only leaves the device)
- **Web search** via SearXNG (query text only leaves the device)
- **Eyes UI** animated eyes on a screen, served locally over WebSocket
- **Admin panel** runtime mode toggle, pause switch, service health, ledger
- **Local memory** voice commands like *"update my location to Dublin"* or *"my name is Ahmad"* route to a `memory` tool that writes to `data/memory.json`. Cleared with *"forget my memory"*. Never leaves the device.

## Architecture

The application is built to guarantee that no audio, voice data, or recordings ever leave the device. When Gemma decides to use an online tool (weather, web search), only the minimum necessary text is sent. Most interactions can also run fully locally in offline mode using admin screen.

![Trusty Architecture](images/trusty_lego_arch.png)

We used Antigravity and Gemini for development.

## Quick start (Mac dev)

`bash scripts/download_models.sh` downloads the **Trusty-tuned Q4_K_S Gemma** from [barqawiz/trusty-gemma-4-e2b-home-assistant](https://huggingface.co/barqawiz/trusty-gemma-4-e2b-home-assistant) (~3.1 GB, public, no token required). The orchestrator detects `trusty` in the filename and auto-loads the short planner prompt.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
bash scripts/download_models.sh   # tuned Gemma + STT + TTS + wake-word, ~5 GB total
docker compose up -d              # HA + Music Assistant + SearXNG

bash scripts/run_llama_server.sh  # terminal A
bash scripts/run_trusty.sh        # terminal B
bash scripts/run_voice.sh         # terminal C needs mic permission

bash scripts/smoke_test.sh        # verify
```

Local URLs:

| Page | URL |
|---|---|
| Eyes UI | [http://localhost:8090/eyes/](http://localhost:8090/eyes/) |
| Admin panel | [http://localhost:8090/admin/](http://localhost:8090/admin/) |

> [!NOTE]
> To A/B against the original un-tuned Google Gemma, set `GEMMA_VARIANT=untuned` and `HF_TOKEN=hf_...` in `.env` (the un-tuned repo is gated; accept the [Gemma licence](https://huggingface.co/google/gemma-4-E2B-it) and create a [read token](https://huggingface.co/settings/tokens)).

Full command flow (Mac + Pi) → [`RUNBOOK.md`](RUNBOOK.md).
Custom wake-word training → [`WAKE_WORD.md`](WAKE_WORD.md).

## Pi easy start

After the Pi is provisioned (see [`RUNBOOK.md`](RUNBOOK.md#pi) for the
one-time setup), every reboot needs **one command** from your laptop:

```bash
ssh <user>@<pi-host> 'bash ~/trusty/boot.sh'
```

**Model on Pi**: the Trusty-tuned build,
`models/gemma/trusty-gemma-4-e2b-tuned-q4_k_s.gguf` (3.1 GB), is what
`bash scripts/download_models.sh` pulls by default. The orchestrator
sees the `trusty` marker in the filename and auto-selects the short
planner prompt (`prompts/planner_system.md`, ~1.5 KB). End-to-end
warm turns land at ~17 s on Pi 5 vs ~277 s for un-tuned Gemma with
the long 11 KB prompt. Full numbers in [`BENCHMARKS.md`](BENCHMARKS.md).

Reattach to watch logs / interact:

```bash
tmux attach -t trusty   # detach with Ctrl-b d
```

Stop everything:

```bash
tmux kill-session -t trusty
```

For any other Linux device (Ubuntu, Debian, etc.) the same flow applies, see the [Linux Setup wiki](https://github.com/Barqawiz/Trusty/wiki/Linux-Setup) for the small adjustments.

## Demo prompts

```
Hey Trusty, what is the capital of Jordan?     → local
Hey Trusty, will it rain in Dublin today?      → weather (location only)
Hey Trusty, search the latest Raspberry Pi news      → search (text only)
Hey Trusty, clean the living room                    → Vacuum via HA
Hey Trusty, play music from my offline folder   → local files
Hey Trusty, send my microphone audio online    → blocked
```

## Privacy promise

| Tool             | Internet | Allowed payload    | Audio leaves? |
|------------------|----------|--------------------|---------------|
| local.answer     | no       | none               | no            |
| home.vacuum      | no (LAN) | none               | no            |
| music            | no       | none               | no            |
| weather.live     | yes      | location text only | no            |
| internet.search  | yes      | query text only    | no            |

Every turn writes one line to `data/privacy_ledger.jsonl`.
Audio uploads are hard-locked off in `app/main.py` the admin endpoint
returns HTTP 403 if anything tries to flip them on.

## Project layout

```
app/        FastAPI orchestrator, planner, validator, ledger
voice/      wake / STT / TTS / mic loop
tools/      per-tool adapters (HA, weather, search, music, ...)
prompts/    Gemma planner + final-answer prompts
config/     tools.yaml, privacy_policy.yaml, offline_mode.yaml
ui/eyes/    Eyes UI (HTML / CSS / canvas)
ui/admin/   Admin panel (HTML / CSS / JS)
scripts/    download_models, run_*, setup_pi, smoke_test
systemd/    Pi service units
docker-compose.yml   HA + Music Assistant + SearXNG
```

## Hardware

<img src="images/pi5_.png" alt="Raspberry Pi 5 Trusty hardware" height="600px" />

Trusty is built for the **Raspberry Pi**, but the same stack
also runs on **macOS** (Apple Silicon and Intel) for development and
day-to-day use.

- Raspberry Pi 5 (**8 GB RAM**)
- USB or 3.5 mm speaker
- USB microphone
- Optional: HDMI display, Vacuum on the same LAN

You can run Trusty on a Raspberry Pi (**4 GB RAM**) by offloading Home Assistant and Music Assistant to other devices on your local network, keeping the Pi dedicated to Gemma (via llama.cpp), speech recognition, and audio. The Pi can still reach home and music services using the IP addresses.

Also runs on macOS (Apple Silicon / Intel) see *Quick start (Mac dev)* above

## Documentation

- [`RUNBOOK.md`](RUNBOOK.md)  commands only, Mac + Pi
- [`VACUUM_SETUP.md`](VACUUM_SETUP.md)  pair a vacuum with Home Assistant
- [`WAKE_WORD.md`](WAKE_WORD.md)  change the wake word, including custom training

## Memory tips for low-RAM devices

If you hit memory pressure on Pi 8 GB, these changes save ~650 MB with zero quality impact:

| Change | RAM saved | Risk |
|---|---|---|
| `--ctx-size 1024` in `scripts/run_llama_server.sh` | ~400 MB | none |
| `--parallel 1 --n-predict 256` in `scripts/run_llama_server.sh` | ~150 MB | none |
| Skip SearXNG container in `docker-compose.yml` | ~100 MB | none if you don't use search |

### Which Gemma the download script fetches (`GEMMA_VARIANT`)

`bash scripts/download_models.sh` reads two env vars from `.env`:

| Var | Default | Effect |
|---|---|---|
| `GEMMA_VARIANT` | `tuned` | `tuned` pulls the Trusty-tuned Q4_K_S from [`barqawiz/trusty-gemma-4-e2b-home-assistant`](https://huggingface.co/barqawiz/trusty-gemma-4-e2b-home-assistant) (public, no `HF_TOKEN`). `untuned` pulls the original Google Gemma from `unsloth/gemma-3n-E2B-it-GGUF` (gated, needs `HF_TOKEN`). |
| `GEMMA_QUANT` | `Q6_K` | Only consulted when `GEMMA_VARIANT=untuned`. Allowed: `Q8_0` / `Q6_K` / `Q5_K_M` / `Q4_K_M`. |

So with all defaults you get one file:

```
models/gemma/trusty-gemma-4-e2b-tuned-q4_k_s.gguf      # 3.1 GB, tuned
```

This is what's recommended for **both Mac and Pi**. The orchestrator
detects `trusty` in the filename and loads the short 1.5 KB planner
prompt automatically; warm-turn latency on Pi 5 is ~17 s vs ~277 s for
un-tuned Gemma with the long 11 KB prompt (see
[`BENCHMARKS.md`](BENCHMARKS.md)).

#### Tuned vs original Gemma: which to use

- **On a Mac or any other computer with plenty of RAM**: you can switch
  to the original Google Gemma weights; they route well at higher quants
  and are useful for A/B comparison.
- **On a Pi 5 or other small device**: stay on the Trusty-tuned default.
  The short planner prompt and Q4_K_S quant make the difference between
  ~17 s and ~277 s per turn.

To opt into the un-tuned baseline, set these in `.env` and re-run the
download script:

```bash
GEMMA_VARIANT=untuned
GEMMA_QUANT=Q6_K                                    # or Q8_0 / Q5_K_M / Q4_K_M
HF_TOKEN=hf_...                                     # required for gated Gemma
GEMMA_MODEL_PATH=models/gemma/gemma-4-e2b-it-q6_k.gguf
```

```bash
bash scripts/download_models.sh
```

Un-tuned quant trade-offs:

| Quant | Size | Mac routing (37-q) | When to use |
|---|---|---|---|
| `Q8_0` | ~4.8 GB | reference | Mac dev, plenty of RAM |
| `Q6_K` (default) | 4.29 GB | 97 % | Mac / desktop A/B baseline |
| `Q5_K_M` | 3.20 GB | n/a | tight RAM, untested |
| `Q4_K_M` | 2.96 GB | 97 % | Mac alternative |
| `Q4_K_S` (un-tuned) | 2.83 GB | 3 % | broken un-tuned; use the tuned build above |

**Pair Whisper to match.** Smaller Gemma → smaller Whisper, otherwise STT
becomes the bottleneck on Pi:

| `GEMMA_QUANT` | Recommended `WHISPER_MODEL_PATH` | STT latency on Pi 5 |
|---|---|---|
| `Q8_0` / `Q6_K` | `ggml-small.en.bin` | ~6–8 s |
| `Q4_K_M` / `Q4_K_S` | `ggml-base.en.bin` | ~2–3 s |

`base.en` is slightly less accent-tolerant but ~3× faster on CPU. The Pi
preset (Q4_K_S + base.en) is the right default for production voice use.

## Speech to text (`STT_BACKEND`)

Two interchangeable backends, both fully on-device. Switch via
`STT_BACKEND` in `.env`. The dispatcher in [`voice/stt.py`](voice/stt.py)
routes to the chosen backend with no fallback.

| Backend | Size | Pi 5 latency (5 s clip) | Accent handling | Notes |
|---|---|---|---|---|
| **`moonshine`** (default) | ~120 MB | **~0.9 s** | strong | Useful Sensors' edge STT, ONNX, runs offline from `models/moonshine/<size>/` |
| `whisper` | 142 MB – 1.4 GB | 2–8 s depending on model | strong (small.en) | classic `whisper.cpp` via subprocess, paired with the Gemma quant |

Moonshine is the right default for a fluent voice assistant on Pi 5.
Whisper is kept as the alternative for cases that need its richer
multilingual coverage or where you've already validated a specific
Whisper variant for your accent.

**Privacy:** both backends run with the model files held inside the
project (`models/moonshine/...` or `models/whisper/...`). No outbound
calls at runtime. Moonshine's loader passes an explicit `models_dir` to
ONNX Runtime so the Hugging Face Hub code path is never touched after
the initial download.

To switch:

```bash
# .env
STT_BACKEND=moonshine    # or whisper
MOONSHINE_MODEL=base     # tiny | base
```

Restart only the voice loop (`pkill -f voice.loop && bash scripts/run_voice.sh`).

## License

MIT.

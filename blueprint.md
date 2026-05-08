# Trusty README blueprint

## Project name

**Trusty**

**Tagline:** Like Alexa, but powered by Gemma 4, local-first, and privacy-verifiable on Raspberry Pi 5 plus AI HAT.

**Wake phrase:**

```text
Hey Trusty
```

**Competition target:**

```text
Impact Track: Safety and Trust
Special Technology Track: llama.cpp
```

## MVP scope

Trusty is a one-week MVP focused on voice, home control, TV control, music, weather, web search, and a live eyes screen.

Camera is intentionally out of scope for this version.

```text
Included now:
  Microphone
  Speaker
  Gemma 4 local model
  Home Assistant device control
  LG Smart TV demo
  Music Assistant
  Local music folder
  Open-Meteo weather
  SearXNG internet search
  Trusty Eyes screen

Skipped for now:
  Camera
  Frigate
  Hailo camera detection
  Vision events
  Image understanding
```

The AI HAT remains part of the target hardware story, but this MVP does not depend on camera or vision features. Camera support can be added later as a separate milestone.

## Core privacy promise

```text
Microphone audio never leaves the Raspberry Pi.
Wake-word audio never leaves the Raspberry Pi.
Speech recordings never leave the Raspberry Pi.
Raw home sensor logs never leave the Raspberry Pi.

Only approved text tool requests may leave the device.
```

Examples:

```text
User says:
  Hey Trusty, what is the capital of Jordan?

What happens:
  Audio is transcribed locally.
  Gemma answers locally.
  No internet is used.

User says:
  Hey Trusty, search the weather in Dublin today.

What happens:
  Audio is transcribed locally.
  Gemma selects the weather tool.
  Only the weather request text or derived location query is sent.
  The microphone audio is not sent.
```

## Live information policy

Trusty does not ask for confirmation when the request clearly needs live information. If the request is live, current, weather-related, news-related, price-related, schedule-related, or explicitly says search, Trusty uses the appropriate internet tool when online mode is enabled.

```text
General knowledge:
  Answer locally from Gemma.

Live information:
  Use the relevant internet tool.

User says "search":
  Use SearXNG internet search.

Weather request:
  Use Open-Meteo.

Offline mode:
  Block all internet tools.
```

Examples:

| User request | Trusty behavior |
|---|---|
| What is the capital of Jordan? | Local Gemma answer |
| What is MQTT? | Local Gemma answer |
| Who is the current CEO of Apple? | Internet search, because current roles can change |
| Will it rain in Dublin today? | Open-Meteo weather tool |
| Search Raspberry Pi AI HAT examples | SearXNG search |
| Play offline music | Local music folder |
| Open YouTube on the TV | Home Assistant LG TV control |

Gemma 4 can be used for tool calling without fine-tuning for the first MVP. The model proposes tool calls, but Trusty validates and executes them safely. Google documents Gemma function calling here: https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4

## Important TTS decision

Piper is fast and stable, but the goal for Trusty is a more natural, personal, and fluent voice. For this MVP, Trusty uses **Kokoro ONNX** as the single TTS engine.

Fixed voice stack:

```text
Wake word: openWakeWord
Speech to text: whisper.cpp
Text to speech: Kokoro ONNX
```

No fallback TTS. No fallback STT.

Kokoro project links:

```text
Main model project:
  https://github.com/hexgrad/kokoro

ONNX wrapper:
  https://github.com/thewh1teagle/kokoro-onnx
```

## Architecture

```text
Raspberry Pi 5 plus AI HAT
  Microphone
  Speaker
  HDMI display, browser screen, or TV browser
  Local network
        |
        v
Local voice layer
  openWakeWord
  whisper.cpp
  Kokoro ONNX
        |
        v
Trusty Orchestrator
  FastAPI app
  Tool registry
  Privacy validator
  Internet policy
  Offline mode switch
  Privacy ledger
  Live eyes web UI
        |
        v
Gemma 4 E2B IT GGUF via llama.cpp
  Same model for:
    tool planning
    local answers
    final response generation
        |
        v
Tool layer
  Home Assistant Core
  LG webOS TV integration
  Music Assistant
  Local music folder
  Open-Meteo
  SearXNG
        |
        v
Output layer
  Kokoro ONNX voice
  LG TV action or notification
  Speaker
  Music playback
  Browser eyes UI
  Privacy dashboard
```

## One Gemma model only

Use one model:

```text
Gemma 4 E2B IT GGUF
```

It is the same local model running in llama.cpp. It is not multiple models. It has multiple jobs.

| Use case | Model | Runtime | Prompt type |
|---|---|---|---|
| Tool planner | Gemma 4 E2B IT GGUF | llama.cpp | JSON tool call |
| Local answer | Gemma 4 E2B IT GGUF | llama.cpp | General answer |
| Final response | Gemma 4 E2B IT GGUF | llama.cpp | Short spoken response |

Gemma llama.cpp documentation:

```text
https://ai.google.dev/gemma/docs/integrations/llamacpp
```

## Main stack

| Layer | Selected project | Role |
|---|---|---|
| LLM runtime | llama.cpp | Runs Gemma locally on Pi 5 |
| LLM model | Gemma 4 E2B IT GGUF | One model for planning, answering, and final reply |
| Wake word | openWakeWord | Detects "Hey Trusty" locally |
| Speech to text | whisper.cpp | Converts audio to text locally |
| Text to speech | Kokoro ONNX | Natural local voice |
| Home control | Home Assistant Core | Device control hub |
| LG TV | Home Assistant LG webOS TV integration | TV control demo |
| Music | Music Assistant | Music playback |
| Offline music | Local folder | Music without internet |
| Weather | Open-Meteo | Live weather |
| Internet search | SearXNG | Text-only web search |
| API app | FastAPI | Trusty orchestrator |
| Validation | Pydantic | Tool-call validation |
| Eyes UI | Trusty Eyes HTML | Live assistant face on screen |

## Apps, models, downloads, folders, and environment variables

| Component | Project link | Purpose | Can script download it? | Manual step | Store path | Required `.env` variables |
|---|---|---|---|---|---|---|
| llama.cpp | https://github.com/ggml-org/llama.cpp | Local Gemma runtime | Yes | Build may take time on Pi | `/opt/trusty/llama.cpp` | `LLAMA_CPP_DIR`, `LLAMA_HOST`, `LLAMA_PORT`, `LLAMA_BASE_URL` |
| Gemma 4 E2B IT GGUF | https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF | Main and only LLM | Yes, if Hugging Face access works | Accept model terms if required | `/opt/trusty/models/gemma/gemma-4-e2b-it.gguf` | `GEMMA_MODEL_PATH`, `HF_TOKEN` optional |
| openWakeWord | https://github.com/dscripka/openWakeWord | Local wake word | Yes | Optional custom wake word model | `/opt/trusty/models/wakeword` | `WAKEWORD_NAME`, `WAKEWORD_MODEL_PATH` |
| whisper.cpp | https://github.com/ggml-org/whisper.cpp | Local STT | Yes | Download Whisper model | `/opt/trusty/models/whisper/ggml-base.en.bin` | `WHISPER_CPP_DIR`, `WHISPER_MODEL_PATH` |
| Kokoro ONNX | https://github.com/thewh1teagle/kokoro-onnx | Natural local TTS | Yes | Download model and voices files | `/opt/trusty/models/kokoro` | `KOKORO_MODEL_PATH`, `KOKORO_VOICES_PATH`, `KOKORO_VOICE`, `KOKORO_LANG`, `KOKORO_SPEED` |
| Kokoro model files | https://github.com/thewh1teagle/kokoro-onnx/releases | ONNX voice model files | Yes | Choose int8 model for Pi | `/opt/trusty/models/kokoro/kokoro-v1.0.int8.onnx` and `/opt/trusty/models/kokoro/voices-v1.0.bin` | Same Kokoro variables |
| Home Assistant Core | https://github.com/home-assistant/core | Home device control | Yes, Docker recommended | Pair devices in UI | Docker volume `ha_config` | `HA_URL`, `HA_TOKEN` |
| Home Assistant REST API | https://developers.home-assistant.io/docs/api/rest/ | Trusty calls services | No install needed | Create long-lived token | Home Assistant UI | `HA_URL`, `HA_TOKEN` |
| LG webOS TV integration | https://www.home-assistant.io/integrations/webostv/ | LG TV control | Installed inside HA | Pair the TV manually | Home Assistant config | `LG_TV_ENTITY_ID` |
| aiowebostv | https://github.com/home-assistant-libs/aiowebostv | Reference library for direct LG TV control | Yes | Not needed for week one if HA works | Python package | None for MVP |
| Music Assistant | https://github.com/music-assistant/server | Music playback | Yes, Docker recommended | Add players and local music source | Docker volume `ma_data` | `MUSIC_ASSISTANT_URL`, `MUSIC_PLAYER_ID` |
| Music Assistant filesystem | https://www.music-assistant.io/music-providers/filesystem/ | Offline music library | No separate install | Mount local folder | `/home/pi/trusty/music` | `LOCAL_MUSIC_DIR` |
| Open-Meteo forecast API | https://github.com/open-meteo/open-meteo | Weather forecast | No local install needed | None | Not stored | None required |
| Open-Meteo geocoding API | https://open-meteo.com/en/docs/geocoding-api | Converts user-provided location text into coordinates | No local install needed | None | Not stored | None required |
| SearXNG | https://github.com/searxng/searxng | Internet search | Yes, Docker recommended | Configure engines if needed | Docker volume `searxng_data` | `SEARXNG_URL` |
| FastAPI | https://github.com/FastAPI/FastAPI | Trusty API server | Yes, pip | None | Python venv | `TRUSTY_HOST`, `TRUSTY_PORT` |
| Pydantic | https://github.com/pydantic/pydantic | JSON validation | Yes, pip | None | Python venv | None |
| Trusty Eyes UI | Inspired by https://github.com/Barqawiz/Tamagotchi/blob/main/eyes.html | Live eyes on screen | Yes, part of repo | Open browser at local URL | `trusty/ui/eyes` | `EYES_ENABLED`, `EYES_PORT` |

## `.env.example`

```env
# Trusty core
TRUSTY_HOST=0.0.0.0
TRUSTY_PORT=8090
TRUSTY_MODE=online

# Privacy policy
ALLOW_INTERNET=true
ALLOW_AUDIO_UPLOAD=false
ALLOW_HOME_LOG_UPLOAD=false

# llama.cpp
LLAMA_CPP_DIR=/opt/trusty/llama.cpp
LLAMA_HOST=127.0.0.1
LLAMA_PORT=8080
LLAMA_BASE_URL=http://127.0.0.1:8080/v1
GEMMA_MODEL_PATH=/opt/trusty/models/gemma/gemma-4-e2b-it.gguf

# Optional Hugging Face token for scripted model download
HF_TOKEN=

# Wake word
WAKEWORD_NAME=hey_trusty
WAKEWORD_MODEL_PATH=/opt/trusty/models/wakeword/hey_trusty.tflite

# Speech to text
WHISPER_CPP_DIR=/opt/trusty/whisper.cpp
WHISPER_MODEL_PATH=/opt/trusty/models/whisper/ggml-base.en.bin

# Text to speech
KOKORO_MODEL_PATH=/opt/trusty/models/kokoro/kokoro-v1.0.int8.onnx
KOKORO_VOICES_PATH=/opt/trusty/models/kokoro/voices-v1.0.bin
KOKORO_VOICE=af_heart
KOKORO_LANG=en-us
KOKORO_SPEED=1.0

# Home Assistant
HA_URL=http://homeassistant.local:8123
HA_TOKEN=replace_with_long_lived_access_token
LG_TV_ENTITY_ID=media_player.lg_webos_tv

# Music
MUSIC_ASSISTANT_URL=http://localhost:8095
MUSIC_PLAYER_ID=media_player.lg_webos_tv
LOCAL_MUSIC_DIR=/home/pi/trusty/music

# Search
SEARXNG_URL=http://localhost:8088

# Eyes UI
EYES_ENABLED=true
EYES_PORT=8091
```

Weather does not use default latitude, longitude, or default location variables. Trusty extracts the location from the user request and sends that location text to the Open-Meteo geocoding API. If the user does not provide a location and no location is available in the request, Trusty asks for the location.

Example:

```text
User:
  Hey Trusty, will it rain in Dublin today?

Tool input:
  location_text = Dublin

User:
  Hey Trusty, will it rain today?

Trusty:
  Which location should I check?
```

## Tool policy

Default behavior:

```text
General knowledge:
  local.answer

Current or live information:
  weather.live or internet.search

User explicitly says search:
  internet.search

TV or home command:
  home.tv or home.control

Offline music:
  music.play_local_folder

Privacy violation:
  none
```

## Tool schema

```yaml
tools:
  local.answer:
    backend: gemma_local
    internet: false
    offline: true
    sends_audio: false
    description: Answer general knowledge from local Gemma model.

  home.tv:
    backend: home_assistant
    internet: false
    offline: true
    sends_audio: false
    actions:
      - turn_on
      - turn_off
      - open_app
      - select_source
      - volume_up
      - volume_down
      - mute
      - show_notification
      - get_state

  music:
    backend: music_assistant
    internet: conditional
    offline: true
    sends_audio: false
    actions:
      - play_local_folder
      - play_local_file
      - pause
      - resume
      - volume

  weather.live:
    backend: open_meteo
    internet: true
    offline: false
    sends_audio: false
    allowed_external_payload:
      - location_text
      - derived_coordinates
      - weather_query
    forbidden_payload:
      - audio
      - home_logs

  internet.search:
    backend: searxng
    internet: true
    offline: false
    sends_audio: false
    allowed_external_payload:
      - text_query
    forbidden_payload:
      - audio
      - home_logs
```

## Gemma planner prompt

```text
You are Trusty, a local Gemma 4 tool orchestrator running on Raspberry Pi 5.

You must choose exactly one tool action.

Return only valid JSON.

Core privacy rules:
1. Never send microphone audio to the internet.
2. Never send wake-word audio to the internet.
3. Never send raw home sensor logs to the internet.
4. Internet tools may receive only minimal text queries.
5. Weather tools may receive only location text or derived coordinates.
6. If offline_mode is true, do not use internet.search or weather.live.
7. If the user asks general knowledge, use local.answer.
8. If the user asks current, latest, live, today, now, weather, news, price, schedule, or search, use the correct internet tool when online mode is enabled.
9. If the user asks weather but gives no location, choose none with action ask_for_location.
10. If the command controls the LG TV, use home.tv.
11. If the command asks for offline music, use music.play_local_folder or music.play_local_file.
12. If the request violates privacy rules, choose none.

Available tools:
{{TOOLS_JSON}}

Mode:
{{MODE}}

Local context:
{{LOCAL_CONTEXT}}

User request:
{{USER_TEXT}}

Return JSON with this shape:
{
  "tool": "local.answer|home.tv|music|weather.live|internet.search|none",
  "action": "...",
  "arguments": {},
  "requires_internet": false,
  "external_payload": "none|text_query_only|location_only",
  "privacy_risk": "low|medium|high",
  "reason": "...",
  "final_response_required": true
}
```

## Final answer prompt

```text
You are Trusty, a warm and concise local voice assistant.

Generate a short spoken answer.

Rules:
1. Be natural, friendly, and brief.
2. Do not invent tool results.
3. Do not say you searched unless a search or weather tool was actually used.
4. If internet was used, mention that only text was sent when privacy is relevant.
5. Never claim microphone audio was sent.
6. If offline mode blocked a tool, say that simply.
7. Keep answers suitable for speech.

User request:
{{USER_TEXT}}

Tool call:
{{TOOL_CALL}}

Tool result:
{{TOOL_RESULT}}

Privacy ledger:
{{PRIVACY_LEDGER}}

Final answer:
```

## Privacy ledger format

Every command creates a ledger entry.

```json
{
  "assistant": "Trusty",
  "hardware": "Raspberry Pi 5 plus AI HAT",
  "runtime": "llama.cpp",
  "model": "Gemma 4 E2B IT GGUF",
  "mode": "online",
  "user_text": "Search the weather in Dublin today.",
  "tool": "weather.live",
  "internet_used": true,
  "external_payload": "location_only",
  "audio_left_device": false,
  "home_logs_left_device": false
}
```

Local answer example:

```json
{
  "assistant": "Trusty",
  "hardware": "Raspberry Pi 5 plus AI HAT",
  "runtime": "llama.cpp",
  "model": "Gemma 4 E2B IT GGUF",
  "mode": "online",
  "user_text": "What is the capital of Jordan?",
  "tool": "local.answer",
  "internet_used": false,
  "external_payload": "none",
  "audio_left_device": false,
  "home_logs_left_device": false
}
```

## Trusty Eyes UI

Trusty Eyes is a local HTML screen inspired by the Tamagotchi eyes page from this repository:

```text
https://github.com/Barqawiz/Tamagotchi/blob/main/eyes.html
```

The original page uses a canvas, a pair of cartoon eyes, and states such as idle, follow mouse, blink, surprised, sleepy, bored, and look around. Trusty adapts that style for assistant states instead of manual demo buttons.

Purpose:

```text
Show animated eyes.
Show assistant state.
Show privacy status.
Show when Trusty is idle, listening, thinking, speaking, searching, offline, or blocked.
```

States:

```text
idle:
  calm blinking eyes

listening:
  eyes follow subtle movement and status says Listening locally

thinking:
  eyes look left and right and status says Thinking locally

speaking:
  eyes pulse and status shows the spoken caption

searching:
  surprised eyes and status says Internet text only

offline:
  sleepy eyes and status says Offline mode

blocked:
  bored or skeptical eyes and status says Privacy blocked
```

Implementation notes:

```text
Use canvas like the Tamagotchi eyes implementation.
Remove demo buttons in production mode.
Add WebSocket state updates from Trusty.
Keep all HTML, CSS, and JavaScript local.
Do not use CDN assets.
Do not call external APIs from the eyes UI.
```

Suggested files:

```text
trusty/ui/eyes/index.html
trusty/ui/eyes/styles.css
trusty/ui/eyes/eyes.js
```

The eyes page connects to Trusty over a local WebSocket:

```text
ws://raspberrypi.local:8090/ws/state
```

Example WebSocket message:

```json
{
  "state": "speaking",
  "caption": "YouTube is open on the TV.",
  "privacy": {
    "audio_left_device": false,
    "internet_used": false,
    "external_payload": "none"
  }
}
```

## Trusty Eyes starter HTML

This is a single-file starter based on the Tamagotchi canvas style, adapted for Trusty states.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trusty Eyes</title>
  <style>
    body {
      margin: 0;
      padding: 0;
      background: #ffffff;
      color: #000000;
      font-family: Arial, sans-serif;
      overflow: hidden;
    }

    #container {
      height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 24px;
    }

    #eyesCanvas {
      width: min(90vw, 720px);
      height: auto;
      background-color: #eeeeee;
      border: 2px solid #000;
      border-radius: 20px;
      box-shadow: 0 20px 80px rgba(0, 0, 0, 0.18);
    }

    #status {
      font-size: clamp(20px, 3vw, 38px);
      font-weight: 700;
      text-align: center;
    }

    #privacy {
      font-size: clamp(14px, 2vw, 20px);
      text-align: center;
      opacity: 0.78;
      line-height: 1.5;
    }
  </style>
</head>
<body>
  <div id="container">
    <canvas id="eyesCanvas" width="500" height="400"></canvas>
    <div id="status">Hey Trusty</div>
    <div id="privacy">audio: local | internet: idle</div>
  </div>

  <script>
    const canvas = document.getElementById('eyesCanvas');
    const ctx = canvas.getContext('2d');
    const statusText = document.getElementById('status');
    const privacyText = document.getElementById('privacy');

    const eyeRadius = 80;
    const irisRadiusDefault = 30;
    const pupilRadiusDefault = 12;
    const eyeOffsetX = 120;
    const eyeCenterY = canvas.height / 2;
    const leftEyeCenterX = canvas.width / 2 - eyeOffsetX;
    const rightEyeCenterX = canvas.width / 2 + eyeOffsetX;

    let mode = 'idle';
    let isBlinking = false;
    let blinkProgress = 0;
    let lookAngle = 0;
    let lookDirection = 1;
    let lastTime = 0;

    function draw(timestamp) {
      const deltaTime = timestamp - lastTime;
      lastTime = timestamp;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      if (mode === 'thinking') {
        lookAngle += 0.0025 * deltaTime * lookDirection;
        if (Math.abs(lookAngle) > Math.PI / 4) lookDirection *= -1;
      }

      drawEye(leftEyeCenterX, eyeCenterY);
      drawEye(rightEyeCenterX, eyeCenterY);
      requestAnimationFrame(draw);
    }

    function drawEye(centerX, centerY) {
      ctx.save();

      let currentEyeRadius = eyeRadius;
      let currentIrisRadius = irisRadiusDefault;
      let currentPupilRadius = pupilRadiusDefault;

      if (mode === 'searching') {
        currentEyeRadius = eyeRadius * 1.15;
        currentIrisRadius = irisRadiusDefault * 1.15;
        currentPupilRadius = pupilRadiusDefault * 1.15;
      }

      ctx.beginPath();
      ctx.arc(centerX, centerY, currentEyeRadius, 0, Math.PI * 2);
      ctx.fillStyle = '#FFFFFF';
      ctx.fill();
      ctx.strokeStyle = '#000000';
      ctx.lineWidth = 5;
      ctx.stroke();

      let irisX = centerX;
      let irisY = centerY;

      if (mode === 'thinking') {
        const maxOffset = currentEyeRadius - currentIrisRadius - 10;
        irisX = centerX + Math.sin(lookAngle) * maxOffset;
      } else if (mode === 'listening') {
        irisY = centerY - 10;
      } else if (mode === 'speaking') {
        const pulse = Math.sin(Date.now() / 90) * 5;
        currentIrisRadius += pulse;
      }

      ctx.beginPath();
      ctx.arc(irisX, irisY, currentIrisRadius, 0, Math.PI * 2);
      ctx.fillStyle = mode === 'blocked' ? '#ff66cc' : '#1E90FF';
      ctx.fill();

      ctx.beginPath();
      ctx.arc(irisX, irisY, currentPupilRadius, 0, Math.PI * 2);
      ctx.fillStyle = '#000000';
      ctx.fill();

      ctx.beginPath();
      ctx.arc(irisX - currentPupilRadius / 2.5, irisY - currentPupilRadius / 2.5, currentPupilRadius / 3, 0, Math.PI * 2);
      ctx.fillStyle = '#FFFFFF';
      ctx.fill();

      if (isBlinking || mode === 'offline') drawBlink(centerX, centerY, currentEyeRadius);
      if (mode === 'blocked') drawSkepticalEyebrow(centerX, centerY, currentEyeRadius);

      ctx.restore();
    }

    function drawBlink(centerX, centerY, radius) {
      const blinkAmount = mode === 'offline' ? 0.55 : blinkProgress / 100;
      ctx.save();
      ctx.beginPath();
      ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
      ctx.closePath();
      ctx.clip();
      ctx.fillStyle = '#cccccc';
      ctx.beginPath();
      ctx.rect(centerX - radius, centerY - radius, radius * 2, radius * blinkAmount * 2);
      ctx.fill();
      ctx.restore();
    }

    function drawSkepticalEyebrow(centerX, centerY, radius) {
      ctx.save();
      ctx.strokeStyle = '#000000';
      ctx.lineWidth = 5;
      ctx.beginPath();
      ctx.moveTo(centerX - radius / 1.5, centerY - radius * 1.1);
      ctx.lineTo(centerX + radius / 1.5, centerY - radius * 0.9);
      ctx.stroke();
      ctx.restore();
    }

    function startBlink() {
      if (isBlinking) return;
      isBlinking = true;
      blinkProgress = 0;
      let closing = true;
      const blinkInterval = setInterval(() => {
        if (closing) {
          blinkProgress += 10;
          if (blinkProgress >= 100) {
            blinkProgress = 100;
            closing = false;
          }
        } else {
          blinkProgress -= 10;
          if (blinkProgress <= 0) {
            blinkProgress = 0;
            isBlinking = false;
            clearInterval(blinkInterval);
          }
        }
      }, 30);
    }

    function setTrustyState(message) {
      mode = message.state || 'idle';
      statusText.textContent = message.caption || 'Hey Trusty';
      const p = message.privacy || {};
      privacyText.textContent =
        'audio: ' + (p.audio_left_device ? 'sent' : 'local') +
        ' | internet: ' + (p.internet_used ? (p.external_payload || 'text only') : 'idle');
    }

    setInterval(() => {
      if (mode === 'idle') startBlink();
    }, 4000);

    try {
      const ws = new WebSocket('ws://' + location.hostname + ':8090/ws/state');
      ws.onmessage = event => setTrustyState(JSON.parse(event.data));
      ws.onerror = () => setTrustyState({
        state: 'offline',
        caption: 'Trusty UI waiting for assistant',
        privacy: { audio_left_device: false, internet_used: false }
      });
    } catch (error) {
      setTrustyState({
        state: 'offline',
        caption: 'Trusty UI waiting for assistant',
        privacy: { audio_left_device: false, internet_used: false }
      });
    }

    requestAnimationFrame(draw);
    setTrustyState({
      state: 'idle',
      caption: 'Hey Trusty',
      privacy: { audio_left_device: false, internet_used: false }
    });
  </script>
</body>
</html>
```

## Example flows

### General local answer

```text
User:
  Hey Trusty, what is the capital of Jordan?

Gemma planner:
{
  "tool": "local.answer",
  "action": "answer",
  "arguments": {
    "question": "What is the capital of Jordan?"
  },
  "requires_internet": false,
  "external_payload": "none",
  "privacy_risk": "low",
  "reason": "This is general knowledge.",
  "final_response_required": true
}

Final response:
  The capital of Jordan is Amman.
```

### LG TV control

```text
User:
  Hey Trusty, open YouTube on the TV.

Gemma planner:
{
  "tool": "home.tv",
  "action": "open_app",
  "arguments": {
    "app": "YouTube"
  },
  "requires_internet": false,
  "external_payload": "none",
  "privacy_risk": "low",
  "reason": "The user wants to control the LG TV.",
  "final_response_required": true
}

Final response:
  YouTube is open on the TV.
```

### Weather

```text
User:
  Hey Trusty, will it rain in Dublin today?

Gemma planner:
{
  "tool": "weather.live",
  "action": "rain_probability",
  "arguments": {
    "location_text": "Dublin"
  },
  "requires_internet": true,
  "external_payload": "location_only",
  "privacy_risk": "low",
  "reason": "Weather is live information.",
  "final_response_required": true
}

Final response:
  I checked the live forecast. Only the location request was sent, not your voice.
```

### Weather with missing location

```text
User:
  Hey Trusty, will it rain today?

Gemma planner:
{
  "tool": "none",
  "action": "ask_for_location",
  "arguments": {
    "message": "Which location should I check?"
  },
  "requires_internet": false,
  "external_payload": "none",
  "privacy_risk": "low",
  "reason": "Weather requires a location and none was provided.",
  "final_response_required": true
}

Final response:
  Which location should I check?
```

### Internet search

```text
User:
  Hey Trusty, search Raspberry Pi AI HAT Gemma examples.

Gemma planner:
{
  "tool": "internet.search",
  "action": "research",
  "arguments": {
    "query": "Raspberry Pi AI HAT Gemma llama.cpp examples"
  },
  "requires_internet": true,
  "external_payload": "text_query_only",
  "privacy_risk": "medium",
  "reason": "The user explicitly asked to search.",
  "final_response_required": true
}

Final response:
  I found a few relevant results. Only the text search query was sent.
```

### Offline music

```text
User:
  Hey Trusty, play music from my offline folder.

Gemma planner:
{
  "tool": "music",
  "action": "play_local_folder",
  "arguments": {
    "folder": "/home/pi/trusty/music",
    "mode": "shuffle"
  },
  "requires_internet": false,
  "external_payload": "none",
  "privacy_risk": "low",
  "reason": "The user asked for offline music.",
  "final_response_required": true
}

Final response:
  Playing your offline music.
```

## One-week execution plan

## Day 1: Local Gemma and Trusty server

Goal:

```text
Gemma 4 runs locally through llama.cpp.
Trusty can send prompts and receive JSON.
```

Build:

```text
llama.cpp
Gemma 4 E2B IT GGUF
FastAPI server
Pydantic tool schema
```

Tasks:

```text
1. Install Raspberry Pi OS 64-bit.
2. Install build tools.
3. Build llama.cpp.
4. Download Gemma 4 E2B IT GGUF.
5. Run llama-server on port 8080.
6. Create Trusty FastAPI app on port 8090.
7. Add planner prompt.
8. Validate JSON output with Pydantic.
```

Test commands:

```text
Open YouTube on the TV.
What is the capital of Jordan?
Search Raspberry Pi AI HAT examples.
```

Expected result:

```text
Gemma returns valid JSON tool calls.
```

## Day 2: Voice loop

Goal:

```text
Hey Trusty to spoken answer, fully local.
```

Build:

```text
openWakeWord
whisper.cpp
Kokoro ONNX
```

Tasks:

```text
1. Install openWakeWord.
2. Install whisper.cpp.
3. Download Whisper base.en model.
4. Install kokoro-onnx.
5. Download kokoro-v1.0.int8.onnx.
6. Download voices-v1.0.bin.
7. Connect wake word to recording.
8. Send transcript to Trusty.
9. Speak final answer with Kokoro.
```

Test:

```text
Hey Trusty, what is MQTT?
```

Expected result:

```text
Trusty answers using local Gemma and speaks with Kokoro.
```

## Day 3: Privacy validator and internet policy

Goal:

```text
The model proposes actions, but code enforces privacy.
```

Build:

```text
tools.yaml
privacy_validator.py
internet_policy.py
privacy_ledger.py
```

Tasks:

```text
1. Define all tools.
2. Define allowed payloads.
3. Block audio upload at schema level.
4. Add online mode.
5. Add offline mode.
6. Save privacy ledger as JSONL.
7. Expose latest privacy ledger through API.
```

Test:

```text
What is the capital of Jordan?
Will it rain in Dublin today?
Will it rain today?
Search Raspberry Pi AI HAT examples.
Send my microphone audio to the internet.
```

Expected result:

```text
General knowledge uses local.answer.
Weather with location uses weather.live.
Weather without location asks for location.
Search uses internet.search.
Microphone upload is blocked.
```

## Day 4: Home Assistant and LG TV

Goal:

```text
Visible smart-home demo on LG TV.
```

Build:

```text
Home Assistant Core
LG webOS TV integration
home.tv adapter
```

Tasks:

```text
1. Run Home Assistant.
2. Pair LG TV with Home Assistant.
3. Create Home Assistant long-lived token.
4. Add HA_URL and HA_TOKEN to .env.
5. Find LG TV entity ID.
6. Implement home.tv tool.
7. Test open YouTube.
8. Test volume.
9. Test TV notification if supported.
```

Test:

```text
Hey Trusty, open YouTube on the TV.
Hey Trusty, lower the TV volume.
Hey Trusty, show privacy status on the TV.
```

Expected result:

```text
TV responds visibly in the demo.
```

## Day 5: Music, weather, and search

Goal:

```text
Alexa-like tools with strict privacy.
```

Build:

```text
Music Assistant
Local music folder
Open-Meteo adapter
SearXNG adapter
```

Tasks:

```text
1. Run Music Assistant.
2. Mount /home/pi/trusty/music.
3. Add local folder to Music Assistant filesystem provider.
4. Implement music adapter.
5. Implement Open-Meteo weather adapter.
6. Add Open-Meteo geocoding by location text.
7. Run SearXNG.
8. Implement search adapter.
9. Log external_payload for every internet call.
```

Test:

```text
Hey Trusty, play music from my offline folder.
Hey Trusty, will it rain in Dublin today?
Hey Trusty, will it rain today?
Hey Trusty, search Gemma 4 llama.cpp Raspberry Pi examples.
```

Expected result:

```text
Local music works offline.
Weather sends only location text or derived coordinates.
Weather without location asks for location.
Search sends only text query.
```

## Day 6: Trusty Eyes screen

Goal:

```text
Make Trusty feel alive on an attached screen.
```

Build:

```text
Trusty Eyes local HTML UI
WebSocket assistant state updates
Privacy display
```

Tasks:

```text
1. Add Trusty Eyes UI based on the Tamagotchi canvas style.
2. Remove manual buttons from production mode.
3. Add states for idle, listening, thinking, speaking, searching, offline, and blocked.
4. Add WebSocket updates from Trusty.
5. Show privacy indicators.
6. Open the screen at http://raspberrypi.local:8091.
```

Test:

```text
Hey Trusty, what is MQTT?
Hey Trusty, search Raspberry Pi AI HAT examples.
Hey Trusty, show privacy status.
```

Expected result:

```text
Eyes animate according to assistant state.
Privacy status is visible.
No external assets are loaded.
```

## Day 7: Packaging and demo

Goal:

```text
Make Trusty competition-ready.
```

Build:

```text
README
setup script
sample .env
demo commands
privacy ledger dashboard
short video flow
```

Tasks:

```text
1. Add setup script.
2. Add model download notes.
3. Add sample .env.
4. Add sample commands.
5. Add demo privacy logs.
6. Record 3-minute video.
```

Demo sequence:

```text
1. Hey Trusty, what is the capital of Jordan?
2. Hey Trusty, open YouTube on the TV.
3. Hey Trusty, play music from my offline folder.
4. Hey Trusty, will it rain in Dublin today?
5. Hey Trusty, will it rain today?
6. Hey Trusty, search Raspberry Pi AI HAT Gemma examples.
7. Hey Trusty, show the privacy ledger.
```

## Suggested repository structure

```text
trusty/
  README.md
  .env.example
  docker-compose.yml
  scripts/
    setup_pi.sh
    download_models.sh
    run_llama_server.sh
    run_trusty.sh

  config/
    tools.yaml
    privacy_policy.yaml
    offline_mode.yaml

  app/
    main.py
    model_client.py
    orchestrator.py
    privacy_validator.py
    privacy_ledger.py
    internet_policy.py
    schemas.py
    tool_registry.py

  prompts/
    planner_system.md
    final_answer_system.md

  voice/
    wakeword.py
    stt_whispercpp.py
    tts_kokoro.py
    audio_capture.py

  tools/
    local_answer.py
    home_assistant.py
    lg_tv.py
    music_assistant.py
    local_music.py
    open_meteo.py
    searxng.py

  ui/
    eyes/
      index.html
      styles.css
      eyes.js

  music/
    put_offline_music_here.md

  data/
    privacy_ledger.jsonl

  demo/
    sample_commands.md
    sample_privacy_logs.json
    video_script.md
```

## Setup script outline

```bash
#!/usr/bin/env bash
set -e

sudo apt update
sudo apt install -y git cmake build-essential python3-venv python3-pip ffmpeg portaudio19-dev espeak-ng

sudo mkdir -p /opt/trusty/models/gemma
sudo mkdir -p /opt/trusty/models/whisper
sudo mkdir -p /opt/trusty/models/kokoro
sudo mkdir -p /opt/trusty/models/wakeword
sudo mkdir -p /home/pi/trusty/music

cd /opt/trusty

if [ ! -d llama.cpp ]; then
  git clone https://github.com/ggml-org/llama.cpp
fi

cd /opt/trusty/llama.cpp
cmake -B build
cmake --build build --config Release -j 4

cd /opt/trusty

if [ ! -d whisper.cpp ]; then
  git clone https://github.com/ggml-org/whisper.cpp
fi

cd /opt/trusty/whisper.cpp
cmake -B build
cmake --build build --config Release -j 4

cd /opt/trusty

python3 -m venv /opt/trusty/venv
. /opt/trusty/venv/bin/activate

pip install --upgrade pip
pip install fastapi uvicorn pydantic python-dotenv requests websockets soundfile sounddevice kokoro-onnx openwakeword
```

## Model download script outline

```bash
#!/usr/bin/env bash
set -e

mkdir -p /opt/trusty/models/gemma
mkdir -p /opt/trusty/models/whisper
mkdir -p /opt/trusty/models/kokoro

# Gemma through llama.cpp can download from Hugging Face at runtime using -hf.
# For a fixed local file, download the GGUF manually or with huggingface-cli after accepting terms.

echo "Place Gemma 4 E2B IT GGUF here:"
echo "/opt/trusty/models/gemma/gemma-4-e2b-it.gguf"

# Whisper model
cd /opt/trusty/whisper.cpp
bash ./models/download-ggml-model.sh base.en
cp models/ggml-base.en.bin /opt/trusty/models/whisper/

# Kokoro files
cd /opt/trusty/models/kokoro
echo "Download these files from kokoro-onnx releases:"
echo "kokoro-v1.0.int8.onnx"
echo "voices-v1.0.bin"
```

## Run commands

Start Gemma:

```bash
/opt/trusty/llama.cpp/build/bin/llama-server \
  --model /opt/trusty/models/gemma/gemma-4-e2b-it.gguf \
  --host 127.0.0.1 \
  --port 8080
```

Start Trusty:

```bash
cd /home/pi/trusty
. /opt/trusty/venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8090
```

Open eyes UI:

```text
http://raspberrypi.local:8091
```

## Final MVP decision

```text
Name:
  Trusty

Hardware:
  Raspberry Pi 5 plus AI HAT

Model:
  Gemma 4 E2B IT GGUF

Runtime:
  llama.cpp

Wake word:
  openWakeWord

Speech to text:
  whisper.cpp base.en

Text to speech:
  Kokoro ONNX int8

Home control:
  Home Assistant Core

TV:
  LG webOS TV integration in Home Assistant

Music:
  Music Assistant plus /home/pi/trusty/music

Weather:
  Open-Meteo with user-provided location text

Search:
  SearXNG

UI:
  Trusty Eyes local HTML page inspired by Tamagotchi eyes

Skipped for this MVP:
  Camera and vision

Core promise:
  Trusty can use internet tools, but microphone audio never leaves the device.
```

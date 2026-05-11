# Trusty Runbook

Just the commands. For project background and architecture see
[`README.md`](README.md). For wake-word customization see
[`WAKE_WORD.md`](WAKE_WORD.md).

---

## Mac

### First-time setup

```bash
cd ~/trusty
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash scripts/download_models.sh
docker compose up -d
```

### Daily run (up to 4 terminals)

```bash
# A
bash scripts/run_llama_server.sh

# B
bash scripts/run_trusty.sh

# C  (optional, also served at http://localhost:8090/eyes/)
bash scripts/run_eyes.sh

# D  voice loop: wake word + STT + TTS on this Mac (see "Voice on Mac" below)
bash scripts/run_voice.sh
```

### Voice on Mac

> **Wake word for the MVP: "Hey Trusty"** (openWakeWord built-in, rebadged
> as "Hey Trusty" in the UI). Custom `hey_trusty.tflite` training is a
> separate step, see [`WAKE_WORD.md`](WAKE_WORD.md).

The Eyes UI does **not** open the microphone, it's a display. The voice
loop is a separate process (`voice/loop.py`) that captures the mic, listens
for the wake word, transcribes with whisper.cpp, sends to Trusty, and
speaks the reply with Kokoro.

#### 1. Grant mic permission (one time)

```
System Settings → Privacy & Security → Microphone → enable Terminal (or iTerm)
```

If macOS doesn't prompt automatically, open Terminal yourself and run any
mic test (the `peak amplitude` snippet below): that triggers the prompt.

#### 2. Confirm mic + speaker work

```bash
# Mic: should print >2000 if you speak; ~0 means blocked or muted
.venv/bin/python -c "
import sounddevice as sd, numpy as np
print('Speak for 3 s...')
rec = sd.rec(int(3*16000), samplerate=16000, channels=1, dtype='int16'); sd.wait()
print('peak amplitude:', int(np.abs(rec).max()))
"

# Speaker: should play a short Kokoro sample through the default output
set -a && . ./.env && set +a
.venv/bin/python -c "from voice.tts_kokoro import speak; speak('Trusty audio test.')"
```

The mic and speaker each follow whatever device is **selected as Input /
Output** in *System Settings → Sound*. Headphones, AirPods, USB headsets
all work, just make sure the right device is selected.

#### 3. Run the voice loop

```bash
bash scripts/run_voice.sh
```

You should see:

```
warming up Kokoro TTS...

  Trusty voice loop ready.
  Wake word: Hey Trusty  (model: hey_trusty, threshold: 0.50)
  Trusty API: http://127.0.0.1:8090
  Say 'hey trusty, ...' then your command. Ctrl-C to quit.
```

Then say:

```
Hey trusty, what is the capital of Jordan?
Hey trusty, will it rain in Dublin today?
Hey trusty, stop the vacuum.
```

Each turn prints (in colour):

```
  WAKE (score=0.88)
  listening...
  transcribing...
  YOU : What is the capital of Jordan?
  TRUSTY: The capital of Jordan is Amman.
```

The reply is also spoken through the speaker. Junk transcripts (whisper's
`[BLANK_AUDIO]`, single-word noise, etc.) are filtered out and Trusty asks
you to repeat instead of wasting a Gemma turn.

#### 4. Stop

Ctrl-C in that terminal.

#### Common issues


| Symptom                                                  | Fix                                                                                                                                                                             |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Wake word never fires                                    | Run the mic test above: peak should be >2000 when you talk. If 0, mic permission isn't granted to your terminal.                                                               |
| Wake word triggers on background noise                   | Raise the threshold in the Admin UI (Wake-word threshold). 0.6 is usually quieter than 0.5.                                                                                     |
| Reply is silent but log shows `TRUSTY:` line             | Check macOS Sound output device. Try the speaker test snippet above.                                                                                                            |
| Voice loop cuts you off mid-sentence                     | Already tuned: recordings now wait for 1.6 s of silence *after* speech is detected, with a 2 s minimum. If still cutting off, lower `silence_rms` in `voice/audio_capture.py`. |
| Transcript misheard ("That's" instead of "What's")       | The default model is now `small.en`, much better with accents than base.en. If still rough, try `medium.en` (~1.5 GB) by editing `WHISPER_MODEL_PATH` in `.env`.               |
| Trusty replies "Sorry, I had trouble understanding that" | Should be rare now: orchestrator falls through to a plain local answer if Gemma's planner fails. If you still see it, llama-server is probably down.                           |


### Test

```bash
bash scripts/smoke_test.sh
```

```bash
curl -sX POST http://127.0.0.1:8090/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"What is the capital of Jordan?"}' | jq
```

### Open in browser


| Page    | URL                                                          | What it does                                                                                          |
| ------- | ------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Eyes    | [http://localhost:8090/eyes/](http://localhost:8090/eyes/)   | Animated eye + status caption + privacy chip. Display only, does not listen.                         |
| Admin   | [http://localhost:8090/admin/](http://localhost:8090/admin/) | Mode toggle (online/offline), Pause Trusty switch, service health, quick test, ledger, live activity. |
| HA      | [http://localhost:8123](http://localhost:8123)               | Home Assistant, pair the Roborock vacuum here.                                                       |
| MA      | [http://localhost:8095](http://localhost:8095)               | Music Assistant.                                                                                      |
| SearXNG | [http://localhost:8088](http://localhost:8088)               | Local web search.                                                                                     |


The Eyes UI shows what Trusty is doing (idle → thinking → tv → speaking …)
but doesn't take input. Use the Admin UI's "Quick test" card or the voice
loop to actually send turns.

### Stop

```bash
pkill -f 'uvicorn app.main'
pkill -f 'llama-server'
docker compose down
```

---

## Home Assistant + Roborock vacuum setup (one-time)

Trusty's `home.vacuum` tool calls Home Assistant. Until you pair the
vacuum and generate a token you'll see "There was an authorization
error" on vacuum commands. Both work the same on Mac and Pi.

Full walk-through with screenshots: [`VACUUM_SETUP.md`](VACUUM_SETUP.md).
Quick version below.

### 1. Open Home Assistant

```
http://localhost:8123     # Mac
http://raspberrypi.local:8123   # Pi
```

First load takes ~1 min. Create the admin account when it onboards.

### 2. Add the Roborock integration

1. *Settings → Devices & Services → Add Integration*
2. Search **"Roborock"**
3. Enter your Roborock account email, click **Submit**
4. Paste the 6-digit verification code sent to your email
5. HA discovers each vacuum on the account and assigns an entity id,
   e.g. `vacuum.s6_pure`

### 3. Generate a Long-Lived Access Token

1. Click your username (bottom-left)
2. Scroll to **Long-Lived Access Tokens → Create Token**
3. Name it `trusty`, copy the value once (you only see it once)

### 4. Wire into `.env`

```env
HA_URL=http://localhost:8123
HA_TOKEN=<paste the token here>
VACUUM_ENTITY_ID=vacuum.s6_pure   # adjust to whatever HA gave you
```

Restart Trusty:

```bash
pkill -f 'uvicorn app.main'
bash scripts/run_trusty.sh
```

### 5. Verify

In the Admin UI's **Quick test**, send: *"Stop the vacuum."*
Expected: vacuum heads back to its dock, reply is "Sending the vacuum
back to its dock."

If you still get an authorization error: `HA_TOKEN` is wrong or expired.
Generate a fresh one and retry.

### 6. Music Assistant (optional)

Open [http://localhost:8095](http://localhost:8095). Add:

- A **Filesystem** music provider pointing to `/media/local-music` (the
compose mount of your `music/` folder)
- Optional: Spotify, Tidal, Apple Music providers (each requires login)

Trusty's `music` tool falls back to a direct local-folder player if MA
isn't reachable, so you can ignore this until you want richer playback.

---

## Pi

### Copy from Mac

```bash
rsync -avz \
  --exclude '.venv' --exclude '__pycache__' \
  --exclude 'external/*/build' --exclude 'external/.docker' \
  ~/Work/Projects/Trusty/ pi@raspberrypi.local:/home/pi/trusty/

rsync -avz ~/Work/Projects/Trusty/models/ pi@raspberrypi.local:/home/pi/trusty/models/
```

### First-time setup on the Pi

```bash
ssh pi@raspberrypi.local
cd /home/pi/trusty
bash scripts/setup_pi.sh
cp .env.example .env
nano .env                           # set TRUSTY_HOME=/home/pi/trusty, HA_TOKEN, VACUUM_ENTITY_ID
```

### Docker stack on the Pi

```bash
sudo apt install -y docker.io docker-compose-plugin
sudo usermod -aG docker pi
# log out, log in
# edit docker-compose.yml: switch HA + Music Assistant to network_mode: host
docker compose up -d
```

### Pair Roborock vacuum in HA

```
Open http://raspberrypi.local:8123
Settings → Devices & Services → Add Integration → "Roborock"
Enter your Roborock account email, then the 6-digit code sent to your inbox.
Profile → Long-Lived Access Tokens → Create → paste into .env as HA_TOKEN
```

Full walk-through: [`VACUUM_SETUP.md`](VACUUM_SETUP.md).

### Run as services

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trusty-llama trusty-app trusty-eyes
journalctl -u trusty-app -f
```

### Open

```
Eyes:   http://raspberrypi.local:8091/   (or :8090/eyes/)
Admin:  http://raspberrypi.local:8090/admin/
```

### Speak

```
"Hey Trusty, what is the capital of Jordan?"
"Hey Trusty, stop the vacuum."
"Hey Trusty, will it rain in Dublin today?"
```

### Stop / restart

```bash
sudo systemctl restart trusty-app
sudo systemctl stop trusty-llama trusty-app trusty-eyes
```


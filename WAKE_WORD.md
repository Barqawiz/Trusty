# Wake word: three paths to "Hey Trusty"

The MVP ships listening for **"Hey Jarvis"** under the hood, even though
the Eyes UI labels it "Hey Trusty". This document covers your three
options for changing that, in order from easiest to most accurate.

| Path | What you say | Effort | Accuracy |
|---|---|---|---|
| 1. Keep `hey_jarvis` | "Hey Jarvis, ..." | none | best (built-in, well-trained) |
| 2. Switch to a different built-in | one of the openWakeWord defaults | 5 min | best |
| 3. Train a real `hey_trusty.tflite` | "Hey Trusty, ..." | ~1 hour | good (depends on training data) |

---

## Quick reference: where the wake word is configured

The wake word is read from `.env`:

```env
# .env
WAKEWORD_NAME=hey_trusty                                    # display label only
WAKEWORD_MODEL_NAME=hey_jarvis                              # built-in model name
WAKEWORD_MODEL_PATH=${TRUSTY_HOME}/models/wakeword/hey_trusty.tflite
WAKEWORD_THRESHOLD=0.5
```

`voice/wakeword.py` resolves it:

1. If `WAKEWORD_MODEL_PATH` exists on disk → use that custom file.
2. Otherwise → use `WAKEWORD_MODEL_NAME` from openWakeWord's bundled set.

So you only need to edit `.env` to switch built-ins; only need to drop a
`.tflite` to use a custom model.

You can also adjust the **trigger sensitivity** live from the
[admin panel](http://localhost:8090/admin/) (the *Wake-word threshold*
field in the Runtime card) without touching `.env`.

---

## Path 1: Keep `hey_jarvis`

Nothing to do. This is the default and is the most reliable wake word
out of the box because openWakeWord trained it on a large synthetic
dataset.

You speak: **"Hey Jarvis, ..."** and the Eyes UI says "Hey Trusty"
above the eyes. That's good enough for the demo.

---

## Path 2: Switch to a different built-in (5 minutes)

openWakeWord ships these pretrained models. They're already on disk in
your `.venv` (downloaded by `download_models.sh`).

| Built-in name | What you'd say | Notes |
|---|---|---|
| `hey_jarvis` | "Hey Jarvis" | Default. Best accuracy. |
| `alexa` | "Alexa" | Confusable with Amazon Echos in the room. |
| `hey_mycroft` | "Hey Mycroft" | Cleaner than alexa, less common. |
| `hey_rhasspy` | "Hey Rhasspy" | Rare in the wild, fewer false triggers. |
| `weather` | "Weather" | Just the word, too easy to trigger by accident. Not recommended. |
| `timer` | "Timer" | Same: single-word, lots of false positives. |

### Steps

1. Edit `.env`:

   ```env
   WAKEWORD_MODEL_NAME=hey_mycroft
   ```

2. Make sure `WAKEWORD_MODEL_PATH` doesn't exist (so the custom-file
   branch isn't taken). The MVP path is
   `models/wakeword/hey_trusty.tflite`, which is **not** present unless
   you trained one.

3. Restart the voice loop:

   ```bash
   pkill -f 'voice.loop'
   bash scripts/run_voice.sh
   ```

You should see:

```
Voice loop ready.
Wake word: Hey Mycroft  (model: hey_mycroft, threshold: 0.50)
```

### Tune the threshold

In the [admin panel](http://localhost:8090/admin/), set
*Wake-word threshold*:

- **0.5** (default): balanced
- **0.6–0.7**: fewer false triggers, may need to speak clearly
- **0.4**: easier to trigger, more likely to fire on background talk

The voice loop reads the threshold once at startup. Changing it from
the admin panel updates the runtime config, but you need to restart the
voice loop to pick it up. (Restart-on-change isn't wired yet, flagged
in `progress.log` as a small future improvement.)

---

## Path 3: Train a real `hey_trusty.tflite` (~1 hour)

For genuine "Hey Trusty" the only honest path is a custom-trained model.
openWakeWord has an official Colab notebook that produces one with
synthetic data, no real recordings of you required (though your own
recordings make it dramatically better).

### What the notebook does

1. **Synthesise ~5,000 spoken "Hey Trusty" clips** from a TTS model
   across many voices, accents, speaking rates.
2. **Mix with ~50,000 negative clips** (random speech, background
   noise) so the false-accept rate stays low.
3. **Train a small TF model** on top of openWakeWord's frozen
   embedding network.
4. **Export `.tflite`** that you download.

Total wall time on Colab's free GPU: 30–60 minutes.

### Steps

1. Open the openWakeWord training notebook:
   <https://github.com/dscripka/openWakeWord#training-new-models>

   It links to a Colab notebook called
   `automatic_model_training.ipynb`. Click the "Open in Colab" badge.

2. In the notebook, set:

   ```python
   target_phrase = "Hey Trusty"
   model_name    = "hey_trusty"
   ```

   Leave training steps at the default (~10 K, the notebook will tell
   you).

3. **Run all cells.** Wait through the dataset synthesis (~10 min) and
   training (~20–40 min).

4. Download the resulting `hey_trusty.tflite` (and optionally
   `hey_trusty.onnx`).

5. Drop it into your project:

   ```bash
   mv ~/Downloads/hey_trusty.tflite \
      ~/trusty/models/wakeword/
   ```

   On the Pi:

   ```bash
   scp ~/Downloads/hey_trusty.tflite \
       pi@raspberrypi.local:/home/pi/trusty/models/wakeword/
   ```

6. Confirm `.env` already points at the right path:

   ```env
   WAKEWORD_MODEL_PATH=${TRUSTY_HOME}/models/wakeword/hey_trusty.tflite
   ```

   No edit needed if you used `.env.example`.

7. Restart the voice loop:

   ```bash
   pkill -f 'voice.loop'
   bash scripts/run_voice.sh
   ```

You should see:

```
Voice loop ready.
Wake word: Hey Trusty  (model: hey_trusty, threshold: 0.50)
```

### Improving accuracy with your own voice

The notebook supports a *user-specific verifier*: record yourself
saying "Hey Trusty" 50–100 times at different distances, paste those
WAVs into the notebook, and it fine-tunes the model on your voice.
False rejects drop to near zero. False accepts also drop because the
model learns to ignore TTS-shaped utterances and bias toward your
formant patterns.

Concretely: in the notebook, find the **"User-specific positive
examples"** cell, upload a folder of WAVs named anything (e.g.
`me_001.wav` ... `me_100.wav`), and re-run from there.

---

## Common issues

| Symptom | Fix |
|---|---|
| Voice loop logs `Loaded built-in wake word model: hey_jarvis` after you set a custom path | The file at `WAKEWORD_MODEL_PATH` doesn't exist or isn't a `.tflite`. `ls models/wakeword/`. |
| Wake fires on TV / radio voices in the room | Raise the threshold to 0.6 in the admin panel and restart the voice loop. |
| Wake never fires when you speak normally | Check mic levels. The mic-test snippet in [`RUNBOOK.md`](RUNBOOK.md) prints peak amplitude: should be >2000 when you talk. If 0, mic permission isn't granted to your Terminal app on macOS. |
| Custom model fires on totally unrelated phrases | Training data was too narrow. Re-run the Colab with more synthetic voices, or add user-specific verifier samples. |
| Two different wake words at once? | Not supported by the voice loop. You'd need to load multiple openWakeWord models and check all scores, small change in `voice/wakeword.py` if you want it. |

---

## Privacy

The wake-word model runs entirely on the Mac / Pi. The audio buffer it
analyses never touches the internet. Even the wake-word *score* (a
single float per frame) stays in process memory.

This is enforced by `config/privacy_policy.yaml`'s
`globally_forbidden: [audio, wake_word_audio, ...]` floor: the privacy
validator would reject any tool call whose payload references audio.

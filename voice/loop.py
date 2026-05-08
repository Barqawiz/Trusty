"""End-to-end voice loop on the host running this process.

Pipeline:
    1. openWakeWord listens on the default mic.
    2. On wake, record up to ~12 s of speech (or until ~1.6 s of silence
       AFTER speech is detected).
    3. whisper.cpp transcribes the recording.
    4. POST the transcript to Trusty's /chat endpoint.
    5. Kokoro ONNX speaks the final response back.

Run:
    bash scripts/run_voice.sh
or directly:
    .venv/bin/python -m voice.loop
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
import warnings
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Kokoro's phonemizer prints noisy "words count mismatch" warnings on most
# replies. They're cosmetic — silence them.
warnings.filterwarnings("ignore", message=".*words count mismatch.*")
logging.getLogger("phonemizer").setLevel(logging.ERROR)

from .audio_capture import open_input_stream, record_until_silence, SAMPLE_RATE
from .stt import have_stt, transcribe_pcm16
from .tts_kokoro import speak

log = logging.getLogger("voice.loop")

# Minimum gap between two wake-word triggers — long enough that the tail
# of the user's wake phrase doesn't re-trigger on its own.
WAKE_COOLDOWN_S = 2.0

# Whisper transcribes background noise / typing / silence as bracketed or
# parenthesised annotations like [BLANK_AUDIO], (typing), [music], *click*.
# Treat any transcript whose ENTIRE content is one of those wrappers as junk.
_JUNK_WRAPPED = re.compile(
    r"^\s*[\[\(\*][^\]\)\*]{1,40}[\]\)\*]\s*\.?\s*$"
)
# Some Whisper builds also emit bare keywords without brackets.
_JUNK_BARE = re.compile(
    r"^\s*("
    r"BLANK_AUDIO|silence|noise|inaudible|unintelligible|music|laughter|applause"
    r")\.?\s*$",
    re.IGNORECASE,
)
_HAS_LETTER = re.compile(r"[a-zA-Z]")
_MIN_TRANSCRIPT_CHARS = 2  # "Hi" / "OK" / "Yes" — let the orchestrator decide

# WAKEWORD_MODE=OFF only — gate that decides whether to POST a chunk to /chat.
# Permissive on common Whisper/Moonshine mishears of "trusty" (rusty/trustly).
_WAKE_TRIGGER_RE = re.compile(
    r"\b("
    r"trust\w*|rusty|trustly|"
    r"wake\s*(?:up|me)?|"
    r"good\s+(?:morning|afternoon|evening)|"
    r"hey|hi"
    r")\b",
    re.IGNORECASE,
)

# ANSI colour codes — disabled if stdout isn't a TTY.
_TTY = sys.stdout.isatty()
def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s
def _green(s):  return _c("1;32", s)
def _cyan(s):   return _c("1;36", s)
def _yellow(s): return _c("1;33", s)
def _dim(s):    return _c("2", s)


def _drain_queue(q) -> int:
    """Discard all frames currently buffered in the input queue.

    Called between turns so the wake-word model only sees fresh audio,
    not the silence + TTS bleed-back accumulated while Trusty was
    thinking and speaking. Returns the number of frames dropped (useful
    for logging if needed)."""
    import queue as _q

    dropped = 0
    while True:
        try:
            q.get_nowait()
            dropped += 1
        except _q.Empty:
            break
    if dropped:
        log.debug("drained %d stale frames", dropped)
    return dropped


def _is_junk(text: str) -> bool:
    """True if STT output is empty / a Whisper annotation / pure punctuation."""
    t = (text or "").strip()
    if not t:
        return True
    if _JUNK_WRAPPED.match(t):
        return True
    if _JUNK_BARE.match(t):
        return True
    if len(t) < _MIN_TRANSCRIPT_CHARS:
        return True
    if not _HAS_LETTER.search(t):
        return True
    return False


def _resolve_settings() -> dict:
    here = Path(__file__).resolve().parents[1]
    load_dotenv(here / ".env", override=False)
    base_threshold = float(os.environ.get("WAKEWORD_THRESHOLD", "0.5"))
    # WAKEWORD_MODE=OFF switches to transcript-scan; anything else fails safe to ON.
    wakeword_mode = os.environ.get("WAKEWORD_MODE", "ON").strip().upper()
    return {
        "trusty_url": os.environ.get(
            "TRUSTY_URL",
            f"http://127.0.0.1:{os.environ.get('TRUSTY_PORT', '8090')}",
        ),
        "wakeword_name": os.environ.get("WAKEWORD_NAME", "hey_trusty"),
        "wakeword_model": os.environ.get("WAKEWORD_MODEL_NAME", "hey_jarvis"),
        "wakeword_threshold": base_threshold,
        # Built-in models like `alexa` are looser than a hand-trained custom
        # model and will fire on TV / podcasts. Allow a stricter threshold
        # for them, falling back to the same value.
        "wakeword_threshold_builtin": float(os.environ.get(
            "WAKEWORD_THRESHOLD_BUILTIN", str(base_threshold)
        )),
        "wakeword_custom": os.environ.get("WAKEWORD_MODEL_PATH", ""),
        "wakeword_enabled": wakeword_mode != "OFF",
        "whisper_bin": os.environ.get("WHISPER_BIN", ""),
        "whisper_model": os.environ.get("WHISPER_MODEL_PATH", ""),
    }


def _load_wakeword(cfg: dict[str, str]):
    """Load custom + built-in wake-word models. Either or both may be
    enabled depending on env: a custom file path activates the custom
    model, the built-in model name activates that one. With both set we
    listen for both phrases simultaneously."""
    from openwakeword.model import Model
    refs: list[str] = []
    custom = cfg["wakeword_custom"]
    if custom and Path(custom).is_file():
        refs.append(custom)
        log.info("wake: custom model -> %s", custom)
    elif custom:
        log.warning("wake: WAKEWORD_MODEL_PATH=%s not found; skipping custom", custom)
    builtin = cfg["wakeword_model"]
    if builtin:
        refs.append(builtin)
        log.info("wake: built-in model -> %s", builtin)
    if not refs:
        raise RuntimeError(
            "No wake-word model configured. Set WAKEWORD_MODEL_PATH (custom) "
            "or WAKEWORD_MODEL_NAME (built-in) in .env."
        )
    # Pick the inference framework. Honour an explicit env override; otherwise
    # let the file extension decide (.onnx -> onnx, .tflite -> tflite). Built-in
    # names with no extension fall back to openwakeword's tflite default — set
    # WAKEWORD_INFERENCE_FRAMEWORK=onnx on hosts without tflite-runtime
    # (e.g. Pi 5 / Debian 13 / Python 3.13).
    forced = os.environ.get("WAKEWORD_INFERENCE_FRAMEWORK", "").strip().lower()
    kwargs = {}
    if forced:
        kwargs["inference_framework"] = forced
    elif any(r.endswith(".onnx") for r in refs) and not any(
        r.endswith(".tflite") for r in refs
    ):
        kwargs["inference_framework"] = "onnx"
    return Model(wakeword_models=refs, **kwargs)


def _preroll_phrase(text: str) -> str | None:
    """Return a short acknowledgement to speak BEFORE the orchestrator runs.

    The music tool's `play_search` takes 4–8 s (planner + MA search +
    Spotify resolve + finalize). That's a long silence between the user's
    command and the first audible feedback. A 1-second placeholder makes
    the assistant feel responsive.

    Heuristics, in priority order:
      1. Birthday songs — special-cased per user request.
      2. Generic music play ("play X", "start X", "put on X") — echo the
         subject so the user knows we heard them right.
      3. Mood / vibe words — generic acknowledgement.
      4. Transport commands (stop / pause / etc.) — None (already fast).
    """
    t = text.strip().lower()
    if not t:
        return None
    # Transport is fast — no pre-roll. Listed first so they short-circuit.
    if any(t.startswith(k) or t == k for k in (
        "stop", "pause", "resume", "skip", "next", "shut", "carry on",
        "hold on", "kill", "play again", "keep playing",
    )):
        return None
    # Birthday — explicit, friendly.
    if "happy birthday" in t or "birthday song" in t:
        return "Happy birthday! Looking for the song now."
    # Sleep/wake commands — short ack, the orchestrator will speak the real reply.
    if any(p in t for p in ("go to sleep", "sleep trusty", "stop listening")):
        return None  # don't interfere with the explicit "Going to sleep" reply
    if any(p in t for p in ("wake up", "good morning", "i'm back")):
        return None
    # "play X" / "start X" / "put on X" — quote the subject back briefly.
    for prefix in ("play ", "start ", "put on ", "i want to hear "):
        if t.startswith(prefix):
            subject = text.strip()[len(prefix):].strip(" ?.!,")
            if not subject or len(subject) > 60:
                return "Looking for that, just a sec."
            return f"Looking for {subject}, just a sec."
    # Mood-y phrases.
    if any(w in t for w in ("music", "song", "playlist")):
        return "On it, give me a sec."
    return None


_FAREWELL = "Call me when you want me back."
# Suppress speaking the orchestrator's "I'm asleep" reply while the
# loop is in its own paused mode — it would talk over music.
_ASLEEP_PREFIX = "I'm asleep"


def _send_sleep(trusty_url: str) -> None:
    """Side-channel: tell the orchestrator to enter paused state.
    Reply is intentionally discarded — caller has already spoken a
    farewell, we don't want a second TTS line on top."""
    try:
        with httpx.Client(timeout=10) as c:
            c.post(f"{trusty_url}/chat", json={"text": "trusty go to sleep"})
    except Exception as e:
        log.debug("sleep dispatch failed (non-fatal): %s", e)


def _send_speech_event(trusty_url: str, event: str) -> None:
    """Tell the FastAPI runner that VAD just detected speech start / end so
    the Mac wrapper can show a 'Listening…' bubble during STT processing.
    Fire-and-forget on a worker thread so the audio-capture loop doesn't
    stall waiting for HTTP."""
    import threading

    def _post():
        try:
            with httpx.Client(timeout=2.0) as c:
                c.post(f"{trusty_url}/admin/speech_event", json={"event": event})
        except Exception as e:
            log.debug("speech_event dispatch failed (non-fatal): %s", e)
    threading.Thread(target=_post, daemon=True).start()


def _is_music_play(plan: dict) -> bool:
    return (plan.get("tool") == "music"
            and str(plan.get("action", "")).startswith("play_"))


def _is_wake_response(plan: dict) -> bool:
    """Did the orchestrator just unpause us?"""
    return plan.get("tool") == "system.power" and plan.get("action") == "wake"


def _is_sleep_response(plan: dict) -> bool:
    """Did the orchestrator just pause us via 'trusty go to sleep'?"""
    return plan.get("tool") == "system.power" and plan.get("action") == "sleep"


def _handle_turn(
    cfg: dict,
    trusty_url: str,
    q=None,
    max_seconds: float = 12.0,
    is_follow_up: bool = False,
    local_paused: bool = False,
    require_trigger: bool = False,
) -> tuple[bool, dict]:
    """Capture utterance, transcribe, send, speak the reply.

    Returns (processed, plan_dict). processed is True when a real turn
    was processed (speech heard, /chat called); the caller uses this
    to decide whether to continue conversation mode. plan_dict is the
    planner output from /chat ({} on no chat / on error).

    `local_paused` suppresses speaking when the orchestrator's reply is
    the "I'm asleep" sentinel — that would interrupt music currently
    playing. Wake-up confirmations (system.power/wake) still speak.

    `q` is the live wake-word stream queue. We reuse it so we don't try to
    open a second InputStream against the same USB mic (ALSA refuses with
    'Device unavailable').

    `require_trigger` (WAKEWORD_MODE=OFF) discards the chunk if the transcript
    has no wake trigger word; returns (False, {}) so the caller keeps listening.
    """
    if is_follow_up:
        print(_yellow("  listening (continue talking, or stay quiet to end)..."),
              flush=True)
        # Initial-timeout guard so a silent follow-up exits in ~3 s rather
        # than waiting the full max_seconds.
        initial_timeout: float | None = 3.0
        # Follow-ups can be short — single-word answers should land.
        min_secs = 0.5
    else:
        print(_yellow("  listening...  (speak now)"), flush=True)
        initial_timeout = None
        min_secs = 2.0
    # speech_rms / silence_rms left at defaults so audio_capture's mic
    # profile lookup picks the right thresholds (e.g. Razer needs much
    # higher thresholds than the cheap C-Media baseline).
    audio = record_until_silence(
        max_seconds=max_seconds,
        silence_seconds=1.6,
        min_seconds=min_secs,
        initial_speech_timeout=initial_timeout,
        q=q,
        on_speech_start=lambda: _send_speech_event(trusty_url, "speech_start"),
    )
    if audio.size == 0:
        if not is_follow_up:
            print(_dim("  no audio — back to wake-word listening."), flush=True)
        return (False, {})
    print(_dim("  transcribing..."), flush=True)
    text = transcribe_pcm16(
        audio, SAMPLE_RATE,
        whisper_bin=cfg["whisper_bin"],
        model_path=cfg["whisper_model"],
    ).strip()
    if _is_junk(text):
        # Silent re-arm. Don't speak anything — user knows from the terminal.
        print(_dim(f"  (no speech detected — STT={text!r})"), flush=True)
        return (False, {})
    if require_trigger and not _WAKE_TRIGGER_RE.search(text):
        # Heard speech but no wake trigger — drop silently.
        print(_dim(f"  (no trigger — discarded: {text!r})"), flush=True)
        return (False, {})
    print(f"  {_green('YOU :')} {text}", flush=True)
    # Speak a quick acknowledgement for slow tool calls (music search etc.)
    # so the user isn't staring at silence for 5–8 s while the orchestrator
    # works. This is best-effort — TTS failure shouldn't block the turn.
    # Skip preroll while paused so we don't interrupt music.
    if not local_paused:
        preroll = _preroll_phrase(text)
        if preroll:
            print(_dim(f"  preroll: {preroll!r}"), flush=True)
            try:
                speak(preroll)
            except Exception as e:
                log.debug("preroll TTS failed (non-fatal): %s", e)
    try:
        # Pi inference is slow (100-200 s for free-form questions). Use the
        # same env var the FastAPI side uses, default 240 s.
        chat_timeout = float(os.environ.get("VOICE_CHAT_TIMEOUT_S",
                                            os.environ.get("LLAMA_REQUEST_TIMEOUT_S", "240")))
        with httpx.Client(timeout=chat_timeout) as c:
            r = c.post(f"{trusty_url}/chat", json={"text": text})
            r.raise_for_status()
            payload = r.json()
            reply = payload.get("final_response", "")
            plan = payload.get("plan", {}) or {}
    except Exception as e:
        log.error("Trusty /chat failed: %s", e)
        if not local_paused:
            speak("I couldn't reach Trusty right now.")
        # Still counts as a turn — we don't want to immediately re-listen
        # after a network blip; let the user re-say their wake word.
        return (True, {})
    if not reply.strip():
        log.warning("empty reply from Trusty")
        return (True, plan)
    print(f"  {_cyan('TRUSTY:')} {reply}\n", flush=True)
    # Skip "I'm asleep" replies while paused so they don't interrupt music.
    # The orchestrator's wake bypass still works — wake-up confirmations
    # have a different reply and DO get spoken so the user hears the cue.
    suppress = local_paused and reply.strip().lower().startswith(_ASLEEP_PREFIX.lower())
    if suppress:
        print(_dim("  (suppressed — paused; wake to resume)"), flush=True)
    else:
        speak(reply)
    return (True, plan)


def _run_transcript_scan(cfg: dict, trusty_url: str) -> int:
    """Main loop for WAKEWORD_MODE=OFF: continuous STT + transcript-scan.
    Dispatches a chunk only when it contains a trigger word; mirrors the
    wake-word path's follow-up window, sleep auto-pause, and music auto-pause."""
    FOLLOWUP_SILENT_LIMIT = 2
    local_paused = False
    with open_input_stream() as q:
        last_frame_at = time.time()
        while True:
            # Heartbeat — bail out if the audio callback stalls (USB hiccup).
            try:
                _frame = q.get(timeout=1.0)
                last_frame_at = time.time()
            except Exception:
                if time.time() - last_frame_at > 10.0:
                    log.error(
                        "audio callback stalled (no frames for %.0fs); "
                        "exiting voice loop so it can restart",
                        time.time() - last_frame_at,
                    )
                    return 3
                continue
            _drain_queue(q)  # fresh audio, no TTS bleed-back
            try:
                processed, plan = _handle_turn(
                    cfg, trusty_url, q=q,
                    max_seconds=10.0,
                    require_trigger=True,
                    local_paused=local_paused,
                )
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("turn failed")
                processed, plan = (False, {})

            if not processed:
                # No speech / junk / no trigger — keep listening.
                last_frame_at = time.time()
                continue

            # Orchestrator unpaused us — clear the local mirror.
            if _is_wake_response(plan):
                local_paused = False

            triggered_auto_sleep = False
            if _is_music_play(plan) and not local_paused:
                try: speak(_FAREWELL)
                except Exception: pass
                _send_sleep(trusty_url)
                local_paused = True
                triggered_auto_sleep = True
            if _is_sleep_response(plan):
                local_paused = True
                triggered_auto_sleep = True

            # Follow-up window — trigger word NOT required for the next
            # FOLLOWUP_SILENT_LIMIT turns. Mirrors the wake-word path.
            silent_in_followup = 0
            while not triggered_auto_sleep and (
                processed or silent_in_followup < FOLLOWUP_SILENT_LIMIT
            ):
                _drain_queue(q)
                try:
                    processed, plan = _handle_turn(
                        cfg, trusty_url, q=q,
                        max_seconds=10.0,
                        is_follow_up=True,
                        local_paused=local_paused,
                        require_trigger=False,  # follow-up: free-form
                    )
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log.exception("follow-up turn failed")
                    processed, plan = (False, {})
                if processed:
                    silent_in_followup = 0
                    if _is_wake_response(plan):
                        local_paused = False
                    if _is_music_play(plan) and not local_paused:
                        try: speak(_FAREWELL)
                        except Exception: pass
                        _send_sleep(trusty_url)
                        local_paused = True
                        triggered_auto_sleep = True
                        break
                    if _is_sleep_response(plan):
                        local_paused = True
                        triggered_auto_sleep = True
                        break
                else:
                    silent_in_followup += 1

            # Back to scanning for the trigger word.
            _drain_queue(q)
            last_frame_at = time.time()
            print(_dim(
                "  back to transcript-scan listening "
                "(say 'trusty <command>' to talk again)"
            ), flush=True)


def run() -> int:
    """Real entry point — restructured so we don't have unreachable code."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    cfg = _resolve_settings()

    backend = os.environ.get("STT_BACKEND", "whisper").lower()
    log.info("STT backend: %s", backend)
    if not have_stt(
        whisper_bin=cfg["whisper_bin"],
        model_path=cfg["whisper_model"],
    ):
        log.error(
            "STT backend %r is not ready (whisper_bin=%s whisper_model=%s)",
            backend, cfg["whisper_bin"], cfg["whisper_model"],
        )
        return 2

    # Silero VAD gates recording (replaces the old RMS thresholds). Fail
    # fast if the model file is missing — silent fallback would mask the
    # exact problem we're moving away from.
    from .vad_silero import have_vad
    if not have_vad():
        log.error(
            "Silero VAD model missing. Run `bash scripts/download_models.sh` "
            "to fetch models/vad/silero_vad.onnx."
        )
        return 2

    # WAKEWORD_MODE=OFF skips openWakeWord; transcript-scan handles wake.
    if cfg["wakeword_enabled"]:
        model = _load_wakeword(cfg)
        # Per-model threshold. The custom model (whose key matches a stem in
        # WAKEWORD_MODEL_PATH) gets the base WAKEWORD_THRESHOLD; the built-in
        # gets WAKEWORD_THRESHOLD_BUILTIN (defaulting to base if not set).
        custom_stem = Path(cfg["wakeword_custom"]).stem if cfg["wakeword_custom"] else ""
        builtin_name = cfg["wakeword_model"]
        base_th = cfg["wakeword_threshold"]
        builtin_th = cfg["wakeword_threshold_builtin"]
        thresholds: dict[str, float] = {}
        for key in model.models.keys():
            if key == custom_stem or key == cfg["wakeword_custom"]:
                thresholds[key] = base_th
            elif key == builtin_name:
                thresholds[key] = builtin_th
            else:
                thresholds[key] = base_th
    else:
        model = None
        thresholds = {}
    trusty_url = cfg["trusty_url"].rstrip("/")
    # Pre-load Kokoro so the first reply isn't 4 s late while it warms up.
    print(_dim("  warming up Kokoro TTS..."), flush=True)
    try:
        from .tts_kokoro import _instance as _preload_kokoro
        _preload_kokoro()
    except Exception as e:
        log.warning("Kokoro preload failed: %s", e)

    # Pre-load the STT backend on the same principle. Otherwise the very
    # first wake-word turn pays the model-load cost mid-flight, which
    # delays the transcript by 1-3 s and can make the user think the
    # loop has frozen.
    print(_dim("  warming up STT backend..."), flush=True)
    try:
        backend = os.environ.get("STT_BACKEND", "whisper").lower()
        if backend == "moonshine":
            from .stt_moonshine import _model as _preload_moonshine
            _preload_moonshine()
        # whisper.cpp is invoked as a subprocess per turn; nothing to
        # preload there — the binary is already on disk.
    except Exception as e:
        log.warning("STT preload failed: %s", e)

    # Warm Silero VAD too; the ONNX session is shared across recorders.
    print(_dim("  warming up VAD..."), flush=True)
    try:
        from .vad_silero import _session as _preload_vad
        _preload_vad()
    except Exception as e:
        log.warning("VAD preload failed: %s", e)

    # Banner label: configured display name, or first model key as fallback.
    if model is not None:
        display_name = cfg.get("wakeword_name") or next(iter(model.models.keys()))
    else:
        display_name = cfg.get("wakeword_name") or "hey trusty"
    wake_phrase = display_name.replace("_", " ")
    # Reset orchestrator's paused state on startup ONLY when openWakeWord is
    # active (Pi). In transcript-scan mode (WAKEWORD_MODE=OFF) the runtime
    # always starts unpaused, the POST is unnecessary, and it would trigger
    # the wake-up TTS + a transcript bubble on launch — bad UX.
    if cfg["wakeword_enabled"]:
        try:
            with httpx.Client(timeout=8.0) as c:
                c.post(f"{trusty_url}/chat", json={"text": "trusty wake up"})
        except Exception as e:
            log.debug("startup wake-up dispatch failed (non-fatal): %s", e)

    print()
    print(_green("  Trusty voice loop ready."))
    if cfg["wakeword_enabled"]:
        keys_summary = ", ".join(
            f"{k}@{thresholds[k]:.2f}" for k in model.models.keys()
        )
        print(f"  Wake word: {_yellow('Hey ' + wake_phrase.split(' ', 1)[-1].title())}  "
              f"(active: {keys_summary})")
    else:
        print(f"  Wake mode: {_yellow('transcript-scan (WAKEWORD_MODE=OFF)')}")
    print(f"  Trusty API: {trusty_url}")
    if cfg["wakeword_enabled"]:
        print(_dim(f"  Say '{wake_phrase}, ...' then your command. Ctrl-C to quit.\n"))
    else:
        print(_dim(
            f"  Continuous listening — say 'trusty <command>' or 'wake up <command>'. "
            f"Ctrl-C to quit.\n"
        ))

    if not cfg["wakeword_enabled"]:
        return _run_transcript_scan(cfg, trusty_url)

    with open_input_stream() as q:
        last_trigger = 0.0
        last_frame_at = time.time()
        # Local paused state — set after the music auto-sleep so the loop
        # can suppress the orchestrator's "I'm asleep" reply (which would
        # otherwise talk over the music). Cleared automatically the next
        # time the wake word fires: hearing the wake word IS the wake-up
        # signal; we don't require a separate "wake up" verb.
        local_paused = False
        # Number of consecutive silent slots tolerated inside follow-up
        # mode before we drop back to wake-word listening. Set to 2 so
        # one quick pause doesn't end the conversation.
        FOLLOWUP_SILENT_LIMIT = 2
        while True:
            try:
                frame = q.get(timeout=1.0)
                last_frame_at = time.time()
            except Exception:
                # No frames for a full second. If silence stretches past
                # ~10 s the audio callback may have stalled (USB hiccup) —
                # we'd never recover without a stream restart. Bail out
                # of the `with` block; the outer process supervisor (tmux
                # / systemd) restarts the loop cleanly.
                if time.time() - last_frame_at > 10.0:
                    log.error(
                        "audio callback stalled (no frames for %.0fs); "
                        "exiting voice loop so it can restart",
                        time.time() - last_frame_at,
                    )
                    return 3
                continue
            preds = model.predict(frame)
            now = time.time()
            # Trigger on the first model whose score crosses its threshold.
            triggered_key = None
            triggered_score = 0.0
            for key, th in thresholds.items():
                s = preds.get(key, 0.0)
                if s >= th and s > triggered_score:
                    triggered_key = key
                    triggered_score = s
            if triggered_key is None or (now - last_trigger) < WAKE_COOLDOWN_S:
                continue
            last_trigger = now
            print(_yellow(
                f"  WAKE [{triggered_key}] (score={triggered_score:.2f})"
            ), flush=True)

            # Wake while paused = the wake itself is the wake-up signal.
            # Single mechanism: there is no separate "wake up" verb the
            # user has to remember. Tell the orchestrator to unpause
            # synchronously so the user's command (which we're about to
            # capture) gets processed against an active runtime.
            if local_paused:
                print(_dim("  (wake while paused — resuming)"), flush=True)
                try:
                    with httpx.Client(timeout=8.0) as c:
                        c.post(f"{trusty_url}/chat",
                               json={"text": "trusty wake up"})
                except Exception as e:
                    log.debug("resume dispatch failed (non-fatal): %s", e)
                local_paused = False
                # Drop any TTS bleed-back / music frames captured before
                # the wake fired so the recorder starts clean.
                _drain_queue(q)

            # Conversation mode. After a successful wake, keep listening
            # for follow-up commands without requiring a new wake word —
            # natural back-and-forth. Each turn drains TTS bleed before
            # the next listen. The follow-up tolerates up to
            # FOLLOWUP_SILENT_LIMIT consecutive silent windows; after
            # that we drop back to wake-word listening.
            try:
                processed, plan = _handle_turn(
                    cfg, trusty_url, q=q, local_paused=local_paused,
                )
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("turn failed")
                processed, plan = (False, {})

            triggered_auto_sleep = False

            # If the wake-triggered turn started music, auto-sleep before
            # entering follow-up so we don't loop on our own output.
            if processed and _is_music_play(plan) and not local_paused:
                try: speak(_FAREWELL)
                except Exception: pass
                _send_sleep(trusty_url)
                local_paused = True
                triggered_auto_sleep = True

            # Explicit sleep ("trusty go to sleep") — mirror the music path:
            # set local_paused so the next wake word triggers auto-resume,
            # and skip the follow-up loop so we don't keep hearing "I'm asleep".
            if processed and _is_sleep_response(plan):
                local_paused = True
                triggered_auto_sleep = True

            silent_in_followup = 0
            # Enter the follow-up loop unless the very first turn was
            # already silent or we just slept. We keep going while either
            # the last turn was processed OR we've still got silent-slot
            # budget left.
            while not triggered_auto_sleep and (
                processed or silent_in_followup < FOLLOWUP_SILENT_LIMIT
            ):
                _drain_queue(q)
                try:
                    processed, plan = _handle_turn(
                        cfg, trusty_url, q=q,
                        max_seconds=10.0, is_follow_up=True,
                        local_paused=local_paused,
                    )
                except KeyboardInterrupt:
                    raise
                except Exception:
                    log.exception("follow-up turn failed")
                    processed, plan = (False, {})
                if processed:
                    silent_in_followup = 0
                    if _is_music_play(plan) and not local_paused:
                        try: speak(_FAREWELL)
                        except Exception: pass
                        _send_sleep(trusty_url)
                        local_paused = True
                        triggered_auto_sleep = True
                        break
                    if _is_sleep_response(plan):
                        local_paused = True
                        triggered_auto_sleep = True
                        break
                else:
                    silent_in_followup += 1

            # Conversation ended — return to wake-word listening.
            _drain_queue(q)
            last_frame_at = time.time()
            print(_dim(
                f"  back to wake-word listening "
                f"(say '{wake_phrase}' to talk again)"
            ), flush=True)


if __name__ == "__main__":
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        print("\nbye.")
        sys.exit(0)

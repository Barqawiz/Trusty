"""Wake-word detection. Uses openWakeWord's pretrained `hey_jarvis` for the MVP
(rebadged as "Hey Trusty" in the UI). Threshold and model name come from .env.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from pathlib import Path

import numpy as np

from openwakeword.model import Model

from .audio_capture import FRAME_SAMPLES, open_input_stream

log = logging.getLogger(__name__)


class WakeWordDetector:
    def __init__(
        self,
        model_name: str = "hey_jarvis",
        threshold: float = 0.5,
        custom_model_path: str | None = None,
    ) -> None:
        self.threshold = threshold
        if custom_model_path and Path(custom_model_path).is_file():
            self.model = Model(wakeword_models=[custom_model_path])
            log.info("Loaded custom wake word model: %s", custom_model_path)
        else:
            self.model = Model(wakeword_models=[model_name])
            log.info("Loaded built-in wake word model: %s", model_name)
        self.model_key = next(iter(self.model.models.keys()))

    def detect_blocking(
        self,
        cooldown_seconds: float = 1.5,
    ) -> None:
        """Block until wake word is detected, then return."""
        last_trigger = 0.0
        with open_input_stream() as q:
            while True:
                frame = q.get()
                preds = self.model.predict(frame)
                score = preds.get(self.model_key, 0.0)
                if score >= self.threshold:
                    now = time.time()
                    if now - last_trigger > cooldown_seconds:
                        log.info("wake word triggered (score=%.2f)", score)
                        last_trigger = now
                        return

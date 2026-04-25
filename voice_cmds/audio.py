"""Microphone capture via sounddevice. 16 kHz mono float32, pushes chunks to a callback."""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import sounddevice as sd

logger = logging.getLogger("voice_cmds.audio")

SAMPLE_RATE = 16000
CHUNK_MS = 100
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000


class MicrophoneStream:
    def __init__(self) -> None:
        self._stream: sd.InputStream | None = None
        self._on_chunk: Callable[[np.ndarray], None] | None = None

    def start(self, on_chunk: Callable[[np.ndarray], None]) -> None:
        if self._stream is not None:
            return
        self._on_chunk = on_chunk
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=CHUNK_SAMPLES,
            callback=self._callback,
        )
        self._stream.start()
        logger.debug("Microphone stream started")

    def stop(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
            self._on_chunk = None
        logger.debug("Microphone stream stopped")

    def _callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning("Audio status: %s", status)
        if self._on_chunk is not None:
            # Mono -> 1-D array of float32
            self._on_chunk(indata[:, 0].copy())

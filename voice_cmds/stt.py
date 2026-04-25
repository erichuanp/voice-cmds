"""Streaming STT using sherpa-onnx zipformer-bilingual-zh-en.

Auto-downloads the model bundle on first run into ./models/. The pip wheel of
sherpa-onnx is CPU-only (compiled without -DSHERPA_ONNX_ENABLE_GPU=ON), so we
go straight to CPU and skip the noisy CUDA fallback log.
"""
from __future__ import annotations

import logging
import tarfile
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

import numpy as np
import requests

from .audio import SAMPLE_RATE
from .config import MODELS_DIR

logger = logging.getLogger("voice_cmds.stt")

MODEL_NAME = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "asr-models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20.tar.bz2"
)

# Optional callback signatures:
#   status_cb(text: str)
#   progress_cb(current: int, total: int)  # total=0 → indeterminate
StatusCB = Optional[Callable[[str], None]]
ProgressCB = Optional[Callable[[int, int], None]]


def _download(url: str, dest: Path, status_cb: StatusCB = None, progress_cb: ProgressCB = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s -> %s", url, dest)
    if status_cb:
        status_cb("正在下载语音识别模型 (~500MB)…")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        if progress_cb:
            progress_cb(0, total)
        with dest.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if progress_cb:
                    progress_cb(downloaded, total)


def ensure_model(status_cb: StatusCB = None, progress_cb: ProgressCB = None) -> Path:
    """Return the directory containing the model files; download if missing."""
    model_dir = MODELS_DIR / MODEL_NAME
    encoder = model_dir / "encoder-epoch-99-avg-1.onnx"
    if encoder.exists():
        return model_dir

    archive = MODELS_DIR / f"{MODEL_NAME}.tar.bz2"
    if not archive.exists():
        _download(MODEL_URL, archive, status_cb=status_cb, progress_cb=progress_cb)

    if status_cb:
        status_cb("正在解压模型…")
    if progress_cb:
        progress_cb(0, 0)  # indeterminate
    logger.info("Extracting %s", archive)
    with tarfile.open(archive, "r:bz2") as tar:
        tar.extractall(MODELS_DIR)
    archive.unlink(missing_ok=True)
    return model_dir


class StreamingSTT:
    """Wraps sherpa-onnx OnlineRecognizer for incremental decoding.

    Construct with `StreamingSTT.prepare(...)` to allow injecting status/progress
    callbacks during the (potentially long) download + initialization step.
    """

    def __init__(self, recognizer, max_chars: int = 15) -> None:
        self.max_chars = max_chars
        self._lock = Lock()
        self.recognizer = recognizer
        self.stream = self.recognizer.create_stream()

    @classmethod
    def prepare(
        cls,
        max_chars: int = 15,
        status_cb: StatusCB = None,
        progress_cb: ProgressCB = None,
    ) -> "StreamingSTT":
        import sherpa_onnx

        model_dir = ensure_model(status_cb=status_cb, progress_cb=progress_cb)
        if status_cb:
            status_cb("正在初始化识别器…")
        if progress_cb:
            progress_cb(0, 0)

        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(model_dir / "tokens.txt"),
            encoder=str(model_dir / "encoder-epoch-99-avg-1.onnx"),
            decoder=str(model_dir / "decoder-epoch-99-avg-1.onnx"),
            joiner=str(model_dir / "joiner-epoch-99-avg-1.onnx"),
            num_threads=4,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
            provider="cpu",
        )
        return cls(recognizer, max_chars=max_chars)

    def reset(self) -> None:
        with self._lock:
            self.stream = self.recognizer.create_stream()

    def feed(self, samples: np.ndarray) -> str:
        """Feed an audio chunk; return current partial transcript."""
        with self._lock:
            self.stream.accept_waveform(SAMPLE_RATE, samples.tolist())
            while self.recognizer.is_ready(self.stream):
                self.recognizer.decode_stream(self.stream)
            return self.recognizer.get_result(self.stream).strip()

    def finalize(self) -> str:
        with self._lock:
            tail = np.zeros(int(SAMPLE_RATE * 0.4), dtype=np.float32)
            self.stream.accept_waveform(SAMPLE_RATE, tail.tolist())
            self.stream.input_finished()
            while self.recognizer.is_ready(self.stream):
                self.recognizer.decode_stream(self.stream)
            return self.recognizer.get_result(self.stream).strip()

    def at_limit(self, text: str) -> bool:
        return len(text) >= self.max_chars

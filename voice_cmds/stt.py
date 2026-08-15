"""Streaming STT using sherpa-onnx zipformer-bilingual-zh-en.

Downloads individual ONNX files from HuggingFace (with hf-mirror.com
fallback) on first run instead of the github tar.bz2 — github releases
are unreliable in CN due to SSL interference, and HF mirrors give us a
clean fallback path.
"""
from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

import numpy as np

from .audio import SAMPLE_RATE
from .config import MODELS_DIR
from .fetch import download_with_mirror_fallback

logger = logging.getLogger("voice_cmds.stt")

MODEL_NAME = "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"

# Try official HF first, then the well-known CN mirror
HF_HOSTS = (
    "https://huggingface.co",
    "https://hf-mirror.com",
)
HF_REPO = "csukuangfj/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20"
HF_FILES = (
    "encoder-epoch-99-avg-1.onnx",
    "decoder-epoch-99-avg-1.onnx",
    "joiner-epoch-99-avg-1.onnx",
    "tokens.txt",
)

StatusCB = Optional[Callable[[str], None]]
ProgressCB = Optional[Callable[[int, int], None]]


def ensure_model(status_cb: StatusCB = None, progress_cb: ProgressCB = None) -> Path:
    """Return the directory containing the model files; download missing ones."""
    model_dir = MODELS_DIR / MODEL_NAME
    encoder = model_dir / "encoder-epoch-99-avg-1.onnx"
    if encoder.exists() and (model_dir / "tokens.txt").exists():
        return model_dir

    model_dir.mkdir(parents=True, exist_ok=True)
    if status_cb:
        status_cb("正在下载语音识别模型 (~280MB, 4 个文件)…")
    for filename in HF_FILES:
        dst = model_dir / filename
        if dst.exists() and dst.stat().st_size > 0:
            continue
        download_with_mirror_fallback(
            HF_REPO, filename, dst, HF_HOSTS, status_cb=status_cb, progress_cb=progress_cb
        )
    return model_dir


class StreamingSTT:
    """Wraps sherpa-onnx OnlineRecognizer for incremental decoding."""

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

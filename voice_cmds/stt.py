"""Streaming STT using sherpa-onnx zipformer-bilingual-zh-en.

Downloads individual ONNX files from HuggingFace (with hf-mirror.com
fallback) on first run instead of the github tar.bz2 — github releases
are unreliable in CN due to SSL interference, and HF mirrors give us a
clean fallback path.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

import numpy as np
import requests

from .audio import SAMPLE_RATE
from .config import MODELS_DIR

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


def _hf_url(host: str, repo: str, filename: str) -> str:
    return f"{host}/{repo}/resolve/main/{filename}"


def _download_one(
    url: str,
    dest: Path,
    label: str = "",
    status_cb: StatusCB = None,
    progress_cb: ProgressCB = None,
    max_attempts: int = 3,
) -> None:
    """Download a single file with retry + backoff. Raises on final failure."""
    backoff = 2.0
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info("Downloading [%d/%d] %s -> %s", attempt, max_attempts, url, dest)
            with requests.get(url, stream=True, timeout=(15, 60)) as r:
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
                # Sanity: if Content-Length present, downloaded must match
                if total and downloaded != total:
                    raise IOError(
                        f"Short read: got {downloaded} of {total} bytes for {label or dest.name}"
                    )
            return  # success
        except Exception as e:
            last_err = e
            logger.warning("Attempt %d failed for %s: %s", attempt, label or dest.name, e)
            if dest.exists():
                try:
                    dest.unlink()
                except OSError:
                    pass
            if attempt < max_attempts:
                wait = backoff ** (attempt - 1)
                if status_cb:
                    status_cb(f"下载失败，{wait:.0f}s 后重试 ({attempt}/{max_attempts})…")
                time.sleep(wait)
    assert last_err is not None
    raise last_err


def _download_with_mirror_fallback(
    filename: str,
    dest: Path,
    status_cb: StatusCB = None,
    progress_cb: ProgressCB = None,
) -> None:
    """Try each HF host in order; raise if all fail."""
    last_err: Exception | None = None
    for host in HF_HOSTS:
        url = _hf_url(host, HF_REPO, filename)
        host_short = host.replace("https://", "")
        if status_cb:
            status_cb(f"正在下载 {filename} (来源: {host_short})…")
        try:
            _download_one(url, dest, label=filename, status_cb=status_cb, progress_cb=progress_cb)
            return
        except Exception as e:
            last_err = e
            logger.warning("Host %s exhausted for %s; trying next mirror", host, filename)
            continue
    assert last_err is not None
    raise last_err


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
        _download_with_mirror_fallback(filename, dst, status_cb=status_cb, progress_cb=progress_cb)
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

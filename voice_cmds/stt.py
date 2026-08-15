"""Streaming STT using sherpa-onnx zipformer-bilingual-zh-en.

Downloads individual ONNX files from HuggingFace (with hf-mirror.com
fallback) on first run. When every mirror is unreachable, falls back to
the whole-model tarball on GitHub Releases.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

import numpy as np

from .audio import SAMPLE_RATE
from .config import MODELS_DIR
from .fetch import download_one, download_with_mirror_fallback

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

# Last-resort whole-tarball source when every mirror is unreachable
# (e.g. networks that reset HTTPS to both huggingface.co and hf-mirror.com).
GITHUB_TARBALL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20.tar.bz2"
)

StatusCB = Optional[Callable[[str], None]]
ProgressCB = Optional[Callable[[int, int], None]]


def _download_tarball(
    model_dir: Path,
    status_cb: StatusCB = None,
    progress_cb: ProgressCB = None,
) -> None:
    """Download the GitHub tarball and extract the model files."""
    import tarfile

    tarball = MODELS_DIR / f"{MODEL_NAME}.tar.bz2"
    if status_cb:
        status_cb("镜像均不可用，正在从 GitHub 下载整包 (~500MB)…")
    try:
        download_one(
            GITHUB_TARBALL,
            tarball,
            label="github tarball",
            status_cb=status_cb,
            progress_cb=progress_cb,
        )
        if status_cb:
            status_cb("正在解压模型…")
        prefix = MODEL_NAME + "/"
        with tarfile.open(tarball, "r:bz2") as tf:
            for member in tf.getmembers():
                if not member.name.startswith(prefix):
                    continue
                rel = member.name[len(prefix):]
                if not rel:
                    continue
                dest = model_dir / rel
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                if dest.exists() and dest.stat().st_size > 0:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(member)
                with src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out, length=1 << 20)
    finally:
        tarball.unlink(missing_ok=True)


def ensure_model(status_cb: StatusCB = None, progress_cb: ProgressCB = None) -> Path:
    """Return the directory containing the model files; download missing ones."""
    model_dir = MODELS_DIR / MODEL_NAME
    encoder = model_dir / "encoder-epoch-99-avg-1.onnx"
    if encoder.exists() and (model_dir / "tokens.txt").exists():
        return model_dir

    model_dir.mkdir(parents=True, exist_ok=True)
    if status_cb:
        status_cb("正在下载语音识别模型 (~280MB, 4 个文件)…")
    try:
        for filename in HF_FILES:
            dst = model_dir / filename
            if dst.exists() and dst.stat().st_size > 0:
                continue
            download_with_mirror_fallback(
                HF_REPO, filename, dst, HF_HOSTS,
                status_cb=status_cb, progress_cb=progress_cb,
            )
    except Exception:
        logger.warning("Per-file mirrors exhausted; falling back to github tarball")
        _download_tarball(model_dir, status_cb=status_cb, progress_cb=progress_cb)

    if not (model_dir / "encoder-epoch-99-avg-1.onnx").exists() or not (
        model_dir / "tokens.txt"
    ).exists():
        raise RuntimeError("语音识别模型下载失败：所有来源均不可用")
    return model_dir


class StreamingSTT:
    """Wraps sherpa-onnx OnlineRecognizer for incremental decoding."""

    def __init__(self, recognizer) -> None:
        self._lock = Lock()
        self.recognizer = recognizer
        self.stream = self.recognizer.create_stream()

    @classmethod
    def prepare(
        cls,
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
        return cls(recognizer)

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

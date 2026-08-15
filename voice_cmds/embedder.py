"""ONNX embedder for command matching (BGE-small-zh-v1.5).

Replaces the torch / sentence-transformers stack (~700 MB inside the frozen
bundle) with onnxruntime + the lightweight `tokenizers` library. CLS pooling
followed by L2 normalization exactly reproduces the sentence-transformers
output (verified per-text cosine == 1.0 against the torch model), so matching
scores and behavior are unchanged.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from .config import MODELS_DIR
from .fetch import download_with_mirror_fallback

logger = logging.getLogger("voice_cmds.embedder")

EMBED_DIR_NAME = "bge-small-zh-v1.5"
EMBED_REPO = "Xenova/bge-small-zh-v1.5"
EMBED_HOSTS = ("https://huggingface.co", "https://hf-mirror.com")
EMBED_FILES = ("onnx/model.onnx", "tokenizer.json")

StatusCB = Optional[Callable[[str], None]]
ProgressCB = Optional[Callable[[int, int], None]]


def ensure_embedder_model(
    status_cb: StatusCB = None,
    progress_cb: ProgressCB = None,
) -> tuple[Path, Path]:
    """Return (model.onnx path, tokenizer.json path); download missing files."""
    model_dir = MODELS_DIR / EMBED_DIR_NAME
    onnx_path = model_dir / "onnx" / "model.onnx"
    tok_path = model_dir / "tokenizer.json"
    missing = [
        (fname, dst)
        for fname, dst in ((EMBED_FILES[0], onnx_path), (EMBED_FILES[1], tok_path))
        if not dst.exists() or dst.stat().st_size == 0
    ]
    if not missing:
        return onnx_path, tok_path
    model_dir.mkdir(parents=True, exist_ok=True)
    if status_cb:
        status_cb("正在下载语义匹配模型 (~95MB, 2 个文件)…")
    for fname, dst in missing:
        download_with_mirror_fallback(
            EMBED_REPO,
            fname,
            dst,
            EMBED_HOSTS,
            status_cb=status_cb,
            progress_cb=progress_cb,
        )
    return onnx_path, tok_path


class ONNXEmbedder:
    """SentenceTransformer-compatible .encode() backed by onnxruntime."""

    def __init__(self, onnx_path: Path, tok_path: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(tok_path))
        self._session = ort.InferenceSession(
            str(onnx_path), providers=["CPUExecutionProvider"]
        )

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return np.zeros((0, 512), dtype=np.float32)
        enc = self._tokenizer.encode_batch(list(texts))
        max_len = max(len(e.ids) for e in enc)
        ids = np.zeros((len(texts), max_len), dtype=np.int64)
        mask = np.zeros((len(texts), max_len), dtype=np.int64)
        for i, e in enumerate(enc):
            ids[i, : len(e.ids)] = e.ids
            mask[i, : len(e.ids)] = 1
        hidden = self._session.run(
            None,
            {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": np.zeros_like(ids),
            },
        )[0]
        # CLS pooling — matches 1_Pooling/config.json of bge-small-zh-v1.5
        # (pooling_mode_cls_token: true), then Normalize layer.
        vec = hidden[:, 0, :].astype(np.float32)
        if normalize_embeddings:
            vec = vec / np.linalg.norm(vec, axis=1, keepdims=True)
        return vec


def prepare_embedder(
    status_cb: StatusCB = None,
    progress_cb: ProgressCB = None,
) -> ONNXEmbedder:
    onnx_path, tok_path = ensure_embedder_model(
        status_cb=status_cb, progress_cb=progress_cb
    )
    if status_cb:
        status_cb("正在加载语义匹配模型…")
    return ONNXEmbedder(onnx_path, tok_path)

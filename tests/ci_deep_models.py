"""CI-only deep test: download the real models and run the whole ML stack.

Runs the STT recognizer (sherpa-onnx zipformer) and the BGE embedder
(tokenizers + the ORT C API binding against sherpa's bundled
libonnxruntime.dylib) on macOS — this is the one thing the headless
selftest cannot cover without the ~375MB models.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    import numpy as np

    print("deep test: downloading models and running the ML stack…")

    # --- STT ---------------------------------------------------------------
    from voice_cmds.stt import ensure_model, StreamingSTT

    ensure_model()
    stt = StreamingSTT.prepare(status_cb=print)
    silence = np.zeros(1600, dtype=np.float32)  # 100ms @ 16kHz
    partial = stt.feed(silence)
    assert isinstance(partial, str)
    stt.reset()
    final = stt.finalize()
    assert isinstance(final, str)
    print("STT ok (feed + finalize on silence):", repr(final))

    # --- Embedder -----------------------------------------------------------
    from voice_cmds.embedder import ensure_embedder_model, ONNXEmbedder

    onnx_path, tok_path = ensure_embedder_model(status_cb=print)
    emb = ONNXEmbedder(onnx_path, tok_path)
    vec = emb.encode(
        ["清空回收站", "打开访达", "qing1kong1hui2shou1zhan4"],
        normalize_embeddings=True,
    )
    assert vec.shape == (3, 512), vec.shape
    for row in vec:
        assert math.isfinite(float(row.sum())), "non-finite embedding"
        # L2-normalized rows must have unit norm
        assert abs(float(np.linalg.norm(row)) - 1.0) < 1e-4
    sim = float(vec[0] @ vec[1])
    print(f"Embedder ok: shape={vec.shape} sim(0,1)={sim:.4f}")

    # --- Matcher over the real embedder ------------------------------------
    from voice_cmds.config import Config
    from voice_cmds.matcher import CommandMatcher

    matcher = CommandMatcher(Config(), emb)
    r = matcher.match("清空回收站")
    assert r is not None and r.command.trigger == "清空回收站", r
    print("Matcher ok:", r.command.trigger, r.layer)

    print("DEEP TEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

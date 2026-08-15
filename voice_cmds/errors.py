"""Error categorization: map a traceback/message to (category, guidance).

Used by the unified error dialog so every failure gets a human-readable
category, a short next-step hint, and a collapsible copyable detail.
"""
from __future__ import annotations

# Order matters — first match wins.
_LOAD_HARD_KEYWORDS = (
    "dll load failed", "importerror", "modulenotfound",
)
_DOWNLOAD_KEYWORDS = (
    "ssl", "sslcertverificationerror", "maxretry", "connection",
    "timeout", "getaddrinfo", "resolve", "unexpected eof", "eof occurred",
    "http 4", " 401", " 403", " 404", " 429", "proxy", "tunnel",
    "download", "下载", "short read",
)
_LOAD_KEYWORDS = (
    "invalid", "recognizer", "encoder", "decoder", "joiner", "onnx",
    "初始化", "load", "加载", "tokens.txt",
)


def categorize_error(text: str) -> tuple[str, str]:
    """Return (category, guidance) for the error text (traceback or message)."""
    low = (text or "").lower()
    # A traceback like "ImportError: DLL load failed while importing _ssl"
    # contains both 'ssl' and 'dll load failed' — load problems win, because
    # the model files may be fine and only the frozen runtime is broken.
    if any(k in low for k in _LOAD_HARD_KEYWORDS):
        return (
            "模型 / 程序加载失败",
            "请重新打开程序重试；若持续失败，删除程序目录下的 models\\ 文件夹后重新启动，让程序重新下载模型。",
        )
    if any(k in low for k in _DOWNLOAD_KEYWORDS):
        return (
            "网络 / 下载失败",
            "请尝试更换网络环境后重试；也可以手动下载模型文件放入程序目录的 models\\ 文件夹（见 README）。",
        )
    if any(k in low for k in _LOAD_KEYWORDS):
        return (
            "模型 / 程序加载失败",
            "请重新打开程序重试；若持续失败，删除程序目录下的 models\\ 文件夹后重新启动，让程序重新下载模型。",
        )
    return (
        "未知错误",
        "请重新打开程序重试；若持续失败，请复制详细信息后反馈。",
    )

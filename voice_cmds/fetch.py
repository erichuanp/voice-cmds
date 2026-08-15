"""Shared HTTP download helpers with retry + CN mirror fallback.

Used by both the STT model downloader (stt.py) and the ONNX embedder
downloader (embedder.py).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("voice_cmds.fetch")

StatusCB = Optional[Callable[[str], None]]
ProgressCB = Optional[Callable[[int, int], None]]


def download_one(
    url: str,
    dest: Path,
    label: str = "",
    status_cb: StatusCB = None,
    progress_cb: ProgressCB = None,
    max_attempts: int = 3,
) -> None:
    """Download a single file with retry + backoff. Raises on final failure."""
    import requests

    backoff = 2.0
    last_err: Exception | None = None
    dest.parent.mkdir(parents=True, exist_ok=True)
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


def download_with_mirror_fallback(
    repo: str,
    filename: str,
    dest: Path,
    hosts: tuple[str, ...],
    status_cb: StatusCB = None,
    progress_cb: ProgressCB = None,
) -> None:
    """Try each host in order; raise if all fail."""
    last_err: Exception | None = None
    for host in hosts:
        url = f"{host}/{repo}/resolve/main/{filename}"
        host_short = host.replace("https://", "")
        if status_cb:
            status_cb(f"正在下载 {Path(filename).name} (来源: {host_short})…")
        try:
            download_one(url, dest, label=filename, status_cb=status_cb, progress_cb=progress_cb)
            return
        except Exception as e:
            last_err = e
            logger.warning("Host %s exhausted for %s; trying next mirror", host, filename)
            continue
    assert last_err is not None
    raise last_err

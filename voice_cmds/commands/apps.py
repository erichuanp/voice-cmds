"""Open-app dispatcher: launches the configured executable with optional args."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path


def open_app(entry: dict, logger: logging.Logger) -> None:
    path = entry.get("path", "")
    args = entry.get("args", []) or []
    if not path:
        raise RuntimeError(f"App entry has no path: {entry!r}")
    exe = Path(path).expanduser()
    cmd = [str(exe), *args]
    logger.info("Launch app: %s", cmd)
    # No CREATE_NO_WINDOW so GUI apps appear normally
    subprocess.Popen(cmd, shell=False)

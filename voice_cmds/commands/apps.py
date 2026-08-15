"""Open-app dispatcher: launches the configured executable with optional args."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path


def open_app(entry: dict, logger: logging.Logger) -> None:
    path = entry.get("path", "")
    args = entry.get("args", []) or []
    if not path:
        raise RuntimeError(f"应用条目缺少路径: {entry!r}")
    exe = Path(path).expanduser()
    if not exe.exists():
        raise FileNotFoundError(
            f"应用不存在: {exe}\n（请在 托盘 → 设置 → 命令 中修正路径）"
        )
    cmd = [str(exe), *args]
    logger.info("Launch app: %s", cmd)
    if exe.suffix.lower() == ".exe":
        # No CREATE_NO_WINDOW so GUI apps appear normally
        subprocess.Popen(cmd, shell=False)
    else:
        # .bat/.cmd/.ps1/.py/.lnk — dispatch through cmd.exe, which resolves
        # shortcuts and file associations the way double-clicking does.
        cmd_str = subprocess.list2cmdline(cmd)
        subprocess.Popen(cmd_str, shell=True)

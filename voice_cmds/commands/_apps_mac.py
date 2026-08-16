"""macOS app launcher: .app via `open`, scripts via their interpreters."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path


def build_command(path: Path, args: list[str]) -> list[str]:
    """The exact argv to launch `path` with `args` — exposed for tests."""
    suffix = path.suffix.lower()
    if suffix == ".app":
        return ["open", str(path)] + (["--args", *args] if args else [])
    if suffix in (".sh", ".command"):
        return ["/bin/sh", str(path), *args]
    if suffix == ".py":
        python = shutil.which("python3") or "/usr/bin/python3"
        return [python, str(path), *args]
    # Documents / unknown binaries — hand to LaunchServices like a double-click.
    return ["open", str(path)]


def open_app(entry: dict, logger: logging.Logger) -> None:
    path = entry.get("path", "")
    args = entry.get("args", []) or []
    if not path:
        raise RuntimeError(f"应用条目缺少路径: {entry!r}")
    target = Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError(
            f"应用不存在: {target}\n（请在 托盘 → 设置 → 命令 中修正路径）"
        )
    cmd = build_command(target, args)
    logger.info("Launch app: %s", cmd)
    subprocess.Popen(cmd, start_new_session=True)

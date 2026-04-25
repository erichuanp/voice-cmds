"""Windows autostart via HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.

No admin required — writes to the per-user Run key. Uses pythonw.exe (if
available in the same env) so no console window pops up at login.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import winreg

from .config import PROJECT_ROOT

logger = logging.getLogger("voice_cmds.autostart")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "voice-cmds"


def _command_line() -> str:
    """Quoted command line for boot. No --debug."""
    if getattr(sys, "frozen", False):
        # Frozen build — just launch the exe directly
        return f'"{Path(sys.executable).resolve()}"'
    # Source mode — pythonw (avoids console window) + main.py
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    interpreter = pyw if pyw.exists() else py
    main_py = PROJECT_ROOT / "main.py"
    return f'"{interpreter}" "{main_py}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as k:
            value, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> None:
    cmd = _command_line()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
    logger.info("Autostart enabled: %s", cmd)


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, APP_NAME)
        logger.info("Autostart disabled")
    except FileNotFoundError:
        pass


def apply(enabled: bool) -> None:
    if enabled:
        enable()
    else:
        disable()

"""Login autostart — platform facade.

- Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
  (no admin required). Uses pythonw.exe in source mode so no console
  window pops up at login.
- macOS: a per-user LaunchAgent plist in ~/Library/LaunchAgents
  (no admin required; loads at login).

Same public API on both platforms: is_enabled() / enable() / disable() /
apply(enabled).
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import PROJECT_ROOT

logger = logging.getLogger("voice_cmds.autostart")

APP_NAME = "voice-cmds"


def _command_line() -> list[str]:
    """argv for boot (no --debug). Source mode runs via the interpreter."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).resolve())]
    main_py = PROJECT_ROOT / "main.py"
    return [sys.executable, str(main_py)]


if sys.platform == "darwin":
    import plistlib

    _PLIST_PATH = (
        Path.home()
        / "Library"
        / "LaunchAgents"
        / "com.erichuanp.voice-cmds.plist"
    )

    def is_enabled() -> bool:
        try:
            with _PLIST_PATH.open("rb") as f:
                data = plistlib.load(f)
            return bool(data.get("RunAtLoad"))
        except (FileNotFoundError, OSError, ValueError):
            return False

    def enable() -> None:
        _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            "Label": "com.erichuanp.voice-cmds",
            "ProgramArguments": _command_line(),
            "RunAtLoad": True,
        }
        with _PLIST_PATH.open("wb") as f:
            plistlib.dump(plist, f)
        logger.info("Autostart enabled: %s", _PLIST_PATH)

    def disable() -> None:
        try:
            _PLIST_PATH.unlink()
        except FileNotFoundError:
            pass
        logger.info("Autostart disabled")

    def apply(enabled: bool) -> None:
        if enabled:
            enable()
        else:
            disable()

else:
    import winreg

    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _command_line_str() -> str:
        """Quoted command line for boot."""
        if getattr(sys, "frozen", False):
            return f'"{Path(sys.executable).resolve()}"'
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
        cmd = _command_line_str()
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

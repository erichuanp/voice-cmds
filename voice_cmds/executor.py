"""Dispatch a MatchResult to the right executor (system / app / custom script)."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from .commands import apps as apps_module
from .commands import system as system_module
from .config import DATA_DIR
from .matcher import MatchResult


class CommandExecutor:
    def __init__(self, config, logger: logging.Logger, scheduler=None) -> None:
        self.config = config
        self.logger = logger
        self.scheduler = scheduler

    def execute(self, result: MatchResult) -> None:
        spec = result.command
        self.logger.info(
            "Execute trigger=%r kind=%s layer=%s score=%.2f arg=%r",
            spec.trigger, spec.kind, result.layer, result.score, result.arg,
        )
        if spec.kind == "system":
            fn = spec.payload["fn"]
            if fn == "abort_shutdown" and self.scheduler is not None:
                system_module.dispatch(fn, self.config, self.logger, self.scheduler)
            else:
                system_module.dispatch(fn, self.config, self.logger)
        elif spec.kind == "app":
            apps_module.open_app(spec.payload, self.logger)
        elif spec.kind == "custom":
            self._run_script(spec.payload)
        elif spec.kind == "schedule":
            # "<时间>后<命令>" — register a delayed task with the scheduler.
            self.scheduler.add_delay(
                spec.payload["command"], int(spec.payload["delay_seconds"])
            )
        else:
            raise RuntimeError(f"Unknown command kind: {spec.kind}")

    def _run_script(self, payload: dict) -> None:
        rel = payload["script"]
        args = payload.get("args", []) or []
        # Resolve relative paths against the user data dir (on Windows this
        # IS the app dir, so behavior there is unchanged).
        script = Path(rel)
        if not script.is_absolute():
            script = (DATA_DIR / script).resolve()
        if not script.exists():
            raise FileNotFoundError(f"Script not found: {script}")
        self.logger.info("Custom script: %s %s", script, " ".join(args))
        if sys.platform == "darwin":
            self._run_script_mac(script, args)
        else:
            # Use list2cmdline + shell=True so .bat / .cmd / .ps1 dispatch via
            # cmd.exe without losing arg quoting.
            cmd_str = subprocess.list2cmdline([str(script), *args])
            subprocess.Popen(cmd_str, shell=True, cwd=str(DATA_DIR))

    def _run_script_mac(self, script: Path, args: list[str]) -> None:
        """Custom scripts on macOS: sh for shell scripts, python3 for .py,
        `open` for anything else (mirrors the app launcher)."""
        suffix = script.suffix.lower()
        if suffix in (".sh", ".command"):
            cmd = ["/bin/sh", str(script), *args]
        elif suffix == ".py":
            import shutil

            python = shutil.which("python3") or "/usr/bin/python3"
            cmd = [python, str(script), *args]
        else:
            cmd = ["open", str(script)]
        subprocess.Popen(cmd, cwd=str(DATA_DIR), start_new_session=True)

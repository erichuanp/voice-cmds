"""Dispatch a MatchResult to the right executor (system / app / custom script)."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from .commands import apps as apps_module
from .commands import system as system_module
from .config import PROJECT_ROOT
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
        # Resolve relative path against project root if needed
        script = Path(rel)
        if not script.is_absolute():
            script = (PROJECT_ROOT / script).resolve()
        if not script.exists():
            raise FileNotFoundError(f"Script not found: {script}")
        # Use list2cmdline + shell=True so .bat / .cmd / .ps1 dispatch via cmd.exe
        # without losing arg quoting.
        cmd_str = subprocess.list2cmdline([str(script), *args])
        self.logger.info("Custom script: %s", cmd_str)
        subprocess.Popen(cmd_str, shell=True, cwd=str(PROJECT_ROOT))

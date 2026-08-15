"""Scheduled task engine for voice-cmds.

Tasks live in config/tasks.json and survive restarts. Four kinds:

- ``once``  : one-shot at a specific datetime (set in the task editor)
- ``daily`` : repeat every day at HH:MM (never shows ✓/✗ icons)
- ``delay`` : one-shot N seconds after creation
- ``loop``  : repeat every N seconds (never shows ✓/✗ icons); on restart the
  next fire is derived from creation time — ``next = created + k*period``
  with the smallest k that lands in the future, so a 12:00 "every 30 min"
  task reopened at 12:35 fires at 13:00.

Restart rules for missed one-shots (no icon for daily/loop):
- once  in the past  → ✗ "程序未运行时已过期"
- delay in the past  → ✗ "程序未运行时已过期"

Status icons (✓/✗) only apply to once/delay tasks after they fire; the
tooltip of a ✗ row carries the failure reason.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger("voice_cmds.scheduler")


@dataclass
class Task:
    id: str
    kind: str            # once | daily | delay | loop
    command: str
    at: str = ""         # "YYYY-MM-DD HH:MM:SS" for once
    daily_time: str = ""  # "HH:MM" for daily
    delay_seconds: int = 0
    period_seconds: int = 0
    created_at: float = 0.0  # epoch — anchor for delay/loop math
    status: str = "pending"  # pending | ok | error (one-shots only)
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        return cls(
            id=d.get("id") or uuid.uuid4().hex,
            kind=d.get("kind", "once"),
            command=d.get("command", ""),
            at=d.get("at", ""),
            daily_time=d.get("daily_time", ""),
            delay_seconds=int(d.get("delay_seconds", 0)),
            period_seconds=int(d.get("period_seconds", 0)),
            created_at=float(d.get("created_at", time.time())),
            status=d.get("status", "pending"),
            error=d.get("error", ""),
        )


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


class TaskScheduler(QObject):
    """1-second tick loop that fires due tasks on the main thread."""

    tasks_changed = Signal()

    def __init__(self, config, matcher, executor, tray=None) -> None:
        super().__init__()
        self.config = config
        self.matcher = matcher
        self.executor = executor
        self.tray = tray
        self._tasks: list[Task] = []
        # id -> last fired date ("YYYY-MM-DD") for daily tasks
        self._daily_fired: dict[str, str] = {}
        self._load()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # --- persistence ------------------------------------------------------
    @property
    def tasks(self) -> list[Task]:
        return list(self._tasks)

    def _load(self) -> None:
        now = time.time()
        today = datetime.now().strftime("%Y-%m-%d")
        for raw in self.config.tasks:
            try:
                t = Task.from_dict(raw)
            except Exception as e:
                logger.warning("Skipping malformed task %r: %s", raw, e)
                continue
            if t.kind == "once":
                dt = _parse_dt(t.at)
                if dt is not None and dt.timestamp() <= now:
                    t.status, t.error = "error", "程序未运行时已过期"
            elif t.kind == "daily":
                # No catch-up: it only fires while the program is alive.
                self._daily_fired[t.id] = ""  # fires at next matching HH:MM
            elif t.kind == "delay":
                if now >= t.created_at + t.delay_seconds:
                    t.status, t.error = "error", "程序未运行时已过期"
            # loop: next fire derived on the fly from created_at
            self._tasks.append(t)
        logger.info("Scheduler loaded %d task(s)", len(self._tasks))

    def _save(self) -> None:
        self.config.tasks = [t.to_dict() for t in self._tasks]
        self.config.save_tasks()
        self.tasks_changed.emit()

    # --- public API (used by executor + task editor) ----------------------
    def add_delay(self, command: str, seconds: int) -> Task:
        t = Task(
            id=uuid.uuid4().hex,
            kind="delay",
            command=command,
            delay_seconds=max(1, int(seconds)),
            created_at=time.time(),
        )
        self._tasks.append(t)
        logger.info("Scheduled delay task %s: %ds later: %r", t.id, t.delay_seconds, command)
        self._save()
        return t

    def add_once(self, command: str, dt: datetime) -> Task:
        t = Task(
            id=uuid.uuid4().hex,
            kind="once",
            command=command,
            at=dt.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self._tasks.append(t)
        self._save()
        return t

    def add_daily(self, command: str, hhmm: str) -> Task:
        t = Task(id=uuid.uuid4().hex, kind="daily", command=command, daily_time=hhmm)
        self._daily_fired[t.id] = ""
        self._tasks.append(t)
        self._save()
        return t

    def add_loop(self, command: str, period_seconds: int) -> Task:
        t = Task(
            id=uuid.uuid4().hex,
            kind="loop",
            command=command,
            period_seconds=max(1, int(period_seconds)),
            created_at=time.time(),
        )
        self._tasks.append(t)
        self._save()
        return t

    def update(self, task: Task) -> None:
        for i, t in enumerate(self._tasks):
            if t.id == task.id:
                task.status, task.error = "pending", ""
                if task.kind == "daily":
                    self._daily_fired[task.id] = ""
                self._tasks[i] = task
                self._save()
                return
        raise KeyError(task.id)

    def remove(self, task_id: str) -> None:
        self._tasks = [t for t in self._tasks if t.id != task_id]
        self._daily_fired.pop(task_id, None)
        self._save()

    def remove_finished(self) -> int:
        """Delete all executed/failed one-shot rows (✓/✗)."""
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t.status == "pending"]
        removed = before - len(self._tasks)
        if removed:
            self._save()
        return removed

    def cancel_matching(self, fn_names: set[str]) -> int:
        """Cancel pending tasks whose command matches a system fn (e.g. 关机/重启)."""
        cancelled = 0
        kept = []
        for t in self._tasks:
            if t.status != "pending":
                kept.append(t)
                continue
            try:
                r = self.matcher.match(t.command)
            except Exception:
                r = None
            if (
                r
                and r.command.kind == "system"
                and r.command.payload.get("fn") in fn_names
            ):
                logger.info("Cancelling pending task %s (%r)", t.id, t.command)
                cancelled += 1
                continue
            kept.append(t)
        if cancelled:
            self._tasks = kept
            self._save()
        return cancelled

    def run_command_now(self, command: str) -> tuple[bool, str]:
        """Execute a command text once (task editor's 立刻执行). No task created."""
        return self._execute_text(command)

    # --- ticking ----------------------------------------------------------
    def _tick(self) -> None:
        now = time.time()
        now_dt = datetime.now()
        for t in self._tasks:
            try:
                if t.kind == "once":
                    if t.status == "pending":
                        dt = _parse_dt(t.at)
                        if dt is not None and dt.timestamp() <= now:
                            self._fire(t)
                elif t.kind == "daily":
                    if now_dt.strftime("%H:%M") == t.daily_time:
                        today = now_dt.strftime("%Y-%m-%d")
                        if self._daily_fired.get(t.id) != today:
                            self._daily_fired[t.id] = today
                            self._fire(t)
                elif t.kind == "delay":
                    if t.status == "pending" and now >= t.created_at + t.delay_seconds:
                        self._fire(t)
                elif t.kind == "loop":
                    if now >= self._loop_next_fire(t):
                        self._fire(t)
            except Exception:
                logger.exception("Scheduler tick failed for task %s", getattr(t, "id", "?"))

    @staticmethod
    def _loop_next_fire(t: Task) -> float:
        """created + k*period, smallest k whose time is in the future."""
        elapsed = time.time() - t.created_at
        if elapsed <= 0:
            return t.created_at + t.period_seconds
        k = int(elapsed // t.period_seconds) + 1
        return t.created_at + k * t.period_seconds

    def _fire(self, t: Task) -> None:
        logger.info("Firing task %s (%s): %r", t.id, t.kind, t.command)
        ok, err = self._execute_text(t.command)
        if t.kind in ("once", "delay"):
            t.status = "ok" if ok else "error"
            t.error = err
        if not ok and self.tray is not None:
            try:
                self.tray.notify("voice-cmds — 定时任务失败", f"{t.command}\n{err}")
            except Exception:
                pass
        self._save()

    def _execute_text(self, text: str) -> tuple[bool, str]:
        if not text or not text.strip():
            return False, "命令为空"
        try:
            result = self.matcher.match(text.strip())
        except Exception as e:
            logger.exception("Scheduled match error: %s", e)
            return False, f"匹配失败: {e}"
        if result is None:
            return False, "未匹配到命令"
        try:
            self.executor.execute(result)
            return True, ""
        except Exception as e:
            logger.exception("Scheduled execution error: %s", e)
            return False, str(e)

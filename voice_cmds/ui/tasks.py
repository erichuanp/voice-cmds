"""Scheduled task windows: list (tray → 定时任务) + add/edit dialog.

Icons: only one-shot tasks (once / delay) show ✓/✗ after execution;
pending one-shots and every daily/loop row never show an icon. A ✗ row's
tooltip carries the failure reason.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..matcher import format_delay
from ..scheduler import Task
from .common import DIALOG_STYLE, hint


def _status_icon(ok: bool) -> QIcon:
    """Small green ✓ / red ✗ drawn on a transparent pixmap."""
    pm = QPixmap(18, 18)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor("#00C853") if ok else QColor("#E53935")
    pen = p.pen()
    pen.setColor(color)
    pen.setWidthF(2.4)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    if ok:
        p.drawLine(4, 9, 8, 13)
        p.drawLine(8, 13, 14, 5)
    else:
        p.drawLine(5, 5, 13, 13)
        p.drawLine(13, 5, 5, 13)
    p.end()
    return QIcon(pm)


def _delay_desc(seconds: int) -> str:
    return f"{format_delay(seconds)}后"


class TaskEditDialog(QDialog):
    """Add/edit one scheduled task. 立刻执行 runs the command once without
    saving or closing; only 保存 / 取消 / window-X leave the dialog."""

    def __init__(self, scheduler, task: Task | None = None, parent=None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.task = task
        self.setWindowTitle("添加定时任务" if task is None else "编辑定时任务")
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(430, 230)

        # --- timed switch + datetime ---
        self.timed_check = QCheckBox("定时")
        self.dt_edit = QDateTimeEdit()
        self.dt_edit.setCalendarPopup(True)
        self.dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        default_dt = datetime.now() + timedelta(hours=1)
        self.dt_edit.setDateTime(default_dt.replace(second=0, microsecond=0))

        # --- repeat switch (每日重复 ⇄ 循环执行) ---
        self.repeat_check = QCheckBox("每日重复")

        # --- delay spins (时/分/秒) ---
        self.h_spin = QSpinBox()
        self.h_spin.setRange(0, 167)
        self.h_spin.setSuffix(" 时")
        self.m_spin = QSpinBox()
        self.m_spin.setRange(0, 59)
        self.m_spin.setSuffix(" 分")
        self.s_spin = QSpinBox()
        self.s_spin.setRange(0, 59)
        self.s_spin.setSuffix(" 秒")
        delay_row = QHBoxLayout()
        delay_row.addWidget(self.h_spin)
        delay_row.addWidget(self.m_spin)
        delay_row.addWidget(self.s_spin)
        self._delay_label = QLabel("后执行（从添加时起算）")
        delay_row.addWidget(self._delay_label)
        delay_row.addStretch(1)
        self._delay_row_widgets = [self.h_spin, self.m_spin, self.s_spin]

        # --- command ---
        self.cmd_edit = QLineEdit()
        self.cmd_edit.setPlaceholderText("例如：清空回收站")

        # --- buttons ---
        run_btn = QPushButton("立刻执行")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        run_btn.clicked.connect(self._run_now)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)
        timed_row = QHBoxLayout()
        timed_row.setSpacing(10)
        timed_row.addWidget(self.timed_check)
        timed_row.addWidget(self.dt_edit)
        timed_row.addWidget(self.repeat_check)
        timed_row.addStretch(1)
        layout.addLayout(timed_row)
        layout.addLayout(delay_row)
        layout.addWidget(QLabel("命令内容"))
        layout.addWidget(self.cmd_edit)
        btn_row = QHBoxLayout()
        btn_row.addWidget(run_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(buttons)
        layout.addLayout(btn_row)

        self.timed_check.toggled.connect(self._sync_visibility)
        self._prefill()
        self._sync_visibility()

    # --- state ------------------------------------------------------------
    def _prefill(self) -> None:
        t = self.task
        if t is None:
            return
        self.cmd_edit.setText(t.command)
        if t.kind in ("once", "daily"):
            self.timed_check.setChecked(True)
            if t.kind == "daily":
                hh, mm = (int(x) for x in t.daily_time.split(":"))
                base = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
                self.dt_edit.setDateTime(base)
                self.repeat_check.setChecked(True)
            else:
                try:
                    dt = datetime.strptime(t.at, "%Y-%m-%d %H:%M:%S")
                    self.dt_edit.setDateTime(dt)
                except ValueError:
                    pass
                self.repeat_check.setChecked(False)
        else:  # delay / loop
            self.timed_check.setChecked(False)
            secs = t.period_seconds if t.kind == "loop" else t.delay_seconds
            h, rem = divmod(secs, 3600)
            m, s = divmod(rem, 60)
            self.h_spin.setValue(h)
            self.m_spin.setValue(m)
            self.s_spin.setValue(s)
            self.repeat_check.setChecked(t.kind == "loop")

    def _sync_visibility(self) -> None:
        timed = self.timed_check.isChecked()
        self.dt_edit.setVisible(timed)
        self.repeat_check.setText("每日重复" if timed else "循环执行")
        for w in self._delay_row_widgets:
            w.setVisible(not timed)
        self._delay_label.setVisible(not timed)

    def _total_seconds(self) -> int:
        return self.h_spin.value() * 3600 + self.m_spin.value() * 60 + self.s_spin.value()

    # --- actions ----------------------------------------------------------
    def _run_now(self) -> None:
        cmd = self.cmd_edit.text().strip()
        if not cmd:
            QMessageBox.warning(self, "voice-cmds", "请先输入命令内容。")
            return
        try:
            self.scheduler.run_command_now(cmd)
        except Exception as e:
            QMessageBox.warning(self, "voice-cmds", f"执行失败：{e}")

    def _save(self) -> None:
        cmd = self.cmd_edit.text().strip()
        if not cmd:
            QMessageBox.warning(self, "voice-cmds", "命令内容不能为空。")
            return
        timed = self.timed_check.isChecked()
        repeat = self.repeat_check.isChecked()
        try:
            if timed and repeat:
                new = Task(
                    id=self.task.id if self.task else "",
                    kind="daily",
                    command=cmd,
                    daily_time=self.dt_edit.time().toString("HH:mm"),
                )
            elif timed:
                dt = self.dt_edit.dateTime().toPython()
                if dt <= datetime.now():
                    QMessageBox.warning(
                        self, "voice-cmds", "所选时间已过，请选择未来的时间。"
                    )
                    return
                new = Task(
                    id=self.task.id if self.task else "",
                    kind="once",
                    command=cmd,
                    at=dt.strftime("%Y-%m-%d %H:%M:%S"),
                )
            else:
                total = self._total_seconds()
                if total <= 0:
                    QMessageBox.warning(self, "voice-cmds", "时/分/秒不能全为 0。")
                    return
                if repeat:
                    new = Task(
                        id=self.task.id if self.task else "",
                        kind="loop",
                        command=cmd,
                        period_seconds=total,
                    )
                else:
                    new = Task(
                        id=self.task.id if self.task else "",
                        kind="delay",
                        command=cmd,
                        delay_seconds=total,
                    )
        except Exception as e:
            QMessageBox.warning(self, "voice-cmds", f"保存失败：{e}")
            return
        try:
            if self.task is not None:
                self.scheduler.update(new)
            else:
                if new.kind == "once":
                    self.scheduler.add_once(new.command, datetime.strptime(new.at, "%Y-%m-%d %H:%M:%S"))
                elif new.kind == "daily":
                    self.scheduler.add_daily(new.command, new.daily_time)
                elif new.kind == "loop":
                    self.scheduler.add_loop(new.command, new.period_seconds)
                else:
                    self.scheduler.add_delay(new.command, new.delay_seconds)
        except Exception as e:
            QMessageBox.warning(self, "voice-cmds", f"保存失败：{e}")
            return
        self.accept()


class TasksWindow(QDialog):
    def __init__(self, scheduler, parent=None) -> None:
        super().__init__(parent)
        self.scheduler = scheduler
        self.setWindowTitle("定时任务")
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(560, 360)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["状态", "时间", "命令"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionBehavior(QTreeWidget.SelectRows)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)

        add_btn = QPushButton("添加")
        clear_btn = QPushButton("删除已完成与失败的任务")
        close_btn = QPushButton("关闭")
        add_btn.clicked.connect(lambda: self._open_editor(None))
        clear_btn.clicked.connect(self._remove_finished)
        close_btn.clicked.connect(self.close)

        btn_row = QHBoxLayout()
        btn_row.addWidget(add_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(hint(
            "右键任务行可编辑或删除；仅一次性任务执行后显示 ✓ / ✗（悬停 ✗ 查看失败原因）。"
        ))
        layout.addWidget(self.tree, 1)
        layout.addLayout(btn_row)

        self.scheduler.tasks_changed.connect(self.refresh)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._refresh_timer.stop()

    # --- refresh ----------------------------------------------------------
    def refresh(self) -> None:
        self.tree.clear()
        now = time.time()
        for t in self.scheduler.tasks:
            item = QTreeWidgetItem(["", self._time_text(t, now), t.command])
            # Icons only for executed one-shots
            if t.kind in ("once", "delay") and t.status in ("ok", "error"):
                item.setIcon(0, _status_icon(t.status == "ok"))
                if t.status == "error":
                    item.setToolTip(0, t.error or "执行失败")
                    item.setToolTip(2, t.error or "执行失败")
            elif t.status == "pending":
                item.setToolTip(0, "未执行")
            item.setData(0, Qt.UserRole, t.id)
            self.tree.addTopLevelItem(item)

    def _time_text(self, t: Task, now: float) -> str:
        if t.kind == "once":
            return t.at
        if t.kind == "daily":
            return f"每天 {t.daily_time}"
        if t.kind == "delay":
            remaining = max(0, int(t.created_at + t.delay_seconds - now))
            state = ""
            if t.status == "ok":
                state = "（已执行）"
            elif t.status == "error":
                state = "（失败）"
            elif remaining > 0:
                state = f"（剩余 {format_delay(remaining)}）"
            return f"{_delay_desc(t.delay_seconds)}{state}"
        # loop
        remaining = max(0, int(self.scheduler._loop_next_fire(t) - now))
        return f"每 {format_delay(t.period_seconds)} 循环（下次 {format_delay(remaining)}）"

    # --- actions ----------------------------------------------------------
    def _current_task(self) -> Task | None:
        item = self.tree.currentItem()
        if item is None:
            return None
        tid = item.data(0, Qt.UserRole)
        for t in self.scheduler.tasks:
            if t.id == tid:
                return t
        return None

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        act_edit = menu.addAction("编辑")
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        task = self._current_task()
        if task is None:
            return
        if chosen == act_edit:
            self._open_editor(task)
        elif chosen == act_del:
            self.scheduler.remove(task.id)

    def _open_editor(self, task: Task | None) -> None:
        d = TaskEditDialog(self.scheduler, task=task, parent=self)
        d.exec()

    def _remove_finished(self) -> None:
        removed = self.scheduler.remove_finished()
        if removed == 0:
            QMessageBox.information(self, "voice-cmds", "没有已完成或失败的任务。")

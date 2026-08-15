"""Settings dialog: hotkey capture, recognition and commands.

Layout follows one convention everywhere: QFormLayout rows inside named
QGroupBox sections, gray hint() labels for secondary info, and plain
Chinese 保存/取消 buttons (no mixed-language QDialogButtonBox).
"""
from __future__ import annotations

import json
import threading

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .common import DIALOG_STYLE, hint
from .hotkeyedit import HotkeyLineEdit


class _CommandDialog(QDialog):
    """Add/edit one entry: 「打开<触发词>」 (app) or 「触发词」 (script/exe).

    App triggers support aliases separated by ';' / '；' — 打开A and 打开B
    then open the same thing.
    """

    def __init__(self, parent=None, entry: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("添加命令" if entry is None else "编辑命令")
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(460, 210)
        entry = entry or {}
        self.kind = entry.get("kind", "custom")

        self.radio_app = QRadioButton("打开<触发词>")
        self.radio_cmd = QRadioButton("触发词")
        self.radio_app.setChecked(self.kind == "app")
        self.radio_cmd.setChecked(self.kind != "app")

        self.trigger = QLineEdit(entry.get("trigger", ""))
        self.trigger.setPlaceholderText("例如：微信；weixin")
        self.path = QLineEdit(entry.get("path", entry.get("script", "")))
        self.args = QLineEdit(" ".join(entry.get("args", []) or []))
        self.args.setPlaceholderText("空格分隔")
        browse = QPushButton("浏览…")
        browse.setMinimumWidth(64)
        browse.clicked.connect(self._browse)

        kind_row = QHBoxLayout()
        kind_row.addWidget(self.radio_app)
        kind_row.addWidget(self.radio_cmd)
        kind_row.addStretch(1)

        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)

        form = QFormLayout()
        form.setContentsMargins(16, 16, 16, 8)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.addRow("类型", kind_row)
        form.addRow("触发词", self.trigger)
        form.addRow("路径", path_row)
        form.addRow("附加参数", self.args)

        save_btn = QPushButton("保存")
        cancel_btn = QPushButton("取消")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 8, 16, 16)
        btn_row.addWidget(hint("“打开<触发词>”的触发词支持 ; 或 ；分隔多个别名"))
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(form)
        layout.addLayout(btn_row)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择程序或脚本", "",
            "程序与脚本 (*.bat *.cmd *.ps1 *.py *.exe *.lnk);;All files (*.*)",
        )
        if path:
            self.path.setText(path)

    def value(self) -> dict:
        args = [a for a in self.args.text().split() if a]
        kind = "app" if self.radio_app.isChecked() else "custom"
        v = {"kind": kind, "trigger": self.trigger.text().strip(), "args": args}
        if kind == "app":
            v["path"] = self.path.text().strip()
        else:
            v["script"] = self.path.text().strip()
        return v


def _parse_version(v: str) -> tuple:
    v = (v or "").strip().lstrip("vV")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p.split("-")[0]))
        except ValueError:
            parts.append(0)
    return tuple(parts) or (0,)


def compare_versions(current: str, latest: str) -> int:
    """-1 if current < latest, 0 if equal, 1 if current > latest."""
    a, b = _parse_version(current), _parse_version(latest)
    return (a > b) - (a < b)


def fetch_latest_version() -> str:
    """Query the latest release tag from GitHub (raises on any failure)."""
    import requests

    r = requests.get(
        "https://api.github.com/repos/erichuanp/voice-cmds/releases/latest",
        timeout=10,
    )
    r.raise_for_status()
    return str(r.json()["tag_name"])


class SettingsDialog(QDialog):
    config_changed = Signal()
    update_checked = Signal(str, str)      # (latest_tag, error)
    update_progress = Signal(str, int)     # (status_text, percent or -1)
    update_prepared = Signal(int, int, str)  # (changed, deleted, error)
    update_ready = Signal()

    def __init__(self, config, debug: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setStyleSheet(DIALOG_STYLE)
        self.config = config
        self.debug = debug
        self.resize(560, 540)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "通用")
        tabs.addTab(self._build_commands_tab(), "命令")
        tabs.addTab(self._build_about_tab(), "关于")

        self.update_checked.connect(self._on_update_checked)
        self.update_progress.connect(self._on_update_progress)
        self.update_prepared.connect(self._on_update_prepared)

        save_btn = QPushButton("保存")
        cancel_btn = QPushButton("取消")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(tabs, 1)
        layout.addLayout(btn_row)

    # --- General tab ------------------------------------------------------
    def _build_general_tab(self) -> QWidget:
        s = self.config.settings
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(10)

        # 热键
        hotkey_box = QGroupBox("热键")
        f = QFormLayout(hotkey_box)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(8)
        self.start_key = HotkeyLineEdit(
            s["hotkey"]["start"], two_keys=True, hint="请设置两个键的快捷键..."
        )
        self.stop_key = HotkeyLineEdit(
            s["hotkey"]["stop"], two_keys=False, hint="请设置快捷键..."
        )
        self.cancel_key = HotkeyLineEdit(
            s["hotkey"]["cancel"], two_keys=False, hint="请设置快捷键..."
        )
        self._hotkey_fields = (
            (self.start_key, s["hotkey"]["start"], "开始录音"),
            (self.stop_key, s["hotkey"]["stop"], "结束识别"),
            (self.cancel_key, s["hotkey"]["cancel"], "取消"),
        )
        for field, default, label in self._hotkey_fields:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(field, 1)
            reset = QPushButton("重置")
            reset.setMinimumWidth(56)
            reset.clicked.connect(lambda _=False, fl=field, d=default: fl.reset_to_default(d))
            row.addWidget(reset)
            row_widget = QWidget()
            row_widget.setLayout(row)
            f.addRow(label, row_widget)
        f.addRow("", hint("点击输入框后按下按键即可录制；开始录音需要两个键，结束/取消一个键。鼠标支持右键，不支持左键。Esc 取消录制。"))
        v.addWidget(hotkey_box)

        # 识别
        rec_box = QGroupBox("识别")
        f = QFormLayout(rec_box)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(8)
        self.stop_mode = QComboBox()
        self.stop_mode.addItem("热键停止", "hotkey")
        self.stop_mode.addItem("静音自动停止", "vad")
        idx = self.stop_mode.findData(s.get("stop_mode", "hotkey"))
        self.stop_mode.setCurrentIndex(max(0, idx))
        f.addRow("结束方式", self.stop_mode)

        self.vad_ms = QSpinBox()
        self.vad_ms.setRange(200, 5000)
        self.vad_ms.setSingleStep(100)
        self.vad_ms.setValue(s.get("vad_silence_ms", 500))
        self.vad_ms.setSuffix(" ms")
        f.addRow("静音时长", self.vad_ms)
        self._vad_hint = hint(self._vad_hint_text(self.vad_ms.value()))
        f.addRow("", self._vad_hint)
        self.vad_ms.valueChanged.connect(
            lambda ms: self._vad_hint.setText(self._vad_hint_text(ms))
        )

        self.result_ms = QSpinBox()
        self.result_ms.setRange(0, 10000)
        self.result_ms.setSingleStep(100)
        self.result_ms.setValue(int(s.get("ui", {}).get("result_text_ms", 1000)))
        self.result_ms.setSuffix(" ms")
        f.addRow("结果展示时长", self.result_ms)
        self._result_hint = hint(self._result_hint_text(self.stop_mode.currentData()))
        f.addRow("", self._result_hint)
        self.stop_mode.currentIndexChanged.connect(
            lambda _i: self._sync_result_ms()
        )
        self._sync_result_ms()
        v.addWidget(rec_box)

        # 启动
        boot_box = QGroupBox("启动")
        f = QFormLayout(boot_box)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(8)
        from .. import autostart
        self.autostart_check = QCheckBox("开机自启动")
        self.autostart_check.setChecked(autostart.is_enabled())
        f.addRow("", self.autostart_check)
        if self.debug:
            self.autostart_check.setEnabled(False)
            f.addRow("", hint("调试模式下不修改开机启动项"))
        v.addWidget(boot_box)

        v.addWidget(hint("保存后程序自动重启以应用更改。"))
        v.addStretch(1)
        return w

    @staticmethod
    def _vad_hint_text(ms: int) -> str:
        return f"识别到内容后静音达到 {ms} ms 自动结束；停止/取消热键始终可用。"

    @staticmethod
    def _result_hint_text(mode: str) -> str:
        if mode == "hotkey":
            return "热键停止模式下识别结束后立即执行（结果展示时长恒为 0）。"
        return "静音自动停止模式下，识别结果停留多久后再执行命令（0 = 立即执行）。"

    def _sync_result_ms(self) -> None:
        hotkey_mode = self.stop_mode.currentData() == "hotkey"
        self.result_ms.setEnabled(not hotkey_mode)
        self._result_hint.setText(self._result_hint_text(self.stop_mode.currentData()))

    # --- Commands tab (merged: 打开 X + 脚本/程序) ------------------------
    def _build_commands_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        self.cmd_list = QListWidget()
        for entry in self.config.apps:
            self._add_cmd_item({**entry, "kind": "app"})
        for entry in self.config.commands:
            self._add_cmd_item({**entry, "kind": "custom"})
        btns = QHBoxLayout()
        add = QPushButton("添加")
        edit = QPushButton("编辑")
        rm = QPushButton("删除")
        export = QPushButton("导出")
        import_ = QPushButton("导入")
        add.clicked.connect(self._add_cmd)
        edit.clicked.connect(self._edit_cmd)
        rm.clicked.connect(self._remove_cmd)
        export.clicked.connect(self._export_cmds)
        import_.clicked.connect(self._import_cmds)
        btns.addWidget(add)
        btns.addWidget(edit)
        btns.addWidget(rm)
        btns.addStretch(1)
        btns.addWidget(import_)
        btns.addWidget(export)
        layout.addWidget(hint(
            "「打开<触发词>」用于启动程序（触发词可用 ; 或 ；分隔多个别名）；"
            "「触发词」用于脚本或程序（.bat/.cmd/.ps1/.py/.exe/.lnk）。"
            "可用 导入/导出 通过 .jsonl 备份或迁移命令。"
        ))
        layout.addWidget(self.cmd_list, 1)
        layout.addLayout(btns)
        return w

    @staticmethod
    def _entry_text(entry: dict) -> str:
        if entry["kind"] == "app":
            return f"打开 {entry['trigger']}  →  {entry['path']}"
        return f"{entry['trigger']}  →  {entry['script']}"

    def _add_cmd_item(self, entry: dict) -> None:
        item = QListWidgetItem(self._entry_text(entry))
        item.setData(0x100, entry)
        self.cmd_list.addItem(item)

    def _add_cmd(self) -> None:
        d = _CommandDialog(self)
        if d.exec() == QDialog.Accepted:
            v = d.value()
            path_key = "path" if v["kind"] == "app" else "script"
            if not v["trigger"] or not v[path_key]:
                QMessageBox.warning(self, "无效", "触发词和路径都必填。")
                return
            self._add_cmd_item(v)

    def _edit_cmd(self) -> None:
        item = self.cmd_list.currentItem()
        if not item:
            return
        d = _CommandDialog(self, entry=item.data(0x100))
        if d.exec() == QDialog.Accepted:
            v = d.value()
            item.setText(self._entry_text(v))
            item.setData(0x100, v)

    def _remove_cmd(self) -> None:
        row = self.cmd_list.currentRow()
        if row >= 0:
            self.cmd_list.takeItem(row)

    def _export_cmds(self) -> None:
        entries = [
            self.cmd_list.item(i).data(0x100)
            for i in range(self.cmd_list.count())
        ]
        if not entries:
            QMessageBox.information(self, "导出", "当前没有可导出的命令。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出命令", "voice-cmds-commands.jsonl",
            "JSONL (*.jsonl);;All files (*.*)",
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for e in entries:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except Exception as e:
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出", f"已导出 {len(entries)} 条命令：\n{path}")

    def _import_cmds(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入命令", "", "JSONL (*.jsonl);;All files (*.*)"
        )
        if not path:
            return
        ok = skipped = 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        skipped += 1
                        continue
                    kind = obj.get("kind")
                    trigger = (obj.get("trigger") or "").strip()
                    args = obj.get("args", []) or []
                    entry = None
                    if kind == "app" and trigger and obj.get("path"):
                        entry = {"kind": "app", "trigger": trigger,
                                 "path": obj["path"], "args": args}
                    elif kind == "custom" and trigger and obj.get("script"):
                        entry = {"kind": "custom", "trigger": trigger,
                                 "script": obj["script"], "args": args}
                    if entry is None:
                        skipped += 1
                        continue
                    self._add_cmd_item(entry)
                    ok += 1
        except Exception as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        QMessageBox.information(
            self, "导入",
            f"导入完成：新增 {ok} 条，跳过 {skipped} 条无效行。\n（点“保存”后生效）",
        )

    # --- About tab --------------------------------------------------------
    def _build_about_tab(self) -> QWidget:
        from .. import __version__ as app_version

        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(10)

        title = QLabel(f"voice-cmds {app_version}")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        v.addWidget(title)

        lic = QLabel(
            "本软件基于 MIT 协议开源：使用时必须保留署名，并邮件通知作者相关链接"
            "（erichuanp@gmail.com）；除此之外可自由使用、修改与分发。"
        )
        lic.setWordWrap(True)
        v.addWidget(lic)

        links = QLabel(
            '项目主页 / Releases：<a href="https://github.com/erichuanp/voice-cmds/releases">'
            'github.com/erichuanp/voice-cmds/releases</a><br/>'
            '作者主页：<a href="https://github.com/erichuanp">github.com/erichuanp</a><br/>'
            '提建议 / 讨论：<a href="mailto:erichuanp@gmail.com">erichuanp@gmail.com</a>'
        )
        links.setOpenExternalLinks(True)
        v.addWidget(links)

        row = QHBoxLayout()
        self.check_btn = QPushButton("检查更新")
        self.check_btn.clicked.connect(self._check_update)
        self.update_status = QLabel("")
        self.update_btn = QPushButton("更新到最新版本")
        self.update_btn.setEnabled(False)  # enabled when a hot update is available
        self.update_btn.hide()
        row.addWidget(self.check_btn)
        row.addWidget(self.update_status)
        row.addWidget(self.update_btn)
        row.addStretch(1)
        v.addLayout(row)
        v.addStretch(1)
        return w

    def _check_update(self) -> None:
        self.update_status.setText("正在检查…")
        self.update_status.setStyleSheet("color:#808080;")
        self.check_btn.setEnabled(False)

        def worker():
            try:
                self.update_checked.emit(fetch_latest_version(), "")
            except Exception as e:
                self.update_checked.emit("", str(e))

        threading.Thread(target=worker, daemon=True).start()

    @Slot(str, str)
    def _on_update_checked(self, latest: str, error: str) -> None:
        from .. import __version__ as cur

        self.check_btn.setEnabled(True)
        if error:
            self.update_status.setText("网络出错检查失败")
            self.update_status.setStyleSheet("color:#c62828;")
            self.update_btn.hide()
            return
        cmp = compare_versions(cur, latest)
        if cmp >= 0:
            self.update_status.setText("已经是最新版本")
            self.update_status.setStyleSheet("color:#00C853;")
            self.update_btn.hide()
            return
        if _parse_version(cur)[:1] < _parse_version(latest)[:1]:
            # Major version mismatch — too outdated for a hot update.
            self.update_status.setText("版本过时，请于GitHub Release下载最新版本")
            self.update_status.setStyleSheet("color:#c62828;")
            self.update_btn.hide()
            return
        self.update_status.setText(f"发现新版本 {latest}")
        self.update_status.setStyleSheet("color:#808080;")
        self.update_btn.setEnabled(True)
        self.update_btn.setToolTip("")
        self.update_btn.clicked.connect(self._start_update)
        self.update_btn.show()

    def _start_update(self) -> None:
        from ..config import PROJECT_ROOT
        from .. import updater

        self.update_btn.setEnabled(False)
        self.update_status.setText("正在比对差异文件…")
        self.update_status.setStyleSheet("color:#808080;")

        def worker():
            try:
                changed, deleted = updater.prepare_update(
                    PROJECT_ROOT,
                    status_cb=lambda s: self.update_progress.emit(s, -1),
                    progress_cb=lambda d, t: self.update_progress.emit(
                        "", max(0, min(99, round(d * 100 / max(t, 1))))
                    ),
                )
                self.update_prepared.emit(changed, deleted, "")
            except Exception as e:
                self.update_prepared.emit(0, 0, str(e))

        threading.Thread(target=worker, daemon=True).start()

    @Slot(str, int)
    def _on_update_progress(self, text: str, percent: int) -> None:
        if percent >= 0:
            self.update_status.setText(f"正在下载: {percent}%")
        else:
            self.update_status.setText(text)
        self.update_status.setStyleSheet("color:#808080;")

    @Slot(int, int, str)
    def _on_update_prepared(self, changed: int, deleted: int, error: str) -> None:
        if error:
            self.update_status.setText(f"更新失败：{error}（请手动下载）")
            self.update_status.setStyleSheet("color:#c62828;")
            self.update_btn.setEnabled(True)
            return
        self.update_status.setText("更新完成")
        self.update_status.setStyleSheet("color:#00C853;")
        QMessageBox.information(
            self, "voice-cmds", "更新已完成，程序将自动重启以应用更新。"
        )
        self.update_ready.emit()
        self.accept()

    # --- save -------------------------------------------------------------
    def _save(self) -> None:
        s = self.config.settings
        hotkeys = {
            "start": self.start_key.text().strip(),
            "stop": self.stop_key.text().strip(),
            "cancel": self.cancel_key.text().strip(),
        }
        # Validate the captured combos against the keyboard library.
        try:
            import keyboard as kb_mod
            for label, combo in hotkeys.items():
                if not combo:
                    QMessageBox.warning(self, "热键无效", f"{label} 热键为空。")
                    return
                kb_mod.parse_hotkey(combo)
        except Exception as e:
            QMessageBox.warning(self, "热键无效", f"热键格式无效：{e}")
            return
        s["hotkey"] = hotkeys
        s["stop_mode"] = self.stop_mode.currentData()
        s["vad_silence_ms"] = self.vad_ms.value()
        s.setdefault("ui", {})["result_text_ms"] = self.result_ms.value()

        apps, commands = [], []
        for i in range(self.cmd_list.count()):
            entry = self.cmd_list.item(i).data(0x100)
            if entry["kind"] == "app":
                apps.append({
                    "trigger": entry["trigger"],
                    "path": entry["path"],
                    "args": entry["args"],
                })
            else:
                commands.append({
                    "trigger": entry["trigger"],
                    "script": entry["script"],
                    "args": entry["args"],
                })
        self.config.apps = apps
        self.config.commands = commands

        self.config.save_settings()
        self.config.save_apps()
        self.config.save_commands()

        # Autostart — only touch the registry when not in --debug mode
        if not self.debug:
            try:
                from .. import autostart
                autostart.apply(self.autostart_check.isChecked())
            except Exception as e:
                QMessageBox.warning(
                    self, "开机自启动",
                    f"写入注册表失败：{e}\n其他设置已保存。",
                )

        self.config_changed.emit()
        self.accept()

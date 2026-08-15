"""Settings dialog: hotkeys, stop mode, custom commands CRUD, apps CRUD, sound toggles."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
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


class _CommandDialog(QDialog):
    """Add/edit one entry: either '打开<触发词>' (launch an app) or a plain
    '触发词' custom command (script or exe). App triggers may list several
    aliases separated by ';' or '；' — 打开A and 打开B then open the same thing."""

    def __init__(self, parent=None, entry: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自定义命令")
        entry = entry or {}
        self.kind = entry.get("kind", "custom")  # 'app' | 'custom'

        self.radio_app = QRadioButton("打开<触发词>")
        self.radio_cmd = QRadioButton("触发词")
        self.radio_app.setChecked(self.kind == "app")
        self.radio_cmd.setChecked(self.kind != "app")

        self.trigger = QLineEdit(entry.get("trigger", ""))
        self.trigger.setToolTip("“打开”模式支持用 ; 或 ；分隔多个触发词：打开A / 打开B 都打开同一个")
        self.path = QLineEdit(entry.get("path", entry.get("script", "")))
        self.args = QLineEdit(" ".join(entry.get("args", []) or []))
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)

        kind_row = QHBoxLayout()
        kind_row.addWidget(self.radio_app)
        kind_row.addWidget(self.radio_cmd)
        kind_row.addStretch(1)

        form = QFormLayout()
        form.addRow("类型", kind_row)
        form.addRow("触发词", self.trigger)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path)
        path_row.addWidget(browse)
        path_widget = QWidget()
        path_widget.setLayout(path_row)
        form.addRow("路径", path_widget)
        form.addRow("附加参数（空格分隔）", self.args)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        if self.radio_app.isChecked():
            path, _ = QFileDialog.getOpenFileName(
                self, "选择应用", "", "Executables (*.exe);;All files (*.*)"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择脚本或程序", "",
                "脚本与程序 (*.bat *.cmd *.ps1 *.py *.exe);;All files (*.*)",
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


class SettingsDialog(QDialog):
    config_changed = Signal()

    def __init__(self, config, debug: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("voice-cmds 设置")
        self.config = config
        self.debug = debug
        self.resize(560, 520)

        tabs = QTabWidget()
        tabs.addTab(self._build_general_tab(), "通用")
        tabs.addTab(self._build_commands_tab(), "自定义命令")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(buttons)

    # --- General tab ---
    def _build_general_tab(self) -> QWidget:
        s = self.config.settings
        w = QWidget()
        form = QFormLayout(w)
        self.start_key = QLineEdit(s["hotkey"]["start"])
        self.stop_key = QLineEdit(s["hotkey"]["stop"])
        self.cancel_key = QLineEdit(s["hotkey"]["cancel"])
        form.addRow("启动热键", self.start_key)
        form.addRow("停止热键 (录音中)", self.stop_key)
        form.addRow("取消热键 (录音中)", self.cancel_key)

        self.stop_mode = QComboBox()
        self.stop_mode.addItem("静音自动停止（说完停 0.5 秒执行）", "vad")
        self.stop_mode.addItem("热键停止（按停止键结束）", "hotkey")
        current = s.get("stop_mode", "vad")
        idx = self.stop_mode.findData(current)
        self.stop_mode.setCurrentIndex(max(0, idx))
        form.addRow("停止模式", self.stop_mode)

        self.vad_ms = QSpinBox()
        self.vad_ms.setRange(200, 5000)
        self.vad_ms.setSingleStep(100)
        self.vad_ms.setValue(s.get("vad_silence_ms", 500))
        self.vad_ms.setSuffix(" ms")
        form.addRow("VAD 静音时长", self.vad_ms)
        form.addRow(
            QLabel('<span style="color:#999;">VAD 模式下停止键 / Esc 依然有效，可随时手动结束。</span>')
        )

        self.result_ms = QSpinBox()
        self.result_ms.setRange(0, 10000)
        self.result_ms.setSingleStep(100)
        self.result_ms.setValue(int(s.get("ui", {}).get("result_text_ms", 1000)))
        self.result_ms.setSuffix(" ms")
        form.addRow("识别结果展示时长", self.result_ms)
        form.addRow(
            QLabel('<span style="color:#999;">识别文本停留多久后再执行命令（单位：毫秒，0 表示立即执行）。</span>')
        )

        self.max_chars = QSpinBox()
        self.max_chars.setRange(3, 50)
        self.max_chars.setValue(s["max_chars"])
        form.addRow("最长识别字符数", self.max_chars)

        self.shutdown_delay = QSpinBox()
        self.shutdown_delay.setRange(0, 300)
        self.shutdown_delay.setValue(s["shutdown_delay_seconds"])
        self.shutdown_delay.setSuffix(" s")
        form.addRow("关机/重启倒计时", self.shutdown_delay)

        self.sound_success = QCheckBox("启用成功提示音")
        self.sound_success.setChecked(s["sound"]["success_enabled"])
        self.sound_error = QCheckBox("启用失败提示音")
        self.sound_error.setChecked(s["sound"]["error_enabled"])
        form.addRow("", self.sound_success)
        form.addRow("", self.sound_error)

        # Autostart
        from .. import autostart
        self.autostart_check = QCheckBox("开机自启动")
        self.autostart_check.setChecked(autostart.is_enabled())
        if self.debug:
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("--debug 模式下不会修改开机启动项")
        form.addRow("", self.autostart_check)
        if self.debug:
            form.addRow(QLabel('<span style="color:#999;">debug 模式：开机自启动项被锁定</span>'))

        form.addRow(QLabel("<i>保存后程序会自动重启以应用更改。</i>"))
        return w

    # --- Commands tab (merged: '打开 X' apps + plain custom commands) ---
    def _build_commands_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.cmd_list = QListWidget()
        for entry in self.config.apps:
            self._add_cmd_item({**entry, "kind": "app"})
        for entry in self.config.commands:
            self._add_cmd_item({**entry, "kind": "custom"})
        btns = QHBoxLayout()
        add = QPushButton("添加")
        edit = QPushButton("编辑")
        rm = QPushButton("删除")
        add.clicked.connect(self._add_cmd)
        edit.clicked.connect(self._edit_cmd)
        rm.clicked.connect(self._remove_cmd)
        btns.addWidget(add)
        btns.addWidget(edit)
        btns.addWidget(rm)
        btns.addStretch(1)
        layout.addWidget(self.cmd_list)
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

    # --- save ---
    def _save(self) -> None:
        s = self.config.settings
        s["hotkey"]["start"] = self.start_key.text().strip()
        s["hotkey"]["stop"] = self.stop_key.text().strip()
        s["hotkey"]["cancel"] = self.cancel_key.text().strip()
        s["stop_mode"] = self.stop_mode.currentData()
        s["vad_silence_ms"] = self.vad_ms.value()
        s.setdefault("ui", {})["result_text_ms"] = self.result_ms.value()
        s["max_chars"] = self.max_chars.value()
        s["shutdown_delay_seconds"] = self.shutdown_delay.value()
        s["sound"]["success_enabled"] = self.sound_success.isChecked()
        s["sound"]["error_enabled"] = self.sound_error.isChecked()

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

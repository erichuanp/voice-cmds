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
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class _AppDialog(QDialog):
    def __init__(self, parent=None, entry: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("打开 — 应用条目")
        entry = entry or {}
        self.trigger = QLineEdit(entry.get("trigger", ""))
        self.path = QLineEdit(entry.get("path", ""))
        self.args = QLineEdit(" ".join(entry.get("args", []) or []))
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)

        form = QFormLayout()
        form.addRow("触发词", self.trigger)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path)
        path_row.addWidget(browse)
        path_widget = QWidget()
        path_widget.setLayout(path_row)
        form.addRow("可执行路径", path_widget)
        form.addRow("附加参数（空格分隔）", self.args)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择应用", "", "Executables (*.exe);;All files (*.*)")
        if path:
            self.path.setText(path)

    def value(self) -> dict:
        args = [a for a in self.args.text().split() if a]
        return {"trigger": self.trigger.text().strip(), "path": self.path.text().strip(), "args": args}


class _CommandDialog(QDialog):
    def __init__(self, parent=None, entry: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("自定义命令")
        entry = entry or {}
        self.trigger = QLineEdit(entry.get("trigger", ""))
        self.script = QLineEdit(entry.get("script", ""))
        self.args = QLineEdit(" ".join(entry.get("args", []) or []))
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)

        form = QFormLayout()
        form.addRow("触发词", self.trigger)
        path_row = QHBoxLayout()
        path_row.addWidget(self.script)
        path_row.addWidget(browse)
        path_widget = QWidget()
        path_widget.setLayout(path_row)
        form.addRow("脚本路径（相对项目根）", path_widget)
        form.addRow("附加参数（空格分隔）", self.args)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择脚本", "", "Scripts (*.bat *.cmd *.ps1 *.py);;All files (*.*)"
        )
        if path:
            self.script.setText(path)

    def value(self) -> dict:
        args = [a for a in self.args.text().split() if a]
        return {"trigger": self.trigger.text().strip(), "script": self.script.text().strip(), "args": args}


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
        tabs.addTab(self._build_apps_tab(), "打开 (Apps)")
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
        self.stop_mode.addItems(["hotkey"])  # "vad" not implemented yet
        self.stop_mode.setCurrentText("hotkey")
        self.stop_mode.setEnabled(False)
        self.stop_mode.setToolTip("VAD 自动停止暂未实现，目前仅支持热键停止")
        form.addRow("停止模式", self.stop_mode)

        self.vad_ms = QSpinBox()
        self.vad_ms.setRange(200, 5000)
        self.vad_ms.setSingleStep(100)
        self.vad_ms.setValue(s["vad_silence_ms"])
        self.vad_ms.setSuffix(" ms")
        self.vad_ms.setEnabled(False)
        form.addRow("VAD 静音阈值", self.vad_ms)

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

    # --- Apps tab ---
    def _build_apps_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.app_list = QListWidget()
        for entry in self.config.apps:
            self._add_app_item(entry)
        btns = QHBoxLayout()
        add = QPushButton("添加新的打开")
        edit = QPushButton("编辑")
        rm = QPushButton("删除")
        add.clicked.connect(self._add_app)
        edit.clicked.connect(self._edit_app)
        rm.clicked.connect(self._remove_app)
        btns.addWidget(add)
        btns.addWidget(edit)
        btns.addWidget(rm)
        btns.addStretch(1)
        layout.addWidget(self.app_list)
        layout.addLayout(btns)
        return w

    def _add_app_item(self, entry: dict) -> None:
        item = QListWidgetItem(f"{entry['trigger']}  →  {entry['path']}")
        item.setData(0x100, entry)  # Qt.UserRole == 0x100
        self.app_list.addItem(item)

    def _add_app(self) -> None:
        d = _AppDialog(self)
        if d.exec() == QDialog.Accepted:
            v = d.value()
            if not v["trigger"] or not v["path"]:
                QMessageBox.warning(self, "无效", "触发词和路径都必填。")
                return
            self._add_app_item(v)

    def _edit_app(self) -> None:
        item = self.app_list.currentItem()
        if not item:
            return
        d = _AppDialog(self, entry=item.data(0x100))
        if d.exec() == QDialog.Accepted:
            v = d.value()
            item.setText(f"{v['trigger']}  →  {v['path']}")
            item.setData(0x100, v)

    def _remove_app(self) -> None:
        row = self.app_list.currentRow()
        if row >= 0:
            self.app_list.takeItem(row)

    # --- Commands tab ---
    def _build_commands_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.cmd_list = QListWidget()
        for entry in self.config.commands:
            self._add_cmd_item(entry)
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

    def _add_cmd_item(self, entry: dict) -> None:
        item = QListWidgetItem(f"{entry['trigger']}  →  {entry['script']}")
        item.setData(0x100, entry)
        self.cmd_list.addItem(item)

    def _add_cmd(self) -> None:
        d = _CommandDialog(self)
        if d.exec() == QDialog.Accepted:
            v = d.value()
            if not v["trigger"] or not v["script"]:
                QMessageBox.warning(self, "无效", "触发词和脚本路径都必填。")
                return
            self._add_cmd_item(v)

    def _edit_cmd(self) -> None:
        item = self.cmd_list.currentItem()
        if not item:
            return
        d = _CommandDialog(self, entry=item.data(0x100))
        if d.exec() == QDialog.Accepted:
            v = d.value()
            item.setText(f"{v['trigger']}  →  {v['script']}")
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
        s["stop_mode"] = self.stop_mode.currentText()
        s["vad_silence_ms"] = self.vad_ms.value()
        s["max_chars"] = self.max_chars.value()
        s["shutdown_delay_seconds"] = self.shutdown_delay.value()
        s["sound"]["success_enabled"] = self.sound_success.isChecked()
        s["sound"]["error_enabled"] = self.sound_error.isChecked()

        self.config.apps = [
            self.app_list.item(i).data(0x100) for i in range(self.app_list.count())
        ]
        self.config.commands = [
            self.cmd_list.item(i).data(0x100) for i in range(self.cmd_list.count())
        ]

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

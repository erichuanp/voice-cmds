"""System tray icon with menu: Settings, Reload Config, Exit."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QBrush
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


def _generate_icon(color: str = "#00C853") -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(QColor(0, 0, 0, 0))
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(QColor(0, 0, 0, 0))
    p.drawEllipse(8, 8, 48, 48)
    p.end()
    return QIcon(pix)


class TrayIcon(QObject):
    settings_requested = Signal()
    reload_requested = Signal()
    exit_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.tray = QSystemTrayIcon(_generate_icon())
        self.tray.setToolTip("voice-cmds")
        menu = QMenu()
        act_settings = QAction("设置", menu)
        act_reload = QAction("重新加载配置", menu)
        act_exit = QAction("退出", menu)
        act_settings.triggered.connect(self.settings_requested.emit)
        act_reload.triggered.connect(self.reload_requested.emit)
        act_exit.triggered.connect(self.exit_requested.emit)
        menu.addAction(act_settings)
        menu.addAction(act_reload)
        menu.addSeparator()
        menu.addAction(act_exit)
        self.tray.setContextMenu(menu)
        self.tray.show()

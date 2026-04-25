"""Startup splash window: shows config/model load + STT download progress."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)


class SplashWindow(QWidget):
    """Frameless centered splash with title, status text, and progress bar.

    Signals are thread-safe; worker threads can call set_status / set_progress
    via Qt's queued connections.
    """

    status_signal = Signal(str)
    progress_signal = Signal(int, int)  # current, total (0,0 = indeterminate)

    def __init__(self) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(460, 180)

        title = QLabel("voice-cmds")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("color: white; font-size: 22px; font-weight: 600;")

        self.status_label = QLabel("正在启动…")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 13px;")

        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # start indeterminate
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setStyleSheet(
            """
            QProgressBar {
                background-color: rgba(255,255,255,0.18);
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background-color: #00C853;
                border-radius: 3px;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 36, 40, 36)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addStretch(1)
        layout.addWidget(self.status_label)
        layout.addWidget(self.bar)
        layout.addStretch(1)

        self.status_signal.connect(self._on_status, Qt.QueuedConnection)
        self.progress_signal.connect(self._on_progress, Qt.QueuedConnection)

        self._center_on_screen()

    def _center_on_screen(self) -> None:
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(
            geo.left() + (geo.width() - self.width()) // 2,
            geo.top() + (geo.height() - self.height()) // 2,
        )

    @Slot(str)
    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)

    @Slot(int, int)
    def _on_progress(self, current: int, total: int) -> None:
        if total <= 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, total)
            self.bar.setValue(current)

    # Convenience for worker threads
    def set_status(self, text: str) -> None:
        self.status_signal.emit(text)

    def set_progress(self, current: int, total: int) -> None:
        self.progress_signal.emit(current, total)

    def set_indeterminate(self) -> None:
        self.progress_signal.emit(0, 0)

    # Painted background (rounded dark surface)
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 14, 14)
        p.fillPath(path, QColor(28, 30, 34, 235))

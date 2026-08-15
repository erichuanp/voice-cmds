"""Help dialog: objective, always-current command reference (built from config)."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .common import DIALOG_STYLE


class HelpDialog(QDialog):
    def __init__(self, html: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("帮助")
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(520, 560)

        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(html)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self.browser, 1)
        layout.addLayout(btn_row)

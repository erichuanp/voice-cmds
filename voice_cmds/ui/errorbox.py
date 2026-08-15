"""Unified error dialog: category + guidance + expandable copyable details."""
from __future__ import annotations

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .common import DIALOG_STYLE, hint


class ErrorDialog(QDialog):
    def __init__(
        self,
        title: str,
        category: str,
        guidance: str,
        detail: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(DIALOG_STYLE)
        self.resize(480, 220)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: 700; color: #c62828;")

        cat_lbl = QLabel(f"错误分类：{category}")
        cat_lbl.setStyleSheet("font-weight: 600; color: #2b2b2b;")
        guide_lbl = hint(guidance)

        self._details = QPlainTextEdit()
        self._details.setReadOnly(True)
        self._details.setPlainText(detail)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self._details.setFont(mono)
        self._details.setVisible(False)

        self._toggle_btn = QPushButton("详细信息 ▾")
        self._toggle_btn.setCheckable(True)
        self._toggle_btn.toggled.connect(self._on_toggle)
        copy_btn = QPushButton("复制")
        copy_btn.clicked.connect(self._copy)
        close_btn = QPushButton("关闭")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.close)

        row = QHBoxLayout()
        row.addWidget(self._toggle_btn)
        row.addWidget(copy_btn)
        row.addStretch(1)
        row.addWidget(close_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(8)
        layout.addWidget(title_lbl)
        layout.addWidget(cat_lbl)
        layout.addWidget(guide_lbl)
        layout.addWidget(self._details, 1)
        layout.addLayout(row)

        if not detail.strip():
            self._toggle_btn.setEnabled(False)

    def _on_toggle(self, checked: bool) -> None:
        self._details.setVisible(checked)
        self._toggle_btn.setText("收起详细信息 ▴" if checked else "详细信息 ▾")
        self.adjustSize()

    def _copy(self) -> None:
        QApplication.clipboard().setText(self._details.toPlainText())

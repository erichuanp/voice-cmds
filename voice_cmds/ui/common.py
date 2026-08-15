"""Shared UI helpers: consistent dialog styling and hint labels."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel

# Light, neutral style applied to every dialog so settings / help / tasks
# look consistent (margins are set per-layout; this only handles colors,
# borders and control padding).
DIALOG_STYLE = """
QDialog { background: #ffffff; }
QLabel { color: #2b2b2b; }
QGroupBox {
    border: 1px solid #d8d8d8;
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: #404040;
}
QPushButton { min-width: 76px; padding: 4px 14px; }
QLineEdit, QSpinBox, QComboBox, QDateTimeEdit { min-height: 22px; }
QListWidget, QTreeWidget, QTextBrowser {
    border: 1px solid #d8d8d8;
    border-radius: 4px;
    background: #ffffff;
}
"""

HINT_STYLE = "color:#808080; font-size:11px;"


def hint(text: str) -> QLabel:
    """Small gray secondary-text label (replaces subjective inline prose)."""
    lbl = QLabel(text)
    lbl.setStyleSheet(HINT_STYLE)
    lbl.setWordWrap(True)
    return lbl

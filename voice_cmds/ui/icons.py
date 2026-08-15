"""Runtime app icon for window title bars.

Faithfully reproduces the exact logo baked into assets/app.ico (green
circle + white microphone), so dialog title bars show the same icon as
the exe / installer instead of the Windows default. Nothing else uses
this module — the tray icon keeps its own green-dot drawing.
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap

GREEN = QColor(0, 200, 83)
WHITE = QColor(255, 255, 255)


def draw_app_logo(p: QPainter, side: float) -> None:
    s = side
    m = max(1.0, s / 32.0)
    p.setPen(Qt.NoPen)

    p.setBrush(GREEN)
    p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))

    p.setBrush(WHITE)
    w = s * 0.4

    def _rr(x0: float, y0: float, x1: float, y1: float, r: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(QRectF(x0 * s, y0 * s, (x1 - x0) * s, (y1 - y0) * s), r, r)
        p.fillPath(path, p.brush())

    _rr(0.32, 0.24, 0.68, 0.72, w / 2)            # mic capsule body
    p.drawRect(QRectF(0.36 * s, 0.46 * s, 0.28 * s, 0.10 * s))  # cross bar
    _rr(0.30, 0.50, 0.70, 0.70, w / 2)            # cradle
    p.drawRect(QRectF(0.47 * s, 0.66 * s, 0.06 * s, 0.20 * s))  # stem
    _rr(0.42, 0.84, 0.58, 0.92, 0.04 * s)         # base


def build_app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        draw_app_logo(p, float(size))
        p.end()
        icon.addPixmap(pm)
    return icon

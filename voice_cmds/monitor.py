"""Focused monitor detection — finds the work area of the monitor under the cursor.

Uses Qt's QGuiApplication / QScreen so coordinates are in *logical* pixels,
matching what QWidget.move() expects. Win32's GetCursorPos / GetMonitorInfo
return *physical* pixels, which on HiDPI screens (125%/150% scaling) puts
windows off-screen. Stay in Qt's coordinate space end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QCursor, QGuiApplication


@dataclass
class WorkArea:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def get_focused_work_area() -> WorkArea:
    """Return the work area (excludes taskbar) of the monitor under the cursor.

    Coordinates are in Qt logical pixels. `right`/`bottom` are exclusive
    (i.e., right = left + width), matching Win32 RECT semantics rather than
    QRect's inclusive right/bottom.
    """
    cursor = QCursor.pos()
    screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
    geom = screen.availableGeometry()
    return WorkArea(
        left=geom.left(),
        top=geom.top(),
        right=geom.left() + geom.width(),
        bottom=geom.top() + geom.height(),
    )

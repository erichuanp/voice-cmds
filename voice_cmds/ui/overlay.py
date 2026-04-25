"""Floating recording overlay: circle → capsule → processing → success/error.

Frameless, translucent, non-activating. Manually painted soft shadow simulates
Win11 elevation without QGraphicsDropShadowEffect (which causes
UpdateLayeredWindowIndirect bleed errors on translucent frameless windows).

Animations are driven by a QTimer pegged to the display refresh rate
(QScreen.refreshRate) so they stay smooth on 144/240 Hz monitors.

Win11's DWM otherwise stamps a 1px border + rounds corners on frameless
windows; we strip both via DwmSetWindowAttribute in showEvent.
"""
from __future__ import annotations

import ctypes
import logging
from enum import Enum, auto
from typing import Callable, Optional

from PySide6.QtCore import (
    QElapsedTimer,
    QObject,
    QRect,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from ..monitor import get_focused_work_area

logger = logging.getLogger("voice_cmds.overlay")


class State(Enum):
    HIDDEN = auto()
    RECORDING = auto()
    PROCESSING = auto()
    SUCCESS = auto()
    ERROR = auto()


class _ScreenRateAnimator(QObject):
    """Drives a value 0→1 over `duration_ms` at the display's refresh rate.

    Calls `on_frame(t)` each tick (t in [0, 1]); `on_done()` once at t=1.
    """

    def __init__(self, parent: QObject, fps: float) -> None:
        super().__init__(parent)
        rate = max(30.0, fps)
        self.interval_ms = max(4, int(round(1000.0 / rate)))
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(self.interval_ms)
        self._timer.timeout.connect(self._tick)
        self._elapsed = QElapsedTimer()
        self._on_frame: Optional[Callable[[float], None]] = None
        self._on_done: Optional[Callable[[], None]] = None
        self._duration_ms: int = 1

    def start(
        self,
        duration_ms: int,
        on_frame: Callable[[float], None],
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        self._duration_ms = max(1, int(duration_ms))
        self._on_frame = on_frame
        self._on_done = on_done
        self._elapsed.restart()
        if not self._timer.isActive():
            self._timer.start()
        self._tick()

    def stop(self) -> None:
        self._timer.stop()
        self._on_frame = None
        self._on_done = None

    def _tick(self) -> None:
        if self._on_frame is None:
            self._timer.stop()
            return
        t = min(1.0, self._elapsed.elapsed() / self._duration_ms)
        self._on_frame(t)
        if t >= 1.0:
            done = self._on_done
            self._timer.stop()
            self._on_frame = None
            self._on_done = None
            if done:
                done()


class OverlayWindow(QWidget):
    def __init__(self, settings: dict) -> None:
        # Pre-init backing fields BEFORE super().__init__ so any Qt event
        # firing during widget setup can read them via the Property binding.
        ui_s = settings["ui"]
        self.diameter: int = int(ui_s["circle_diameter_px"])
        self.max_width: int = int(ui_s["max_capsule_width_px"])
        self.bottom_offset: int = int(ui_s["bottom_offset_px"])
        self.shadow_margin: int = int(ui_s.get("shadow_margin_px", 16))
        self.font_pt: int = int(ui_s.get("font_size_pt", 13))
        self.color_idle = QColor(ui_s["color_idle"])
        self.color_error = QColor(ui_s["color_error"])
        self._capsule_width: int = self.diameter
        self._state: State = State.HIDDEN
        self._text: str = ""
        self._spinner_angle_deg: float = 0.0

        super().__init__(None)
        self.s = settings

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        # Detect display refresh rate for buttery animations
        screen = QGuiApplication.primaryScreen()
        rate = screen.refreshRate() if screen else 60.0
        if rate < 30:
            rate = 60.0
        self._refresh_hz = rate
        self._anim = _ScreenRateAnimator(self, fps=rate)

        # Spinner uses a separate timer + elapsed clock so angular speed is
        # constant across refresh rates.
        self._spinner_elapsed = QElapsedTimer()
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setTimerType(Qt.PreciseTimer)
        self._spinner_timer.setInterval(self._anim.interval_ms)
        self._spinner_timer.timeout.connect(self._tick_spinner)

        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide_overlay)

        side = self.diameter + 2 * self.shadow_margin
        self.resize(side, side)
        logger.warning(
            "Overlay init: diameter=%d shadow=%d max_w=%d refresh=%.1fHz",
            self.diameter, self.shadow_margin, self.max_width, self._refresh_hz,
        )

    # --- Win11 chrome stripping ---
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._strip_win11_chrome()

    def _strip_win11_chrome(self) -> None:
        """Disable Win11's DWM border + corner rounding on this window."""
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            # DWMWA_BORDER_COLOR = 34, DWMWA_COLOR_NONE = 0xFFFFFFFE
            color = ctypes.c_uint(0xFFFFFFFE)
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(color), 4)
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_DONOTROUND = 1
            corner = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
        except Exception as e:
            logger.warning("DwmSetWindowAttribute failed: %s", e)

    # --- positioning ---
    def _reposition(self) -> None:
        try:
            wa = get_focused_work_area()
        except Exception:
            logger.exception("get_focused_work_area failed")
            return
        w = self._capsule_width + 2 * self.shadow_margin
        h = self.diameter + 2 * self.shadow_margin
        self.resize(w, h)
        x = (wa.left + wa.right) // 2 - w // 2
        y = wa.bottom - h - self.bottom_offset
        self.move(x, y)

    # --- public state transitions ---
    def show_recording(self) -> None:
        self._state = State.RECORDING
        self._text = ""
        self._anim.stop()
        self._spinner_timer.stop()
        self._auto_hide_timer.stop()
        self._capsule_width = self.diameter
        self._reposition()
        self.show()
        self.raise_()
        self.update()

    def update_text(self, text: str) -> None:
        if self._state != State.RECORDING:
            return
        self._text = text
        self._animate_width(self._target_capsule_width(text))

    def show_processing(self) -> None:
        self._state = State.PROCESSING
        self._spinner_angle_deg = 0.0
        self._spinner_elapsed.restart()
        if not self._spinner_timer.isActive():
            self._spinner_timer.start()
        self.update()

    def show_success(self) -> None:
        self._state = State.SUCCESS
        self._spinner_timer.stop()
        self._text = ""
        self._animate_width(self.diameter)
        self.update()
        self._auto_hide_timer.start(2000)

    def show_error(self) -> None:
        self._state = State.ERROR
        self._spinner_timer.stop()
        self._text = ""
        self._animate_width(self.diameter)
        self.update()
        self._auto_hide_timer.start(2000)

    def hide_overlay(self) -> None:
        self._state = State.HIDDEN
        self._spinner_timer.stop()
        self._auto_hide_timer.stop()
        self._anim.stop()
        self.hide()

    # --- animation helpers ---
    def _animate_width(self, target: int) -> None:
        target = max(self.diameter, min(int(target), self.max_width))
        start = self._capsule_width
        if start == target:
            return
        delta = target - start

        def on_frame(t: float) -> None:
            eased = 1.0 - (1.0 - t) ** 3  # OutCubic
            self._capsule_width = int(round(start + delta * eased))
            self._reposition()
            self.update()

        self._anim.start(180, on_frame)

    def _target_capsule_width(self, text: str) -> int:
        if not text:
            return self.diameter
        font = QFont("Microsoft YaHei UI", self.font_pt)
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(text)
        return min(self.diameter + text_w + 24, self.max_width)

    def _tick_spinner(self) -> None:
        # 360° per 1.2 s for an even, fast-but-not-frantic rotation
        self._spinner_angle_deg = (self._spinner_elapsed.elapsed() / 1200.0 * 360.0) % 360.0
        self.update()

    # --- painting ---
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        capsule_rect = QRectF(
            self.shadow_margin,
            self.shadow_margin,
            self._capsule_width,
            self.diameter,
        )

        # Stacked soft shadow — Gaussian-ish falloff, fades to invisible at outer edge
        margin = float(self.shadow_margin)
        shadow_layers = (
            (1.00, 2),   # outermost, ~0.8% black
            (0.72, 5),
            (0.50, 9),
            (0.32, 14),
            (0.18, 22),  # innermost, ~9% black at the surface
        )
        for frac, alpha in shadow_layers:
            ext = max(1.0, margin * frac)
            rect = QRectF(
                capsule_rect.x() - ext,
                capsule_rect.y() - ext + 2,  # slight downward bias = light from above
                capsule_rect.width() + 2 * ext,
                capsule_rect.height() + 2 * ext,
            )
            radius = self.diameter / 2.0 + ext
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.fillPath(path, QColor(0, 0, 0, alpha))

        # Capsule body
        color = self.color_error if self._state == State.ERROR else self.color_idle
        body = QPainterPath()
        body.addRoundedRect(capsule_rect, self.diameter / 2.0, self.diameter / 2.0)
        p.fillPath(body, color)

        # Foreground content
        if self._state == State.RECORDING and self._text:
            p.setPen(Qt.white)
            p.setFont(QFont("Microsoft YaHei UI", self.font_pt))
            text_rect = capsule_rect.adjusted(12, 0, -12, 0).toRect()
            p.drawText(text_rect, Qt.AlignCenter | Qt.TextWordWrap, self._text)
        elif self._state == State.PROCESSING:
            self._draw_spinner(p, capsule_rect)
        elif self._state == State.SUCCESS:
            self._draw_check(p, capsule_rect)
        elif self._state == State.ERROR:
            self._draw_cross(p, capsule_rect)

    def _draw_spinner(self, p: QPainter, rect: QRectF) -> None:
        cx = rect.center().x()
        cy = rect.center().y()
        r = self.diameter / 3.0
        pen = QPen(QColor(255, 255, 255, 230))
        pen.setWidthF(3.0)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        arc_rect = QRectF(cx - r, cy - r, 2 * r, 2 * r)
        # Qt's drawArc takes 1/16-degree units
        p.drawArc(arc_rect, int(self._spinner_angle_deg * 16), int(270 * 16))

    def _draw_check(self, p: QPainter, rect: QRectF) -> None:
        pen = QPen(Qt.white)
        pen.setWidthF(self.diameter * 0.08)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        cx = int(rect.center().x())
        cy = int(rect.center().y())
        s = max(4, self.diameter // 4)
        s23 = (s * 2) // 3
        s3 = max(1, s // 3)
        p.drawLine(cx - s, cy, cx - s3, cy + s23)
        p.drawLine(cx - s3, cy + s23, cx + s, cy - s23)

    def _draw_cross(self, p: QPainter, rect: QRectF) -> None:
        pen = QPen(Qt.white)
        pen.setWidthF(self.diameter * 0.08)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        cx = int(rect.center().x())
        cy = int(rect.center().y())
        s = max(4, self.diameter // 4)
        p.drawLine(cx - s, cy - s, cx + s, cy + s)
        p.drawLine(cx + s, cy - s, cx - s, cy + s)

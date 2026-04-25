"""Global hotkey registration via the `keyboard` library.

Distinguishes left/right modifiers. Stop and Cancel keys only fire when recording.
Emits Qt signals so the main thread handles UI updates.
"""
from __future__ import annotations

import logging
from typing import Callable

import keyboard
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("voice_cmds.hotkey")


class HotkeyManager(QObject):
    start_pressed = Signal()
    stop_pressed = Signal()
    cancel_pressed = Signal()

    def __init__(self, start_combo: str, stop_combo: str, cancel_combo: str) -> None:
        super().__init__()
        self.start_combo = start_combo
        self.stop_combo = stop_combo
        self.cancel_combo = cancel_combo
        self._recording = False
        self._handles: list[Callable] = []

    def set_recording(self, value: bool) -> None:
        self._recording = value

    def start(self) -> None:
        try:
            h1 = keyboard.add_hotkey(self.start_combo, self._on_start, suppress=False)
            h2 = keyboard.add_hotkey(self.stop_combo, self._on_stop, suppress=False)
            h3 = keyboard.add_hotkey(self.cancel_combo, self._on_cancel, suppress=False)
        except Exception as e:
            logger.error("Hotkey registration failed: %s", e)
            raise
        self._handles = [h1, h2, h3]
        logger.warning(  # WARNING so it shows without --debug
            "Hotkeys registered: start=%r  stop=%r (only while recording)  cancel=%r",
            self.start_combo, self.stop_combo, self.cancel_combo,
        )

    def stop(self) -> None:
        for h in self._handles:
            try:
                keyboard.remove_hotkey(h)
            except Exception:
                pass
        self._handles.clear()

    def _on_start(self) -> None:
        logger.warning("Hotkey: START key fired (recording=%s)", self._recording)
        if not self._recording:
            self.start_pressed.emit()

    def _on_stop(self) -> None:
        logger.warning("Hotkey: STOP key fired (recording=%s)", self._recording)
        if self._recording:
            self.stop_pressed.emit()

    def _on_cancel(self) -> None:
        logger.warning("Hotkey: CANCEL key fired (recording=%s)", self._recording)
        if self._recording:
            self.cancel_pressed.emit()

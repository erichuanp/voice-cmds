"""Hotkey capture widget: click the field, press key(s), done.

- Clicking the field shows the capture hint and starts recording.
- Two-key mode (开始录音): two presses, e.g. LeftCtrl then RightAlt →
  "left ctrl+right alt".
- Single-key mode (结束识别 / 取消): one press (modifier combos allowed).
- Mouse right button is supported ("right"); left button is excluded.
- Esc cancels (restores the previous value); Enter accepts early.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit

# Windows native scan codes (extended keys have 0x100 added).
_MODIFIER_BY_SCAN = {
    0x1D: "left ctrl",
    0x11D: "right ctrl",
    0x38: "left alt",
    0x138: "right alt",
    0x2A: "left shift",
    0x136: "right shift",
    0x15B: "left windows",
    0x15C: "right windows",
}

_SPECIAL_KEYS = {
    Qt.Key_Space: "space",
    Qt.Key_Enter: "enter",
    Qt.Key_Return: "enter",
    Qt.Key_Tab: "tab",
    Qt.Key_Backspace: "backspace",
    Qt.Key_Escape: "esc",
    Qt.Key_Insert: "insert",
    Qt.Key_Delete: "delete",
    Qt.Key_Home: "home",
    Qt.Key_End: "end",
    Qt.Key_PageUp: "page up",
    Qt.Key_PageDown: "page down",
    Qt.Key_Up: "up",
    Qt.Key_Down: "down",
    Qt.Key_Left: "left",
    Qt.Key_Right: "right",
    Qt.Key_CapsLock: "caps lock",
    Qt.Key_Print: "print screen",
    Qt.Key_Pause: "pause",
    Qt.Key_VolumeUp: "volume up",
    Qt.Key_VolumeDown: "volume down",
    Qt.Key_VolumeMute: "volume mute",
    Qt.Key_MediaPlay: "play/pause media",
    Qt.Key_MediaNext: "next track",
    Qt.Key_MediaPrevious: "previous track",
}


class HotkeyLineEdit(QLineEdit):
    """Read-only line edit that records a hotkey when focused."""

    value_changed = Signal(str)

    def __init__(
        self,
        value: str,
        two_keys: bool = False,
        hint: str = "请设置快捷键...",
        parent=None,
    ) -> None:
        super().__init__(value, parent)
        self._value = value
        self._two_keys = two_keys
        self._hint = hint
        self._capturing = False
        self._parts: list[str] = []
        self.setReadOnly(True)
        self.setPlaceholderText(hint)

    # --- capture state ----------------------------------------------------
    def focusInEvent(self, event) -> None:  # noqa: N802
        super().focusInEvent(event)
        self._begin_capture()

    def _begin_capture(self) -> None:
        self._capturing = True
        self._parts = []
        self.setText("")
        self.setPlaceholderText(self._hint)

    def focusOutEvent(self, event) -> None:  # noqa: N802
        super().focusOutEvent(event)
        if self._capturing and not self._parts:
            self._cancel_capture()

    def _cancel_capture(self) -> None:
        self._capturing = False
        self._parts = []
        self.setText(self._value)

    def _accept(self) -> None:
        combo = "+".join(self._parts)
        self._value = combo
        self._capturing = False
        self._parts = []
        self.setText(combo)
        self.value_changed.emit(combo)

    def reset_to_default(self, default: str) -> None:
        self._value = default
        self._capturing = False
        self._parts = []
        self.setText(default)
        self.value_changed.emit(default)

    # --- events -----------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self._capturing:
            super().keyPressEvent(event)
            return
        scan = int(event.nativeScanCode()) & 0x1FF
        self._process_key(scan, event.key(), event.modifiers())

    def _process_key(self, scan: int, key: int, modifiers) -> None:
        """Capture step, separated for testability (synthetic events lack
        native scan codes)."""
        if not self._capturing:
            return
        if key == Qt.Key_Escape:
            self._cancel_capture()
            return
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self._parts:
                self._accept()
            return
        name = self._key_name(scan, key, modifiers)
        if name is None:
            return
        if self._two_keys:
            if name not in self._parts:
                self._parts.append(name)
            if len(self._parts) >= 2:
                self._accept()
            else:
                self.setText("+".join(self._parts))
        else:
            self._parts = [name]
            self._accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self._capturing and event.button() == Qt.RightButton:
            if self._two_keys:
                if "right" not in self._parts:
                    self._parts.append("right")
                if len(self._parts) >= 2:
                    self._accept()
                else:
                    self.setText("+".join(self._parts))
            else:
                self._parts = ["right"]
                self._accept()
            return
        # Left button (and unmappable buttons) are ignored on purpose.
        super().mousePressEvent(event)

    @staticmethod
    def _key_name(scan: int, key: int, modifiers) -> str | None:
        """Map (scan code, Qt key, modifiers) to the keyboard-library name."""
        if scan in _MODIFIER_BY_SCAN:
            # A modifier key itself — left/right distinguishable by scan code.
            return _MODIFIER_BY_SCAN[scan]
        mods = []
        if modifiers & Qt.ControlModifier:
            mods.append("ctrl")
        if modifiers & Qt.AltModifier:
            mods.append("alt")
        if modifiers & Qt.ShiftModifier:
            mods.append("shift")
        if modifiers & Qt.MetaModifier:
            mods.append("windows")
        if Qt.Key_A <= key <= Qt.Key_Z:
            main = chr(ord("a") + key - Qt.Key_A)
        elif Qt.Key_0 <= key <= Qt.Key_9:
            main = chr(ord("0") + key - Qt.Key_0)
        elif Qt.Key_F1 <= key <= Qt.Key_F24:
            main = f"f{key - Qt.Key_F1 + 1}"
        else:
            main = _SPECIAL_KEYS.get(key)
        if main is None:
            return None
        return "+".join([*mods, main])

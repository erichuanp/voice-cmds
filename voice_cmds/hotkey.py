"""Global hotkeys: combo parsing + a platform-independent matcher facade.

Backends feed canonical KEY NAMES into the matcher:

- voice_cmds.hotkey_win — Win32 low-level keyboard+mouse hooks (Windows).
  Replaces the old `keyboard` package: on many real machines its left/right
  modifier tables were polluted ("right alt" matched the left alt scan code)
  and its state machine fired spurious events.
- voice_cmds.hotkey_mac — Quartz CGEventTap, listen-only (macOS; requires
  the Accessibility permission, same as any global-hotkey app).

Modifiers are plain `ctrl` / `alt` / `shift` / `windows` — either side
matches (`windows` is the Command key on macOS). Main keys use the same
names the capture widget produces ('q', 'f5', 'esc', 'space', 'volume up',
…). The right mouse button is a hotkey name too: "right".

Stop and Cancel only fire while recording. The matching itself is
platform-independent: the backends translate OS events to names and the
matcher tracks a down-set of names.
"""
from __future__ import annotations

import logging
import sys
import threading

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("voice_cmds.hotkey")

_MODIFIER_NAMES = ("ctrl", "alt", "shift", "windows", "win")

_KEY_NAME_TO_VK = {
    "esc": 0x1B,
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "backspace": 0x08,
    "insert": 0x2D,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "page up": 0x21,
    "page down": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "caps lock": 0x14,
    "print screen": 0x2C,
    "pause": 0x13,
    "volume up": 0xAF,
    "volume down": 0xAE,
    "volume mute": 0xAD,
    "play/pause media": 0xB3,
    "next track": 0xB0,
    "previous track": 0xB1,
}


def normalize_combo(combo: str) -> str:
    """'left ctrl+right alt' -> 'ctrl+alt' (left/right collapsed to plain)."""
    parts = []
    for p in combo.split("+"):
        p = p.strip().lower()
        for side in ("left ", "right "):
            if p.startswith(side):
                p = p[len(side):]
                break
        if p in ("cmd", "command"):
            p = "windows"  # macOS Command key
        parts.append(p)
    return "+".join(parts)


def parse_combo(combo: str) -> dict:
    """Split 'ctrl+alt' / 'ctrl+q' / 'esc' / 'right' into parts.

    Returns {'mods': set, 'main': str|None}. Raises ValueError for unknown
    key names so the settings dialog can validate captured hotkeys.
    """
    combo = normalize_combo(combo)
    mods: set[str] = set()
    main: str | None = None
    for p in (part for part in combo.split("+") if part.strip()):
        if p in _MODIFIER_NAMES:
            mods.add("windows" if p == "win" else p)
            continue
        if p == "right" or p in _KEY_NAME_TO_VK:
            main = p
            continue
        if len(p) == 1 and p.isalpha():
            main = p
            continue
        if len(p) == 1 and p.isdigit():
            main = p
            continue
        if p.startswith("f") and p[1:].isdigit() and 1 <= int(p[1:]) <= 24:
            main = p
            continue
        raise ValueError(f"未知按键: {p!r}")
    if not mods and main is None:
        raise ValueError("热键为空")
    return {"mods": mods, "main": main}


def _select_backend(manager: "HotkeyManager"):
    if sys.platform == "darwin":
        from . import hotkey_mac

        return hotkey_mac.Backend(manager)
    from . import hotkey_win

    return hotkey_win.Backend(manager)


class HotkeyManager(QObject):
    start_pressed = Signal()
    stop_pressed = Signal()
    cancel_pressed = Signal()

    def __init__(self, start_combo: str, stop_combo: str, cancel_combo: str) -> None:
        super().__init__()
        self.start_combo = normalize_combo(start_combo)
        self.stop_combo = normalize_combo(stop_combo)
        self.cancel_combo = normalize_combo(cancel_combo)
        self._start = parse_combo(self.start_combo)
        self._stop = parse_combo(self.stop_combo)
        self._cancel = parse_combo(self.cancel_combo)
        self._recording = False
        self._down: set[str] = set()
        # Physical key state: auto-repeat KEYDOWNs must not re-trigger a
        # hotkey after its completing key was consumed (holding ctrl+alt
        # past the OS repeat delay would otherwise fire stop instantly).
        self._physical_down: set[str] = set()
        self._thread: threading.Thread | None = None
        self._backend = _select_backend(self)

    def set_recording(self, value: bool) -> None:
        self._recording = value

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        thread = self._backend.create_thread()
        self._thread = thread
        thread.start()
        logger.warning(  # WARNING so it shows without --debug
            "Hotkeys registered: start=%r  stop=%r (only while recording)  cancel=%r",
            self.start_combo, self.stop_combo, self.cancel_combo,
        )

    def stop(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            try:
                thread.stop()
            except Exception:
                logger.debug("Hotkey backend stop failed", exc_info=True)

    # --- event feeds (called by backends on their hook/tap thread) ---------
    def _on_key_name(self, name: str, down: bool) -> None:
        if down:
            if name in self._physical_down:
                return  # OS auto-repeat — not a fresh press
            self._physical_down.add(name)
            self._down.add(name)
            self._evaluate(name)
        else:
            self._physical_down.discard(name)
            self._down.discard(name)

    def _on_mouse_right(self) -> None:
        if self._recording:
            if self._stop.get("main") == "right" and self._mods_down(self._stop):
                self._fire_stop()
            elif self._cancel.get("main") == "right" and self._mods_down(self._cancel):
                self._fire_cancel()

    def _evaluate(self, name: str) -> None:
        if not self._recording:
            if self._combo_complete(self._start, name):
                self._consume(self._start, name)
                self._recording = True
                self.start_pressed.emit()
        else:
            if self._combo_complete(self._stop, name):
                self._consume(self._stop, name)
                self._fire_stop()
                return
            if self._combo_complete(self._cancel, name):
                self._consume(self._cancel, name)
                self._fire_cancel()

    def _fire_stop(self) -> None:
        self._recording = False
        self._down.clear()
        self.stop_pressed.emit()

    def _fire_cancel(self) -> None:
        self._recording = False
        self._down.clear()
        self.cancel_pressed.emit()
    def _mods_down(self, combo: dict) -> bool:
        return all(m in self._down for m in combo["mods"])

    def _combo_complete(self, combo: dict, name: str) -> bool:
        if not self._mods_down(combo):
            return False
        main = combo["main"]
        if main is None:
            # Modifier-only combo (e.g. ctrl+alt): complete when the just
            # pressed key is one of the combo's modifiers.
            return name in combo["mods"]
        if main == "right":
            return False  # mouse events come via _on_mouse_right
        return name == main

    def _consume(self, combo: dict, name: str) -> None:
        """Remove the completing key from _down so the same physical press
        cannot also complete another hotkey (e.g. start's alt also matching
        the stop hotkey) — but held modifiers stay available for a repeat."""
        self._down.discard(name)

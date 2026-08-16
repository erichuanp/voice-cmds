"""macOS hotkey backend: a listen-only Quartz CGEventTap on a CFRunLoop thread.

Translates CoreGraphics keyboard/mouse events to the canonical key names
used by the platform-independent matcher in voice_cmds.hotkey (Command →
"windows"). Requires the Accessibility permission — a listen-only session
event tap is refused otherwise; the app surfaces that as a friendly error.

Keycode table follows the standard ANSI layout. Letters/digits/function
keys are layout-dependent on macOS by nature; ANSI covers the vast
majority of users (same limitation every global-hotkey app accepts).
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger("voice_cmds.hotkey_mac")

# macOS virtual keycodes (ANSI layout) -> canonical names.
_KEYCODE_TO_NAME = {
    0: "a", 1: "s", 2: "d", 3: "f", 4: "h", 5: "g", 6: "z", 7: "x", 8: "c",
    9: "v", 11: "b", 12: "q", 13: "w", 14: "e", 15: "r", 16: "y", 17: "t",
    31: "o", 32: "u", 34: "i", 35: "p", 37: "l", 38: "j", 40: "k", 45: "n",
    46: "m",
    18: "1", 19: "2", 20: "3", 21: "4", 23: "5", 22: "6", 26: "7", 28: "8",
    25: "9", 29: "0",
    122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7",
    100: "f8", 101: "f9", 109: "f10", 103: "f11", 111: "f12", 105: "f13",
    107: "f14", 113: "f15", 106: "f16", 64: "f17", 79: "f18", 80: "f19",
    90: "f20",
    49: "space", 36: "enter", 76: "enter", 48: "tab", 51: "backspace",
    53: "esc", 114: "insert", 117: "delete", 115: "home", 119: "end",
    116: "page up", 121: "page down", 126: "up", 125: "down", 123: "left",
    124: "right", 57: "caps lock",
    72: "volume up", 73: "volume down", 74: "volume mute",
    16: "play/pause media", 17: "next track", 18: "previous track",
    59: "ctrl", 62: "ctrl", 58: "alt", 61: "alt",
    56: "shift", 60: "shift", 55: "windows", 54: "windows",
}


def preflight_access() -> bool:
    """True if the app may listen to global key events (macOS 10.15+)."""
    try:
        import Quartz

        return bool(Quartz.CGPreflightListenEventAccess())
    except Exception:  # pragma: no cover - older macOS / pyobjc mismatch
        return True


def request_access() -> None:
    """Trigger the system Accessibility prompt (macOS 10.15+)."""
    try:
        import Quartz

        Quartz.CGRequestListenEventAccess()
    except Exception:  # pragma: no cover
        pass


class _MacTapThread(threading.Thread):
    """Owns the event tap + CFRunLoop; feeds key names to the manager."""

    def __init__(self, manager) -> None:
        super().__init__(name="voice-cmds-tap", daemon=True)
        self._manager = manager
        self._tap = None
        self._run_source = None
        self._loop = None
        self._cb = None

    def run(self) -> None:
        import Quartz

        tap_ref = {}

        def cb(proxy, etype, event, refcon):
            tap = tap_ref.get("tap")
            if etype in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                if tap is not None:
                    Quartz.CGEventTapEnable(tap, True)
                return event
            if etype == Quartz.kCGEventKeyDown:
                code = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode
                )
                name = _KEYCODE_TO_NAME.get(int(code))
                if name:
                    self._manager._on_key_name(name, True)
            elif etype == Quartz.kCGEventKeyUp:
                code = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventKeycode
                )
                name = _KEYCODE_TO_NAME.get(int(code))
                if name:
                    self._manager._on_key_name(name, False)
            elif etype == Quartz.kCGEventRightMouseDown:
                self._manager._on_mouse_right()
            return event

        self._cb = cb
        mask = (
            (1 << Quartz.kCGEventKeyDown)
            | (1 << Quartz.kCGEventKeyUp)
            | (1 << Quartz.kCGEventRightMouseDown)
        )
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            cb,
            None,
        )
        if not tap:
            raise RuntimeError(
                "无法注册全局热键：macOS 需要在 系统设置 → 隐私与安全性 → 辅助功能 "
                "中勾选 voice-cmds（首次运行会弹出提示），勾选后请重启本应用。"
            )
        tap_ref["tap"] = tap
        self._tap = tap
        self._run_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        self._loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(
            self._loop, self._run_source, Quartz.kCFRunLoopDefaultMode
        )
        Quartz.CGEventTapEnable(tap, True)
        logger.debug("CGEventTap installed (accessibility ok)")
        Quartz.CFRunLoopRun()
        # --- shutdown ---
        Quartz.CGEventTapEnable(tap, False)
        logger.debug("CGEventTap removed")

    def stop(self) -> None:
        import Quartz

        if self._tap is not None:
            try:
                Quartz.CGEventTapEnable(self._tap, False)
            except Exception:
                pass
        if self._loop is not None:
            try:
                Quartz.CFRunLoopStop(self._loop)
                Quartz.CFRunLoopWakeUp(self._loop)
            except Exception:
                pass


class Backend:
    """macOS backend factory for voice_cmds.hotkey.HotkeyManager."""

    def __init__(self, manager) -> None:
        self._manager = manager

    def create_thread(self) -> threading.Thread:
        # Raise on the caller's thread (start() is wrapped in a try/except
        # that shows the friendly error dialog) instead of dying silently
        # inside the background thread.
        if not preflight_access():
            request_access()
            raise RuntimeError(
                "无法注册全局热键：macOS 需要在 系统设置 → 隐私与安全性 → 辅助功能 "
                "中勾选 voice-cmds（首次运行会弹出提示），勾选后请重启本应用。"
            )
        return _MacTapThread(self._manager)

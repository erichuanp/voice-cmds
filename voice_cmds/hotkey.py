"""Global hotkey handling via a raw Win32 low-level keyboard + mouse hook.

Why not the `keyboard` package: on many real machines its left/right
modifier tables are polluted ("right alt" matches the left alt scan code
and vice versa) and its hotkey state machine fires spurious events (a lone
alt press completing a "left ctrl+right alt" combo was reproduced). A
direct WH_KEYBOARD_LL hook with exact scan codes is deterministic.

Modifiers are plain `ctrl` / `alt` / `shift` / `windows` — either side
matches. Stop and Cancel only fire while recording. The right mouse button
is supported as a hotkey ("right") via WH_MOUSE_LL.

The hook runs on a dedicated thread with its own GetMessage loop (a
low-level hook requires a message pump); callbacks emit Qt signals, which
queue onto the main thread.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("voice_cmds.hotkey")

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_RBUTTONDOWN = 0x0204
WM_QUIT = 0x0012
LLKHF_EXTENDED = 0x01

_CTRL_SCANS = {0x1D, 0x11D}
_ALT_SCANS = {0x38, 0x138}
_SHIFT_SCANS = {0x2A, 0x136}
_WIN_SCANS = {0x15B, 0x15C}

_MODIFIER_SCANS = {
    "ctrl": _CTRL_SCANS,
    "alt": _ALT_SCANS,
    "shift": _SHIFT_SCANS,
    "windows": _WIN_SCANS,
    "win": _WIN_SCANS,
}

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
        if p in _MODIFIER_SCANS:
            mods.add(p if p != "win" else "windows")
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


def _main_key_vk(name: str) -> int | None:
    if name == "right":
        return None  # handled by the mouse hook
    if name in _KEY_NAME_TO_VK:
        return _KEY_NAME_TO_VK[name]
    if len(name) == 1 and name.isalpha():
        return ord(name.upper()) - ord("A") + 0x41
    if len(name) == 1 and name.isdigit():
        return ord(name) - ord("0") + 0x30
    if name.startswith("f") and name[1:].isdigit():
        return 0x6F + int(name[1:])
    return None


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)
_MOUSEPROC = ctypes.WINFUNCTYPE(
    ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)


class _WinHookThread(threading.Thread):
    """Owns the LL hooks + message loop; feeds key events to the manager."""

    def __init__(self, manager: "HotkeyManager") -> None:
        super().__init__(name="voice-cmds-hook", daemon=True)
        self._manager = manager
        self._tid = 0
        self._kb_proc = None
        self._ms_proc = None
        self._kb_hook = None
        self._ms_hook = None
        self._stopping = False

    def run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = [
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD
        ]
        user32.CallNextHookEx.restype = ctypes.c_longlong
        user32.CallNextHookEx.argtypes = [
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        ]
        self._tid = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)
        self._kb_proc = _HOOKPROC(self._kb_callback)
        self._ms_proc = _MOUSEPROC(self._ms_callback)
        self._kb_hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_proc, hmod, 0)
        self._ms_hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_proc, hmod, 0)
        if not self._kb_hook:
            logger.error("SetWindowsHookExW(WH_KEYBOARD_LL) failed")
            return
        logger.debug("Win32 hooks installed on thread %d", self._tid)

        msg = wintypes.MSG()
        getmsg = user32.GetMessageW
        getmsg.argtypes = [
            ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT
        ]
        getmsg.restype = ctypes.c_int
        while getmsg(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self._kb_hook:
            user32.UnhookWindowsHookEx(self._kb_hook)
            self._kb_hook = None
        if self._ms_hook:
            user32.UnhookWindowsHookEx(self._ms_hook)
            self._ms_hook = None
        logger.debug("Win32 hooks removed")

    def stop(self) -> None:
        if self._tid and self.is_alive() and not self._stopping:
            self._stopping = True
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _kb_callback(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0 and self._kb_hook:
            s = ctypes.cast(lparam, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
            scan = s.scanCode
            if (s.flags & LLKHF_EXTENDED) and not (scan & 0x100):
                scan |= 0x100
            down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
            logger.debug(
                "hook event: wp=%d scan=%#x vk=%#x down=%s",
                wparam, scan, s.vkCode, down,
            )
            self._manager._on_key(int(scan), int(s.vkCode), down)
        return ctypes.windll.user32.CallNextHookEx(
            self._kb_hook, code, wparam, lparam
        )

    def _ms_callback(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0 and wparam == WM_RBUTTONDOWN and self._ms_hook:
            self._manager._on_mouse_right()
        return ctypes.windll.user32.CallNextHookEx(
            self._ms_hook, code, wparam, lparam
        )


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
        self._down: set[int] = set()
        self._thread: _WinHookThread | None = None

    def set_recording(self, value: bool) -> None:
        self._recording = value

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = _WinHookThread(self)
        self._thread.start()
        logger.warning(  # WARNING so it shows without --debug
            "Hotkeys registered: start=%r  stop=%r (only while recording)  cancel=%r",
            self.start_combo, self.stop_combo, self.cancel_combo,
        )

    def stop(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.stop()

    # --- hook-thread callbacks ---------------------------------------------
    def _on_key(self, scan: int, vk: int, down: bool) -> None:
        if down:
            if scan in self._down:
                return  # auto-repeat
            self._down.add(scan)
            self._evaluate(scan, vk)
        else:
            self._down.discard(scan)

    def _on_mouse_right(self) -> None:
        if self._recording:
            if self._stop.get("main") == "right" and self._mods_down(self._stop):
                self._fire_stop()
            elif self._cancel.get("main") == "right" and self._mods_down(self._cancel):
                self._fire_cancel()

    def _evaluate(self, scan: int, vk: int) -> None:
        if not self._recording:
            if self._combo_complete(self._start, scan, vk):
                self._consume(self._start, scan, vk)
                self._recording = True
                self.start_pressed.emit()
                return
            # Not recording: stop/cancel must not fire (and must not be
            # consumed — a held alt that later completes the start combo
            # stays in _down, which is what we want).
        else:
            if self._combo_complete(self._stop, scan, vk):
                self._consume(self._stop, scan, vk)
                self._fire_stop()
                return
            if self._combo_complete(self._cancel, scan, vk):
                self._consume(self._cancel, scan, vk)
                self._fire_cancel()
                return

    def _fire_stop(self) -> None:
        self._recording = False
        self._down.clear()
        self.stop_pressed.emit()

    def _fire_cancel(self) -> None:
        self._recording = False
        self._down.clear()
        self.cancel_pressed.emit()

    def _mods_down(self, combo: dict) -> bool:
        return all(
            any(s in self._down for s in _MODIFIER_SCANS[m]) for m in combo["mods"]
        )

    def _combo_complete(self, combo: dict, scan: int, vk: int) -> bool:
        if not self._mods_down(combo):
            return False
        main = combo["main"]
        if main is None:
            # Modifier-only combo (e.g. ctrl+alt): complete when the just
            # pressed key is one of the combo's modifiers.
            return any(scan in _MODIFIER_SCANS[m] for m in combo["mods"])
        if main == "right":
            return False  # mouse events come via _on_mouse_right
        return _main_key_vk(main) == vk

    def _consume(self, combo: dict, scan: int, vk: int) -> None:
        """Remove the completing key from _down so the same physical press
        cannot also complete another hotkey (e.g. start's alt also matching
        the stop hotkey) — but held modifiers stay available for a repeat."""
        main = combo["main"]
        if main is not None and main != "right":
            self._down.discard(scan)
        elif main is None:
            self._down.discard(scan)

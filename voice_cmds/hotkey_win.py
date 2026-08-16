"""Windows hotkey backend: Win32 low-level keyboard + mouse hooks.

Runs a dedicated thread with its own GetMessage loop (a low-level hook
requires a message pump) and translates raw events to canonical key names
for the platform-independent matcher in voice_cmds.hotkey.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

logger = logging.getLogger("voice_cmds.hotkey_win")

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_RBUTTONDOWN = 0x0204
WM_QUIT = 0x0012
LLKHF_EXTENDED = 0x01

_SCAN_TO_MOD = {
    0x1D: "ctrl",
    0x11D: "ctrl",
    0x38: "alt",
    0x138: "alt",
    0x2A: "shift",
    0x136: "shift",
    0x15B: "windows",
    0x15C: "windows",
}

_VK_TO_NAME = {
    0x1B: "esc",
    0x20: "space",
    0x0D: "enter",
    0x09: "tab",
    0x08: "backspace",
    0x2D: "insert",
    0x2E: "delete",
    0x24: "home",
    0x23: "end",
    0x21: "page up",
    0x22: "page down",
    0x26: "up",
    0x28: "down",
    0x25: "left",
    0x27: "right",
    0x14: "caps lock",
    0x2C: "print screen",
    0x13: "pause",
    0xAF: "volume up",
    0xAE: "volume down",
    0xAD: "volume mute",
    0xB3: "play/pause media",
    0xB0: "next track",
    0xB1: "previous track",
}


def name_for_key(scan: int, vk: int) -> str | None:
    """Map a (scan code, virtual key) pair to a canonical key name."""
    if scan in _SCAN_TO_MOD:
        return _SCAN_TO_MOD[scan]
    if 0x41 <= vk <= 0x5A:
        return chr(vk - 0x41 + ord("a"))
    if 0x30 <= vk <= 0x39:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    return _VK_TO_NAME.get(vk)


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

    def __init__(self, manager) -> None:
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
            name = name_for_key(int(scan), int(s.vkCode))
            logger.debug(
                "hook event: wp=%d scan=%#x vk=%#x down=%s name=%r",
                wparam, scan, s.vkCode, down, name,
            )
            if name:
                self._manager._on_key_name(name, down)
        return ctypes.windll.user32.CallNextHookEx(
            self._kb_hook, code, wparam, lparam
        )

    def _ms_callback(self, code: int, wparam: int, lparam: int) -> int:
        if code >= 0 and wparam == WM_RBUTTONDOWN and self._ms_hook:
            self._manager._on_mouse_right()
        return ctypes.windll.user32.CallNextHookEx(
            self._ms_hook, code, wparam, lparam
        )


class Backend:
    """Windows backend factory for voice_cmds.hotkey.HotkeyManager."""

    def __init__(self, manager) -> None:
        self._manager = manager

    def create_thread(self) -> threading.Thread:
        return _WinHookThread(self._manager)

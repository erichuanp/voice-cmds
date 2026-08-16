"""Single-instance guard: one voice-cmds process per user session.

A named mutex is held for the process lifetime (the OS releases it when the
process exits, so stale locks are impossible). `release()` is called by the
restart path before spawning the replacement process — otherwise the child
would briefly see the parent's mutex and refuse to start.
"""
from __future__ import annotations

import ctypes

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ERROR_ALREADY_EXISTS = 183
_MUTEX_NAME = "voice-cmds-single-instance-mutex"

_handle = None


def acquire() -> bool:
    """Try to take the singleton mutex. False => another instance is running."""
    global _handle
    _kernel32.CreateMutexW.restype = ctypes.c_void_p
    _kernel32.CreateMutexW.argtypes = (
        ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p,
    )
    _handle = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not _handle:
        return True  # mutex creation failed — don't block the app on this
    return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS


def release() -> None:
    """Release the mutex early (used by the self-restart path)."""
    global _handle
    if _handle:
        _kernel32.CloseHandle(_handle)
        _handle = None

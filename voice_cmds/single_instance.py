"""Single-instance guard: one voice-cmds process per user session.

- Windows: a named mutex held for the process lifetime (the OS releases it
  when the process exits, so stale locks are impossible).
- macOS: an advisory `flock` on a lock file under the data dir (released by
  the OS when the process exits, same guarantee).

`release()` is called by the restart path before spawning the replacement
process — otherwise the child would briefly see the parent's lock and
refuse to start.
"""
from __future__ import annotations

import sys

if sys.platform == "darwin":
    import fcntl

    from .config import DATA_DIR

    _LOCK_PATH = DATA_DIR / ".single-instance.lock"
    _fd = None

    def acquire() -> bool:
        global _fd
        try:
            _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            _fd = open(_LOCK_PATH, "a+")
            fcntl.flock(_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, BlockingIOError):
            return False

    def release() -> None:
        global _fd
        if _fd is not None:
            try:
                fcntl.flock(_fd, fcntl.LOCK_UN)
                _fd.close()
            except OSError:
                pass
            _fd = None

else:
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

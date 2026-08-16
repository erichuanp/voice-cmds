"""Built-in system commands — platform-neutral facade.

The trigger list is identical on every platform; each implementation lives
in a per-platform module (_sys_win / _sys_mac) with the same function
names. 取消关机 additionally cancels pending scheduled shutdown/restart
tasks via the scheduler (platform-neutral).
"""
from __future__ import annotations

import logging
import sys

if sys.platform == "darwin":
    from . import _sys_mac as _impl
else:
    from . import _sys_win as _impl


# (trigger, function name in the platform module)
SYSTEM_COMMANDS: list[tuple[str, str]] = [
    ("关机", "shutdown"),
    ("重启", "restart"),
    ("睡眠", "sleep"),
    ("注销", "logoff"),
    ("保持开机", "abort_shutdown"),
    ("取消关机", "abort_shutdown"),
    ("锁屏", "lock"),
    ("音量加", "volume_up"),
    ("音量减", "volume_down"),
    ("静音", "volume_mute"),
    ("暂停", "media_play_pause"),
    ("播放", "media_play_pause"),
    ("下一首", "media_next"),
    ("上一首", "media_prev"),
    ("关闭当前窗口", "close_window"),
    ("最小化全部", "minimize_all"),
    ("清空回收站", "empty_recycle_bin"),
]


def _abort_shutdown(config, logger: logging.Logger, scheduler=None) -> None:
    _impl.abort_shutdown(config, logger)
    # Also cancel pending scheduled 关机/重启 tasks (the scheduler matches
    # each task's command text against the same command set).
    if scheduler is not None:
        try:
            cancelled = scheduler.cancel_matching({"shutdown", "restart"})
            if cancelled:
                logger.info("Cancelled %d scheduled shutdown/restart task(s)", cancelled)
        except Exception as e:
            logger.warning("Failed to cancel scheduled shutdown tasks: %s", e)


def dispatch(fn_name: str, config, logger: logging.Logger, *extra) -> None:
    if fn_name == "abort_shutdown":
        _abort_shutdown(config, logger, *extra)
        return
    fn = getattr(_impl, fn_name, None)
    if not callable(fn):
        raise RuntimeError(f"Unknown system function: {fn_name}")
    # macOS shutdown/restart get the abortable 15s grace period through the
    # scheduler (the Windows impls have shutdown /t 15 natively).
    if extra and sys.platform == "darwin" and fn_name in ("shutdown", "restart"):
        fn(config, logger, scheduler=extra[0])
    else:
        fn(config, logger)

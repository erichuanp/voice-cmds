"""Built-in system commands. Each function takes (config, logger) and executes."""
from __future__ import annotations

import ctypes
import logging
import subprocess


# (trigger, function name in this module)
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
    ("打开资源管理器", "open_explorer"),
    ("清空回收站", "empty_recycle_bin"),
]


def _run(cmd: list[str], logger: logging.Logger) -> None:
    logger.info("Run: %s", " ".join(cmd))
    subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)


def _send_vk(vk: int) -> None:
    KEYEVENTF_KEYUP = 0x0002
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


# --- implementations ---

def shutdown(config, logger):
    delay = config.settings["shutdown_delay_seconds"]
    _run(["shutdown", "/s", "/t", str(delay)], logger)


def restart(config, logger):
    delay = config.settings["shutdown_delay_seconds"]
    _run(["shutdown", "/r", "/t", str(delay)], logger)


def sleep(config, logger):
    # /h would hibernate; SetSuspendState invokes sleep. Use rundll32 directly.
    _run(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], logger)


def logoff(config, logger):
    delay = config.settings["shutdown_delay_seconds"]
    _run(["shutdown", "/l", "/t", str(delay)], logger)


def abort_shutdown(config, logger):
    _run(["shutdown", "/a"], logger)


def lock(config, logger):
    ctypes.windll.user32.LockWorkStation()
    logger.info("LockWorkStation")


def volume_up(config, logger):
    _send_vk(0xAF)  # VK_VOLUME_UP


def volume_down(config, logger):
    _send_vk(0xAE)  # VK_VOLUME_DOWN


def volume_mute(config, logger):
    _send_vk(0xAD)  # VK_VOLUME_MUTE


def media_play_pause(config, logger):
    _send_vk(0xB3)  # VK_MEDIA_PLAY_PAUSE


def media_next(config, logger):
    _send_vk(0xB0)  # VK_MEDIA_NEXT_TRACK


def media_prev(config, logger):
    _send_vk(0xB1)  # VK_MEDIA_PREV_TRACK


def close_window(config, logger):
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    WM_CLOSE = 0x0010
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    logger.info("WM_CLOSE -> hwnd=%s", hwnd)


def minimize_all(config, logger):
    # Win+D toggles desktop; using shell COM object is more "minimize all":
    import win32com.client
    win32com.client.Dispatch("Shell.Application").MinimizeAll()
    logger.info("Shell.MinimizeAll")


def open_explorer(config, logger):
    _run(["explorer.exe"], logger)


def empty_recycle_bin(config, logger):
    SHERB_NOCONFIRMATION = 0x00000001
    SHERB_NOPROGRESSUI = 0x00000002
    SHERB_NOSOUND = 0x00000004
    flags = SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND
    res = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, flags)
    logger.info("SHEmptyRecycleBin -> %s", res)


def dispatch(fn_name: str, config, logger) -> None:
    fn = globals().get(fn_name)
    if not callable(fn):
        raise RuntimeError(f"Unknown system function: {fn_name}")
    fn(config, logger)

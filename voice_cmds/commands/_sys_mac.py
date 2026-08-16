"""macOS implementations of the built-in system commands.

Every function has the signature fn(config, logger). Loaded only on macOS
by voice_cmds.commands.system.

Implementation notes:
- Shutdown/restart/logout go through System Events AppleScript — needs the
  Apple Events (Automation) permission. The OS prompts on first use.
- Lock sends Cmd+Ctrl+Q via System Events keystroke — needs Accessibility
  (which the app already requires for its global hotkeys).
- Volume uses `set volume output volume …` (no special permission).
- Media keys are posted as system-defined NX_KEYTYPE events through
  CoreGraphics — same mechanism media-key apps use, no permission needed.
"""
from __future__ import annotations

import logging
import subprocess


def _run(cmd: list[str], logger: logging.Logger) -> None:
    logger.info("Run: %s", " ".join(cmd))
    subprocess.Popen(cmd, start_new_session=True)


def _osascript(script: str, logger: logging.Logger) -> None:
    _run(["osascript", "-e", script], logger)


def _post_media_key(nx_keytype: int) -> None:
    import Quartz

    down = Quartz.CGEventCreateKeyboardEvent(None, nx_keytype, True)
    up = Quartz.CGEventCreateKeyboardEvent(None, nx_keytype, False)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


def shutdown(config, logger):
    _osascript('tell app "System Events" to shut down', logger)


def restart(config, logger):
    _osascript('tell app "System Events" to restart', logger)


def sleep(config, logger):
    _run(["pmset", "sleepnow"], logger)


def logoff(config, logger):
    _osascript('tell app "System Events" to log out', logger)


def abort_shutdown(config, logger):
    # macOS has no pending OS-level shutdown to abort; scheduled 关机/重启
    # tasks are cancelled by the shared abort_shutdown wrapper in system.py.
    logger.info("abort_shutdown: nothing pending at the OS level")


def lock(config, logger):
    # Cmd+Ctrl+Q is the system "Lock Screen" shortcut (macOS 10.13+).
    _osascript(
        'tell application "System Events" to keystroke "q" using {control down, command down}',
        logger,
    )


def volume_up(config, logger):
    _osascript(
        'set volume output volume ((output volume of (get volume settings)) + 5)',
        logger,
    )


def volume_down(config, logger):
    _osascript(
        'set volume output volume ((output volume of (get volume settings)) - 5)',
        logger,
    )


def volume_mute(config, logger):
    _osascript(
        'set volume output muted not (output muted of (get volume settings))',
        logger,
    )


def media_play_pause(config, logger):
    _post_media_key(16)  # NX_KEYTYPE_PLAY


def media_next(config, logger):
    _post_media_key(17)  # NX_KEYTYPE_NEXT


def media_prev(config, logger):
    _post_media_key(18)  # NX_KEYTYPE_PREVIOUS


def close_window(config, logger):
    _osascript(
        'tell application "System Events" to keystroke "w" using command down',
        logger,
    )


def minimize_all(config, logger):
    # Cmd+Opt+M minimizes all windows of the frontmost app — the closest
    # macOS counterpart to Windows "minimize all".
    _osascript(
        'tell application "System Events" to keystroke "m" using {option down, command down}',
        logger,
    )


def empty_recycle_bin(config, logger):
    _osascript('tell application "Finder" to empty trash', logger)

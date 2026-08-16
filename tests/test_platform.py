"""Platform-specific tests — the right branch runs per OS."""
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_launchagent_plist():
    from voice_cmds import autostart

    orig = autostart._PLIST_PATH
    try:
        with tempfile.TemporaryDirectory() as td:
            autostart._PLIST_PATH = Path(td) / "com.erichuanp.voice-cmds.plist"
            autostart.enable()
            assert autostart.is_enabled()
            r = subprocess.run(
                ["plutil", "-lint", str(autostart._PLIST_PATH)],
                capture_output=True, text=True,
            )
            assert r.returncode == 0, r.stderr
            autostart.disable()
            assert not autostart.is_enabled()
    finally:
        autostart._PLIST_PATH = orig


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_single_instance_flock():
    from voice_cmds import single_instance

    assert single_instance.acquire() is True
    single_instance.release()
    assert single_instance.acquire() is True
    single_instance.release()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_update_sh_lines():
    from voice_cmds.updater import build_update_sh_lines

    plan = {"files": ["a.txt", "manifest.json"], "deleted": ["old file.dll"]}
    text = "\n".join(build_update_sh_lines(Path("/tmp/app"), plan))
    assert "cp -R _update/. ." in text
    assert 'rm -f "./old file.dll"' in text
    assert "nohup ./voice-cmds" in text
    assert 'rm -f "./_update.json"' in text


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_mac_app_launcher():
    from voice_cmds.commands._apps_mac import build_command

    assert build_command(Path("/Applications/Safari.app"), []) == [
        "open", "/Applications/Safari.app"
    ]
    assert build_command(Path("/Applications/X.app"), ["--x"]) == [
        "open", "/Applications/X.app", "--args", "--x"
    ]
    assert build_command(Path("/tmp/a.sh"), ["-v"]) == ["/bin/sh", "/tmp/a.sh", "-v"]
    py = build_command(Path("/tmp/a.py"), [])
    assert py[-1] == "/tmp/a.py" and py[0].endswith("python3")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_mac_modules_importable():
    from voice_cmds import hotkey_mac
    from voice_cmds.commands import _sys_mac  # noqa: F401
    from voice_cmds.hotkey import parse_combo

    # every keycode name must be usable in a combo
    for name in set(hotkey_mac._KEYCODE_TO_NAME.values()):
        parse_combo(name)
    assert hotkey_mac._KEYCODE_TO_NAME[55] == "windows"
    assert hotkey_mac._KEYCODE_TO_NAME[59] == "ctrl"
    assert hotkey_mac._KEYCODE_TO_NAME[58] == "alt"


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_mac_shutdown_grace_scheduling():
    """shutdown/restart schedule a 15s abortable grace task when given a scheduler."""
    import logging

    from voice_cmds.commands import _sys_mac

    class _Scheduler:
        def __init__(self):
            self.calls = []

        def add_delay(self, command, seconds):
            self.calls.append((command, seconds))

    log = logging.getLogger("test")
    s = _Scheduler()
    _sys_mac.shutdown(None, log, scheduler=s)
    assert s.calls == [("关机", 15)]
    _sys_mac.restart(None, log, scheduler=s)
    assert s.calls == [("关机", 15), ("重启", 15)]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_mac_dispatch_forwards_scheduler():
    import logging

    from voice_cmds.commands import system as sm

    class _Scheduler:
        def __init__(self):
            self.calls = []

        def add_delay(self, command, seconds):
            self.calls.append((command, seconds))

    s = _Scheduler()
    sm.dispatch("shutdown", None, logging.getLogger("test"), s)
    assert s.calls == [("关机", 15)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_single_instance():
    from voice_cmds import single_instance

    assert single_instance.acquire() is True
    single_instance.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_key_names():
    from voice_cmds.hotkey_win import name_for_key

    assert name_for_key(0x1D, 0xA2) == "ctrl"
    assert name_for_key(0x138, 0xA5) == "alt"
    assert name_for_key(0x15B, 0x5B) == "windows"
    assert name_for_key(0, 0x51) == "q"
    assert name_for_key(0, 0xAF) == "volume up"
    assert name_for_key(0, 0x70) == "f1"

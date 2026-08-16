"""Headless self-test (--selftest): exercises everything that needs no
models, microphone, or OS permissions. Used by the macOS CI pipeline and
run locally on Windows before pushing.

Covers, on BOTH platforms:
- config load/seed + hotkey combo parsing
- the platform-independent matcher pieces (timed tokens, aliases, pinyin)
- the HotkeyManager matcher via simulated key-name feeds (no OS hooks)
- the hotkey capture widget (Windows capture path; synthetic events)
- overlay editor append/current_text
- the settings dialog construction (offscreen)
On macOS additionally:
- LaunchAgent plist generation validated with plutil
- single-instance flock
- the update.sh script generation
- the macOS app launcher command builder
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _check(name: str, fn) -> None:
    fn()
    print(f"  ok: {name}")


def run() -> int:
    print("voice-cmds selftest:", sys.platform)

    # --- pure matcher / parser pieces (no Qt, no models) -------------------
    from voice_cmds.hotkey import normalize_combo, parse_combo

    def _combos():
        assert normalize_combo("left ctrl+right alt") == "ctrl+alt"
        assert normalize_combo("right alt") == "alt"
        assert normalize_combo("cmd+shift") == "windows+shift"
        parse_combo("ctrl+alt")
        parse_combo("ctrl+q")
        parse_combo("esc")
        parse_combo("right")
        parse_combo("ctrl+shift+f12")
        for bad in ("", "nonsense key", "f25"):
            try:
                parse_combo(bad)
                raise AssertionError(f"parse_combo({bad!r}) should raise")
            except ValueError:
                pass

    _check("combo parsing", _combos)

    from voice_cmds.matcher import (
        _parse_time_tokens,
        _split_aliases,
        _to_toned_pinyin,
        format_delay,
    )

    def _matcher_pieces():
        assert _parse_time_tokens("3小时30分15秒") == 12615
        assert _parse_time_tokens("一小时零五分") == 3900
        assert _parse_time_tokens("半小时") == 1800
        assert _parse_time_tokens("十五秒") == 15
        assert _parse_time_tokens("3时") == 10800
        assert _parse_time_tokens("170时") is None  # out of range
        assert format_delay(12615) == "3小时30分15秒"
        assert _split_aliases("code;vs") == ["code", "vs"]
        assert _split_aliases("a；b") == ["a", "b"]
        assert _to_toned_pinyin("清空回收站") == "qing1kong1hui2shou1zhan4"

    _check("matcher pieces", _matcher_pieces)

    # --- hotkey manager matcher via simulated feeds (no OS hooks) ----------
    from voice_cmds.hotkey import HotkeyManager

    def _manager():
        fired = []
        m = HotkeyManager("ctrl+alt", "alt", "esc")
        m.start_pressed.connect(lambda: fired.append("start"))
        m.stop_pressed.connect(lambda: fired.append("stop"))
        m.cancel_pressed.connect(lambda: fired.append("cancel"))
        m._on_key_name("ctrl", True)
        m._on_key_name("alt", True)   # completes start
        assert fired == ["start"], fired
        m._on_key_name("alt", False)
        m._on_key_name("ctrl", False)
        m._on_key_name("alt", True)   # stop
        assert fired == ["start", "stop"], fired
        m._on_key_name("alt", False)
        # cancel path
        m2 = HotkeyManager("ctrl+alt", "alt", "esc")
        f2 = []
        m2.cancel_pressed.connect(lambda: f2.append("cancel"))
        m2._on_key_name("ctrl", True)
        m2._on_key_name("alt", True)
        m2._on_key_name("ctrl", False)
        m2._on_key_name("alt", False)
        m2._on_key_name("esc", True)
        assert f2 == ["cancel"], f2
        # mouse right as stop
        m3 = HotkeyManager("ctrl+alt", "right", "esc")
        f3 = []
        m3.stop_pressed.connect(lambda: f3.append("stop"))
        m3._on_key_name("ctrl", True)
        m3._on_key_name("alt", True)
        m3._on_key_name("ctrl", False)
        m3._on_key_name("alt", False)
        m3._on_mouse_right()
        assert f3 == ["stop"], f3

    _check("hotkey manager matcher", _manager)

    # --- config ------------------------------------------------------------
    from voice_cmds.config import Config, DATA_DIR, MODELS_DIR

    def _config():
        cfg = Config()
        assert cfg.settings["hotkey"]["start"] in ("ctrl+alt",)
        assert MODELS_DIR == DATA_DIR / "models"

    _check("config load/seed", _config)

    # --- Qt widgets (offscreen) --------------------------------------------
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication(sys.argv)

    def _capture_widget():
        from voice_cmds.ui.hotkeyedit import HotkeyLineEdit

        e = HotkeyLineEdit("left ctrl+right alt", two_keys=True)
        assert e.text() == "ctrl+alt"
        e._begin_capture()
        e._process_key(0x138, Qt.Key_Alt, Qt.NoModifier)
        assert e.text() == "alt"
        e._process_key(0x1D, Qt.Key_Control, Qt.NoModifier)
        assert e.text() == "alt+ctrl"
        if sys.platform == "darwin":
            # macOS capture path (no scan codes)
            e2 = HotkeyLineEdit("windows+alt", two_keys=False)
            e2._begin_capture()
            e2._process_key_mac(_FakeEvent(Qt.Key_Meta, Qt.MetaModifier))
            assert e2.text() == "windows", e2.text()

    _check("hotkey capture widget", _capture_widget)

    def _overlay():
        from voice_cmds.ui.overlay import OverlayWindow

        o = OverlayWindow(Config().settings)
        o.show_recording(editable=True)
        o.append_partial("清空")
        o.append_partial("回收站")
        assert o.current_text() == "清空回收站"
        o.hide_overlay()

    _check("overlay editor", _overlay)

    def _settings_dialog():
        from voice_cmds.ui.settings import SettingsDialog

        QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
        QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
        d = SettingsDialog(Config(), debug=True)
        d.close()
        d.deleteLater()

    _check("settings dialog construction", _settings_dialog)

    # --- platform-specific ------------------------------------------------
    if sys.platform == "darwin":
        def _autostart_plist():
            from voice_cmds import autostart

            # Generate a plist into a temp dir by monkeypatching the path.
            orig = autostart._PLIST_PATH
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
            autostart._PLIST_PATH = orig

        _check("LaunchAgent plist (plutil-validated)", _autostart_plist)

        def _single_instance():
            from voice_cmds import single_instance

            assert single_instance.acquire() is True
            single_instance.release()

        _check("single instance flock", _single_instance)

        def _update_sh():
            from voice_cmds.updater import build_update_sh_lines

            plan = {"files": ["a.txt", "manifest.json"], "deleted": ["old.dll"]}
            lines = build_update_sh_lines(Path("/tmp/app"), plan)
            text = "\n".join(lines)
            assert "cp -R _update/. ." in text
            assert 'rm -f "./old.dll"' in text
            assert "nohup ./voice-cmds" in text

        _check("update.sh generation", _update_sh)

        def _app_launcher():
            from voice_cmds.commands._apps_mac import build_command

            assert build_command(Path("/Applications/Safari.app"), []) == [
                "open", "/Applications/Safari.app"
            ]
            assert build_command(Path("/Applications/X.app"), ["--x"]) == [
                "open", "/Applications/X.app", "--args", "--x"
            ]
            assert build_command(Path("/tmp/a.sh"), ["-v"]) == [
                "/bin/sh", "/tmp/a.sh", "-v"
            ]
            py = build_command(Path("/tmp/a.py"), [])
            assert py[-1] == "/tmp/a.py"
            assert py[0].endswith("python3")

        _check("macOS app launcher builder", _app_launcher)

        def _mac_modules():
            from voice_cmds.commands import _sys_mac  # noqa: F401
            from voice_cmds import hotkey_mac  # noqa: F401
            from voice_cmds.hotkey_mac import _KEYCODE_TO_NAME
            from voice_cmds.hotkey import parse_combo

            # every keycode name must be usable in a combo
            for name in set(_KEYCODE_TO_NAME.values()):
                parse_combo(name)

        _check("macOS module imports + keycode table", _mac_modules)

    else:
        def _win_modules():
            from voice_cmds import single_instance

            assert single_instance.acquire() is True
            single_instance.release()
            from voice_cmds.hotkey_win import name_for_key

            assert name_for_key(0x1D, 0xA2) == "ctrl"
            assert name_for_key(0x138, 0xA5) == "alt"
            assert name_for_key(0, 0x51) == "q"
            assert name_for_key(0, 0xAF) == "volume up"
            from voice_cmds.commands import _sys_win  # noqa: F401

        _check("windows modules", _win_modules)

    print("SELFTEST OK")
    return 0


class _FakeEvent:
    """Minimal stand-in for a QKeyEvent in the macOS capture path."""

    def __init__(self, key: int, modifiers) -> None:
        self._key = key
        self._mods = modifiers

    def key(self) -> int:
        return self._key

    def modifiers(self):
        return self._mods

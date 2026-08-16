"""Qt widget tests (offscreen) — run on both platforms."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from voice_cmds.config import Config
from voice_cmds.ui.hotkeyedit import HotkeyLineEdit
from voice_cmds.ui.overlay import OverlayWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_hotkey_capture_windows_path(qapp):
    e = HotkeyLineEdit("left ctrl+right alt", two_keys=True)
    assert e.text() == "ctrl+alt"
    e._begin_capture()
    e._process_key(0x138, Qt.Key_Alt, Qt.NoModifier)
    assert e.text() == "alt"
    e._process_key(0x1D, Qt.Key_Control, Qt.NoModifier)
    assert e.text() == "alt+ctrl"


def test_hotkey_capture_combo(qapp):
    q = HotkeyLineEdit("ctrl+q", two_keys=False)
    q._begin_capture()
    q._process_key(0, Qt.Key_Q, Qt.ControlModifier)
    assert q.text() == "ctrl+q"


def test_hotkey_capture_esc_cancels(qapp):
    e = HotkeyLineEdit("esc", two_keys=False)
    e._begin_capture()
    e._process_key(0, Qt.Key_X, Qt.NoModifier)
    assert e.text() == "x"
    e._begin_capture()
    e._process_key(0, Qt.Key_Escape, Qt.NoModifier)
    assert e.text() == "x"  # restored previous value


def test_overlay_editor(qapp):
    o = OverlayWindow(Config().settings)
    o.show_recording(editable=True)
    o.append_partial("清空")
    o.append_partial("回收站")
    assert o.current_text() == "清空回收站"
    o.append_partial("")  # no-op
    assert o.current_text() == "清空回收站"
    o.hide_overlay()


def test_settings_dialog_constructs(qapp):
    from voice_cmds.ui.settings import SettingsDialog

    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
    d = SettingsDialog(Config(), debug=True)
    d.close()
    d.deleteLater()

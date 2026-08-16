"""HotkeyManager matcher tests via simulated key-name feeds (no OS hooks)."""
from voice_cmds.hotkey import HotkeyManager


def _connect(manager):
    fired = []
    manager.start_pressed.connect(lambda: fired.append("start"))
    manager.stop_pressed.connect(lambda: fired.append("stop"))
    manager.cancel_pressed.connect(lambda: fired.append("cancel"))
    return fired


def test_start_stop_flow():
    m = HotkeyManager("ctrl+alt", "alt", "esc")
    fired = _connect(m)
    m._on_key_name("ctrl", True)
    m._on_key_name("alt", True)  # completes start
    assert fired == ["start"]
    m._on_key_name("alt", False)
    m._on_key_name("ctrl", False)
    m._on_key_name("alt", True)  # stop
    assert fired == ["start", "stop"]


def test_stop_does_not_fire_before_start():
    m = HotkeyManager("ctrl+alt", "alt", "esc")
    fired = _connect(m)
    m._on_key_name("alt", True)
    m._on_key_name("alt", False)
    assert fired == []


def test_start_not_retriggered_by_held_keys():
    m = HotkeyManager("ctrl+alt", "alt", "esc")
    fired = _connect(m)
    m._on_key_name("ctrl", True)
    m._on_key_name("alt", True)   # completes start
    m._on_key_name("alt", True)   # OS auto-repeat while held — ignored
    m._on_key_name("ctrl", True)  # ditto
    assert fired == ["start"]
    # a fresh alt press (after release) stops
    m._on_key_name("alt", False)
    m._on_key_name("ctrl", False)
    m._on_key_name("alt", True)
    assert fired == ["start", "stop"]


def test_cancel():
    m = HotkeyManager("ctrl+alt", "alt", "esc")
    fired = _connect(m)
    m._on_key_name("ctrl", True)
    m._on_key_name("alt", True)
    m._on_key_name("ctrl", False)
    m._on_key_name("alt", False)
    m._on_key_name("esc", True)
    assert fired == ["start", "cancel"]


def test_mouse_right_stop():
    m = HotkeyManager("ctrl+alt", "right", "esc")
    fired = _connect(m)
    m._on_key_name("ctrl", True)
    m._on_key_name("alt", True)
    m._on_key_name("ctrl", False)
    m._on_key_name("alt", False)
    m._on_mouse_right()
    assert fired == ["start", "stop"]


def test_main_key_combo():
    m = HotkeyManager("ctrl+q", "alt", "esc")
    fired = _connect(m)
    m._on_key_name("ctrl", True)
    m._on_key_name("q", True)
    assert fired == ["start"]
    m._on_key_name("q", False)
    m._on_key_name("ctrl", False)

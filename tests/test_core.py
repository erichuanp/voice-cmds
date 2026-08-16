"""Platform-independent core logic tests (run on Windows AND macOS CI)."""
from voice_cmds.hotkey import normalize_combo, parse_combo
from voice_cmds.matcher import (
    _parse_time_tokens,
    _split_aliases,
    _to_toned_pinyin,
    format_delay,
)


def test_ort_ffi_loads_sherpa_runtime():
    """sherpa-onnx's bundled onnxruntime must load via the C API."""
    from voice_cmds.ort_ffi import _load_api

    _dll, api = _load_api()
    assert api is not None


def test_normalize_combo():
    assert normalize_combo("left ctrl+right alt") == "ctrl+alt"
    assert normalize_combo("right alt") == "alt"
    assert normalize_combo("esc") == "esc"
    assert normalize_combo("ctrl + q") == "ctrl+q"
    assert normalize_combo("cmd+shift") == "windows+shift"


def test_parse_combo():
    assert parse_combo("ctrl+alt") == {"mods": {"ctrl", "alt"}, "main": None}
    assert parse_combo("ctrl+q") == {"mods": {"ctrl"}, "main": "q"}
    assert parse_combo("esc") == {"mods": set(), "main": "esc"}
    assert parse_combo("right") == {"mods": set(), "main": "right"}
    assert parse_combo("win+shift+f12") == {"mods": {"windows", "shift"}, "main": "f12"}
    for bad in ("", "nonsense key", "f25"):
        try:
            parse_combo(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"parse_combo({bad!r}) should raise")


def test_time_tokens():
    assert _parse_time_tokens("3小时30分15秒") == 12615
    assert _parse_time_tokens("一小时零五分") == 3900
    assert _parse_time_tokens("半小时") == 1800
    assert _parse_time_tokens("十五秒") == 15
    assert _parse_time_tokens("3时") == 10800
    assert _parse_time_tokens("170时") is None
    assert _parse_time_tokens("1分61秒") is None
    assert format_delay(12615) == "3小时30分15秒"
    assert format_delay(0) == "0秒"


def test_aliases_and_pinyin():
    assert _split_aliases("code;vs") == ["code", "vs"]
    assert _split_aliases("a；b") == ["a", "b"]
    assert _split_aliases("single") == ["single"]
    assert _to_toned_pinyin("清空回收站") == "qing1kong1hui2shou1zhan4"

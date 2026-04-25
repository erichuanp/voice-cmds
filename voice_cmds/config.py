"""Configuration loading and saving (settings, apps, commands)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    """Where data lives.

    - Source mode: project dir (`voice-cmds/`).
    - Frozen (PyInstaller): the directory holding the exe, so config/models/
      logs sit next to `voice-cmds.exe` and the user can edit / inspect them.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _project_root()
CONFIG_DIR = PROJECT_ROOT / "config"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
MODELS_DIR = PROJECT_ROOT / "models"
LOGS_DIR = PROJECT_ROOT / "logs"
ASSETS_DIR = PROJECT_ROOT / "assets"


DEFAULT_SETTINGS: dict[str, Any] = {
    "hotkey": {
        "start": "left ctrl+right alt",
        "stop": "right alt",
        "cancel": "esc",
    },
    "stop_mode": "hotkey",
    "vad_silence_ms": 1000,
    "max_chars": 15,
    "shutdown_delay_seconds": 15,
    "ui": {
        "color_idle": "#00C853",
        "color_error": "#E53935",
        "bottom_offset_px": 8,
        "max_capsule_width_px": 240,
        "circle_diameter_px": 26,
        "shadow_margin_px": 8,
        "font_size_pt": 7,
    },
    "match": {
        "embedding_similarity_threshold": 0.85,
    },
    "sound": {
        "success_enabled": True,
        "error_enabled": True,
    },
}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _seed_user_data_dirs() -> None:
    """Frozen builds bundle config/ + scripts/ inside _internal/. Copy them to
    the user-writable PROJECT_ROOT (next to the exe) on first run so:
      - users can edit settings without going into the bundle
      - the Settings dialog has a stable place to write back to
      - reads always go through PROJECT_ROOT/config/
    """
    if not getattr(sys, "frozen", False):
        return
    bundle = Path(sys.executable).resolve().parent / "_internal"
    pairs = [
        (bundle / "config", CONFIG_DIR),
        (bundle / "scripts", SCRIPTS_DIR),
    ]
    for src_dir, dst_dir in pairs:
        if not src_dir.exists():
            continue
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src in src_dir.iterdir():
            if src.is_file():
                dst = dst_dir / src.name
                if not dst.exists():
                    shutil.copy2(src, dst)


class Config:
    def __init__(self) -> None:
        _seed_user_data_dirs()
        self.settings_path = CONFIG_DIR / "settings.json"
        self.apps_path = CONFIG_DIR / "apps.json"
        self.commands_path = CONFIG_DIR / "commands.json"
        self.reload()

    def reload(self) -> None:
        self.settings = _deep_merge(DEFAULT_SETTINGS, _read_json(self.settings_path, {}))
        self.apps = _read_json(self.apps_path, [])
        self.commands = _read_json(self.commands_path, [])

    def save_settings(self) -> None:
        _write_json(self.settings_path, self.settings)

    def save_apps(self) -> None:
        _write_json(self.apps_path, self.apps)

    def save_commands(self) -> None:
        _write_json(self.commands_path, self.commands)

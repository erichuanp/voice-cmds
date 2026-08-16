"""Configuration loading and saving (settings, apps, commands)."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    """The directory holding the app's shipped files (the app dir).

    - Source mode: project dir (`voice-cmds/`).
    - Frozen (PyInstaller): the directory holding the executable, so the
      updater operates on files next to the exe (Windows/macOS onedir).
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _data_dir() -> Path:
    """The user-writable data directory (config/models/logs/scripts).

    Windows keeps the portable layout — data sits next to the exe. macOS
    conventions put user data under ~/Library/Application Support (an app
    extracted into /Applications usually can't write next to itself).
    """
    if getattr(sys, "frozen", False) and sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "voice-cmds"
    return _project_root()


PROJECT_ROOT = _project_root()
DATA_DIR = _data_dir()
CONFIG_DIR = DATA_DIR / "config"
SCRIPTS_DIR = DATA_DIR / "scripts"
MODELS_DIR = DATA_DIR / "models"
LOGS_DIR = DATA_DIR / "logs"
ASSETS_DIR = PROJECT_ROOT / "assets"


DEFAULT_SETTINGS: dict[str, Any] = {
    "hotkey": {
        "start": "ctrl+alt",
        "stop": "alt",
        "cancel": "esc",
    },
    "stop_mode": "hotkey",
    "vad_silence_ms": 500,
    "ui": {
        "color_idle": "#00C853",
        "color_error": "#E53935",
        "bottom_offset_px": 8,
        "max_capsule_width_px": 240,
        "circle_diameter_px": 26,
        "shadow_margin_px": 8,
        "font_size_pt": 7,
        "result_text_ms": 1000,
    },
    "match": {
        "embedding_similarity_threshold": 0.85,
        "pinyin_similarity_threshold": 0.88,
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


# Fresh-install default apps on macOS (the bundled apps.json seeds Windows
# paths — 打开资源管理器 → explorer.exe — which mean nothing on a Mac).
MAC_DEFAULT_APPS: list[dict[str, Any]] = [
    {"trigger": "访达", "path": "/System/Applications/Finder.app", "args": []},
    {"trigger": "Safari", "path": "/Applications/Safari.app", "args": []},
    {"trigger": "系统设置", "path": "/System/Applications/System Settings.app", "args": []},
    {"trigger": "终端", "path": "/System/Applications/Utilities/Terminal.app", "args": []},
]


def _seed_user_data_dirs() -> None:
    """Frozen builds bundle config/ + scripts/ inside _internal/. Copy them to
    the user-writable data dir on first run so:
      - users can edit settings without going into the bundle
      - the Settings dialog has a stable place to write back to
      - reads always go through DATA_DIR/config/
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
                # apps.json ships Windows defaults; macOS writes its own seed.
                if src.name == "apps.json" and sys.platform == "darwin":
                    continue
                dst = dst_dir / src.name
                if not dst.exists():
                    shutil.copy2(src, dst)
    if sys.platform == "darwin":
        apps_path = CONFIG_DIR / "apps.json"
        if not apps_path.exists():
            _write_json(apps_path, MAC_DEFAULT_APPS)


class Config:
    def __init__(self) -> None:
        _seed_user_data_dirs()
        self.settings_path = CONFIG_DIR / "settings.json"
        self.apps_path = CONFIG_DIR / "apps.json"
        self.commands_path = CONFIG_DIR / "commands.json"
        self.tasks_path = CONFIG_DIR / "tasks.json"
        self.reload()

    def reload(self) -> None:
        self.settings = _deep_merge(DEFAULT_SETTINGS, _read_json(self.settings_path, {}))
        self.apps = _read_json(self.apps_path, [])
        self.commands = _read_json(self.commands_path, [])
        self.tasks = _read_json(self.tasks_path, [])

    def save_settings(self) -> None:
        _write_json(self.settings_path, self.settings)

    def save_apps(self) -> None:
        _write_json(self.apps_path, self.apps)

    def save_commands(self) -> None:
        _write_json(self.commands_path, self.commands)

    def save_tasks(self) -> None:
        _write_json(self.tasks_path, self.tasks)

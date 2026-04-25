# voice-cmds

Windows 11 voice command tool — press a hotkey, talk a short command, watch a green capsule fill with your words and execute. Streaming Chinese-first STT (sherpa-onnx zipformer-bilingual), three-layer fuzzy matching (literal → pinyin → embedding), runs from system tray.

📘 **[DESIGN.md](DESIGN.md)** is the source of truth for architecture, configuration, and behavior. Always update it alongside code changes.

## Quick start

```powershell
# 1. Create the conda env
conda env create -f environment.yml
conda activate voice-cmds

# 2. Run (model auto-downloads on first hotkey press, ~100MB)
python main.py

# Or with file logging
python main.py --debug
```

## Default hotkeys

| Action | Keys |
|---|---|
| Start recording | `Left Ctrl + Right Alt` |
| Stop (only while recording) | `Right Alt` |
| Cancel (only while recording) | `Esc` |

## Built-in commands

关机 / 重启 / 睡眠 / 注销 / 保持开机 / 锁屏 / 音量加 / 音量减 / 静音 / 暂停 / 播放 / 下一首 / 上一首 / 关闭当前窗口 / 最小化全部 / 打开资源管理器 / 清空回收站

Plus `打开 <触发词>` for any app you've added in settings.

## Customizing

- **打开 X**: tray → 设置 → 打开 (Apps) tab → "添加新的打开"
- **Custom scripts**: tray → 设置 → 自定义命令 tab. Bind a Chinese trigger word to any `.bat` / `.ps1` / `.exe` in the project's `scripts/` folder.
- **Direct file editing**: `config/settings.json`, `config/apps.json`, `config/commands.json`, `config/hot_words.json`. Tray → "重新加载配置" picks up changes (hotkey changes need an app restart).

## Hot-word correction

`config/hot_words.json` rewrites recognized text before matching:

```json
{ "重庆": "重启", "光剂": "关机" }
```

Useful when STT consistently mis-hears a specific word.

## GPU notes

- RTX 5080 (sm_120) needs `onnxruntime-gpu >= 1.20`, CUDA 12.4+, cuDNN 9 — already pinned in `environment.yml`.
- App falls back to CPU automatically if CUDA fails.

## Project layout

See [DESIGN.md §3](DESIGN.md#3-目录结构).

# voice-cmds

Windows 11 voice command tool — press a hotkey, speak a short command, watch a green capsule fill with your words and execute. Streaming Chinese-first STT (sherpa-onnx zipformer-bilingual) feeds a tray-resident app; matched commands fire native Windows actions, configured apps, or user scripts.

📘 **[DESIGN.md](DESIGN.md)** is the source of truth for architecture, configuration, and behavior. Always update it alongside code changes.

---

## Install (recommended)

Download the latest installer from **[Releases](https://github.com/erichuanp/voice-cmds/releases)**:

- `voice-cmds-Setup-v0.0.1.exe` — installs to `%LOCALAPPDATA%\Programs\voice-cmds\`, optional autostart, no admin needed.

On first launch the app downloads ~600MB of models (STT 511MB + embedder 96MB) into `models/` next to the exe. Subsequent launches start in seconds.

## Run from source

```powershell
conda env create -f environment.yml
conda activate voice-cmds
python main.py            # normal
python main.py --debug    # writes logs to ./logs/
```

## Default hotkeys

Edit `config/settings.json` or use **Tray → 设置 → 通用**.

| Action | Default |
|---|---|
| Start recording | `Left Ctrl + Right Alt` |
| Stop (only while recording) | `Right Alt` |
| Cancel (only while recording) | `Esc` |

## Built-in commands

关机 / 重启 / 睡眠 / 注销 / 保持开机 / 锁屏 / 音量加 / 音量减 / 静音 / 暂停 / 播放 / 下一首 / 上一首 / 关闭当前窗口 / 最小化全部 / 打开资源管理器 / 清空回收站

Plus `打开 <触发词>` for any app you've added in Settings.

## Customizing

- **打开 X**: tray → 设置 → "打开 (Apps)" → 添加新的打开
- **Custom scripts**: tray → 设置 → 自定义命令 — bind a Chinese trigger word to any `.bat` / `.ps1` / `.exe`
- **Direct file editing**: `config/settings.json`, `config/apps.json`, `config/commands.json`. Tray → "重新加载配置" picks up changes (saving from the Settings dialog auto-restarts the app).

## Matching

Two layers, in order:

1. **Literal** trigger match
2. **Embedding** (BGE-small-zh-v1.5) over the full registered command set, threshold default 0.85

All triggers are pre-encoded at startup so dispatch is one matmul (~1ms).

## Building from source

```powershell
conda activate voice-cmds
pip install pyinstaller
pyinstaller voice-cmds.spec --clean --noconfirm
# dist/voice-cmds/voice-cmds.exe   (with _internal/ deps, ~1.1 GB)

# Optional installer (requires Inno Setup 6):
iscc installer.iss
# release/voice-cmds-Setup-v0.0.1.exe
```

## Project layout

See [DESIGN.md §3](DESIGN.md#3-目录结构).

## License

MIT (see [LICENSE](LICENSE)).

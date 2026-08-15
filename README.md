# voice-cmds

Windows 11 voice command tool — press a hotkey, speak a short command, watch a green capsule fill with your words and execute. Streaming Chinese-first STT (sherpa-onnx zipformer-bilingual) feeds a tray-resident app; matched commands fire native Windows actions, configured apps, or user scripts. Stop by key press **or automatically after 0.5 s of silence** (VAD); misheard homophones still match thanks to a pinyin-with-tones embedding layer, and the embedder runs on **direct ONNX** (no torch, ~700 MB smaller bundle).

📘 **[DESIGN.md](DESIGN.md)** is the source of truth for architecture, configuration, and behavior. Always update it alongside code changes.

---

## Install (recommended)

Download the latest installer from **[Releases](https://github.com/erichuanp/voice-cmds/releases)**:

- `voice-cmds-Setup-v0.0.1.exe` — installs to `%LOCALAPPDATA%\Programs\voice-cmds\`, optional autostart, no admin needed.

On first launch the app downloads ~375MB of models (STT 280MB + embedder ONNX 95MB) into `models/` next to the exe. Subsequent launches start in seconds.

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

## Stop modes

**Tray → 设置 → 通用 → 停止模式**:

- **`vad` (default)** — execute automatically after 0.5 s of silence (configurable `vad_silence_ms`); `Right Alt` / `Esc` still work.
- **`hotkey`** — execute only when you press the stop key.

## Built-in commands

关机 / 重启 / 睡眠 / 注销 / 保持开机 / 锁屏 / 音量加 / 音量减 / 静音 / 暂停 / 播放 / 下一首 / 上一首 / 关闭当前窗口 / 最小化全部 / 打开资源管理器 / 清空回收站

Plus **timed tasks**: `<时间>后<命令>` — `3小时后打开资源管理器` / `一小时四十一分十二秒后锁屏` / `半小时后静音` / `十五秒后关机` (Arabic or Chinese numerals; 时 0–167, 分/秒 0–59). Manage them in **Tray → 定时任务** (edit / delete / 每日重复 / 循环执行 / manual add); `取消关机` also cancels pending 关机/重启 tasks.

Plus `打开 <触发词>` for any app you've added in Settings.

Tray **帮助** lists every available command.

## Customizing

- **打开 X**: tray → 设置 → 自定义命令 → 添加 → 选“打开<触发词>”。触发词支持 `;`/`；` 多别名（`code;vs` → 打开code 和 打开vs 是同一个）
- **Custom scripts / programs**: tray → 设置 → 自定义命令 → 添加 → 选“触发词”，路径支持 `.bat` / `.ps1` / `.py` / `.exe`
- **Direct file editing**: `config/settings.json`, `config/apps.json`, `config/commands.json`, `config/tasks.json`. 编辑后重启程序生效（在设置窗口内保存会自动重启）。

## Matching

Three layers, in order:

1. **Literal** trigger match
2. **Pinyin + tones embedding** — text is converted to `拼音+声调` (`清空回收站 → qing1kong1hui2shou1zhan4`) before embedding, so STT homophone errors score ~0.95–1.0 against the right trigger (threshold default 0.88, `match.pinyin_similarity_threshold`)
3. **Raw-text embedding** (BGE-small-zh-v1.5 ONNX) fallback, threshold default 0.85

All triggers are pre-encoded at startup (pinyin + raw), so dispatch is two matmuls (~1ms). The embedder uses `Xenova/bge-small-zh-v1.5` ONNX via onnxruntime + tokenizers — CLS pooling reproduces the torch output exactly (cosine 1.0) without shipping torch.

## Building from source

```powershell
conda activate voice-cmds
pip install pyinstaller
pyinstaller voice-cmds.spec --clean --noconfirm
# dist/voice-cmds/voice-cmds.exe   (with _internal/ deps, ~400 MB — no torch)

# Optional installer (requires Inno Setup 6):
iscc installer.iss
# release/voice-cmds-Setup-v0.0.1.exe
```

## Project layout

See [DESIGN.md §3](DESIGN.md#3-目录结构).

## License

MIT (see [LICENSE](LICENSE)).

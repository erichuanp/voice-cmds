# voice-cmds

Windows 11 voice command tool — press a hotkey, speak a short command, watch a green capsule fill with your words and execute. Streaming Chinese-first STT (sherpa-onnx zipformer-bilingual) feeds a tray-resident app; matched commands fire native Windows actions, configured apps, or user scripts. Stop by key press **or automatically after 0.5 s of silence** (VAD); misheard homophones still match thanks to a pinyin-with-tones embedding layer, and the embedder runs on **direct ONNX** (no torch, ~700 MB smaller bundle).

**macOS is supported on the `macos` branch** — see [macOS 支持](#macos-支持) below; CI builds and headless-tests both Intel and Apple Silicon on every push.

📘 **[DESIGN.md](DESIGN.md)** is the source of truth for architecture, configuration, and behavior. Always update it alongside code changes.

---

## Install (recommended)

Download the latest installer from **[Releases](https://github.com/erichuanp/voice-cmds/releases)**:

- `voice-cmds-Setup-vX.Y.Z.exe` — Inno Setup 向导：默认装到 `%LOCALAPPDATA%\Programs\voice-cmds\`，**全程无需管理员权限**；可选桌面快捷方式与开机自启；自带卸载器（开始菜单 / 添加或删除程序），卸载时同时清理开机自启和运行数据（models/logs/config/scripts）。

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
| Start recording | `Ctrl + Alt` |
| Stop (only while recording) | `Alt` |
| Cancel (only while recording) | `Esc` |

Modifiers are side-agnostic (`ctrl` = either Ctrl key; on macOS `windows` = Command ⌘).

## Stop modes

**Tray → 设置 → 通用 → 结束方式**:

- **`hotkey` (default)** — press the stop key to end; the command executes immediately.
- **`vad`** — 0.5 s of silence (configurable) ends recording; the recognized text is then shown for `result_text_ms` (default 1000 ms) before executing. `Right Alt` / `Esc` still work in both modes.

## Hotkey capture

In 设置 → 通用 → 热键, click a field and press the key(s) to record (start needs two keys, stop/cancel one; mouse right button supported, left excluded; Esc cancels). Each row has a 重置 button restoring the default.

## Built-in commands

关机 / 重启 / 睡眠 / 注销 / 保持开机 / 锁屏 / 音量加 / 音量减 / 静音 / 暂停 / 播放 / 下一首 / 上一首 / 关闭当前窗口 / 最小化全部 / 清空回收站

「打开资源管理器」由默认自带的打开条目提供（`打开 资源管理器`），可在 设置 → 命令 中编辑。

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

All triggers are pre-encoded at startup (pinyin + raw), so dispatch is two matmuls (~1ms). The embedder uses `Xenova/bge-small-zh-v1.5` ONNX via tokenizers + a minimal ctypes binding to the onnxruntime.dll sherpa-onnx already ships for STT (no separate onnxruntime package) — CLS pooling reproduces the torch output exactly (cosine 1.0) without shipping torch.

## Building from source

```powershell
conda activate voice-cmds
pip install pyinstaller
pyinstaller voice-cmds.spec --clean --noconfirm
# dist/voice-cmds/voice-cmds.exe   (with _internal/ deps, ~230 MB — no torch, no onnxruntime)

# Optional installer (requires Inno Setup 6):
iscc installer.iss
# release/voice-cmds-Setup-v0.0.1.exe
```

## macOS 支持

`macos` 分支维护完整的 macOS 移植（Intel + Apple Silicon 双架构，GitHub
Actions 上真机构建 + 无头测试）：

- **同一套识别核心**：sherpa-onnx STT、ONNX 语义嵌入、拼音+声调三层匹配、定时任务、混合语音+打字编辑全部一致；热键语义一致（`ctrl`/`alt`/`shift`/`windows`=Command，左右等价，支持右键）
- **平台实现**：全局热键用 Quartz CGEventTap（需「辅助功能」权限，首次启动引导）；系统命令走 `osascript`/`pmset`/CoreGraphics 媒体键；开机自启写 `~/Library/LaunchAgents` 的 launchd plist；单实例用 `flock`；数据目录在 `~/Library/Application Support/voice-cmds/`
- **更新器**：同一套差分热更新（manifest + zip），按架构选择 `-macos-arm64-` / `-macos-x86_64-` 资产；`update.sh` 替代 `update.bat`（macOS 无文件锁，替换更简单）
- **发布**：`build_release.py`（跨平台 manifest+zip 打包）；v1 为便携 zip（解压到可写目录如 `~/Applications` 后运行 `voice-cmds`），首次下载模型约 375MB

构建（macOS）：

```sh
pip install -r mac-requirements.txt
pytest tests -q
python main.py --selftest
pyinstaller voice-cmds.spec --noconfirm
python build_release.py --suffix -macos-arm64   # 或 -macos-x86_64
```

限制（v1）：未做代码签名/公证（首次运行需右键→打开绕过 Gatekeeper，与
Windows 的 SmartScreen 提示类似）；Dock 图标不隐藏；交互层（麦克风权限
弹窗、辅助功能授权流程、热键手感）需真机最终确认。

## Project layout

See [DESIGN.md §3](DESIGN.md#3-目录结构).

## License

MIT (see [LICENSE](LICENSE)).

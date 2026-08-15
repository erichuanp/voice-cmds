# Voice-CMDs 设计文档

> 这份文档是 **程序设计细节书**。所有需求 / 决策 / 配置默认值都记录在此。
> 改需求 = 改这个文档（用户与 Claude 共同维护）。
> 未明确的项目走文档中的 "默认"。

---

## 1. 概述

按热键唤出屏幕底部居中的浮动小窗 → 流式语音识别 → 模糊匹配命令 → 执行脚本 / 打开应用 / 系统操作。
**专为 Windows 11 设计**。开发与运行环境：conda。

---

## 2. 技术栈

| 类别 | 选择 | 理由 |
|---|---|---|
| 语言 | Python 3.11 | STT/embedding 生态最齐 |
| 包管理 | **conda**（env: `voice-cmds`） | 用户指定 |
| UI | PySide6 (Qt 6) | 原生圆角/阴影/动画/半透明 |
| STT | sherpa-onnx `streaming-zipformer-bilingual-zh-en` | 中英双语流式，ONNX，5080 上 RTF < 0.05 |
| Embedding | `Xenova/bge-small-zh-v1.5`（ONNX，onnxruntime + tokenizers） | 直接 ONNX 推理，CLS 池化 + L2 归一化与 torch 版逐条一致（cosine = 1.0）；省掉 torch ~700MB 打包体积 |
| 音频 | `sounddevice` | 16kHz mono |
| 热键 | `keyboard` | 全局；同进程内可区分左右 Ctrl/Alt 但默认按键已改 |
| Win32 | `pywin32` | 系统命令（LockWorkStation 等）+ DwmSetWindowAttribute 通过 ctypes 调用 |
| 托盘 | `QSystemTrayIcon`（PySide6 自带） | 不引入 pystray |

**STT GPU**：sherpa-onnx 的 pip wheel 编译时未开 `-DSHERPA_ONNX_ENABLE_GPU=ON`，强制 CPU。CPU 推理已经够快（命令短，~100ms）。

---

## 3. 目录结构

```
voice-cmds/
├── DESIGN.md                 ← 本文档
├── README.md
├── environment.yml           ← conda env
├── main.py                   ← 入口；支持 --debug
├── voice_cmds/
│   ├── __init__.py
│   ├── app.py                ← QApplication、托盘、协调
│   ├── config.py             ← 配置 load/save
│   ├── hotkey.py             ← 全局热键
│   ├── monitor.py            ← 焦点显示器（按光标位置）
│   ├── audio.py              ← 麦克风采集
│   ├── stt.py                ← sherpa-onnx 流式封装
│   ├── matcher.py            ← 命令匹配（字面 → 拼音 → embedding）
│   ├── executor.py           ← 命令分发与执行
│   ├── logger.py             ← --debug 日志
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── system.py         ← 关机/重启/睡眠/注销/保持开机/锁屏/音量/媒体/...
│   │   └── apps.py           ← "打开 <触发词>"
│   └── ui/
│       ├── overlay.py        ← 录音浮窗（圆 → 胶囊动画）
│       ├── tray.py           ← 托盘菜单
│       └── settings.py       ← 设置窗口
├── scripts/                  ← 用户自定义 .bat / .ps1
│   └── del_des_png.bat       ← 示例（"吃饭" → 删桌面 png）
├── config/
│   ├── settings.json         ← 全局设置
│   ├── apps.json             ← "打开 XX" 触发词→路径
│   └── commands.json         ← 自定义命令触发词→脚本
│   # hot_words.json removed in 0.2 — replaced by embedding-only fallback
├── assets/
│   ├── tray.ico
│   ├── success.wav
│   └── error.wav
├── models/                   ← STT / embedding 缓存（gitignore）
└── logs/                     ← --debug 日志（gitignore）
```

---

## 4. 交互流程

### 4.1 热键

| 动作 | 按键 | 触发条件 |
|---|---|---|
| 启动录音 | **左 Ctrl + 右 Alt** | 全局 |
| 停止录音 | **右 Alt** | 仅在录音中 |
| 取消（不识别不执行） | **Esc** | 仅在录音中 |

> 单按 RAlt 仅在录音中拦截；非录音状态完全透传，不影响正常输入。
> **停止模式**（设置 → 通用）：`vad`（默认）/ `hotkey`。
> - `vad`：识别到文字后静音 ≥ `vad_silence_ms`（默认 500ms，≥200ms）自动结束识别；
> - `hotkey`：仅按停止键结束识别；
> - 结束识别后先展示识别结果 `result_text_ms`（默认 1000ms，≥0），**展示结束才执行命令**；
> - **两种模式下右 Alt / Esc 始终有效**。
> VAD 为自适应能量检测：静音判定阈值 = max(0.012, 3 × 环境底噪)，底噪按 EMA 缓慢跟踪；未识别到任何文字前不会自动停止。

### 4.2 浮窗状态机

```
[Hidden]
   │ Hotkey 触发
   ▼
[Recording-Idle 圆形 80×80 绿]
   │ 收到 partial（>0 字）
   ▼
[Recording-Capsule 高80 宽自适应 绿]  ←── 文字流式追加
   │ 停止键 / VAD 静音 0.5s / 达到 15 字
   ▼
[Processing 胶囊 + Loading 蒙层]
   │ 处理完成
   ▼
[Result 胶囊 + 识别文本]  ←── 绿=匹配成功 / 红=未匹配或失败，停留 result_text_ms（默认 1000ms）
   │ 展示结束后（此时才执行命令）
   ├──── 成功 ───▶ [Done-Success 圆形 + ✔ 绿] ─ 2s ─▶ [Hidden]
   ├──── 失败 ───▶ [Done-Error   圆形 + ✗ 红] ─ 2s ─▶ [Hidden]
   └──── Esc 取消 ────────────────────────────────▶ [Hidden]
```

### 4.3 浮窗外观

| 项 | 值 |
|---|---|
| 底色（录音/成功） | `#00C853` |
| 失败色 | `#E53935` |
| 圆形直径 | 80 px |
| 胶囊高度 | 80 px |
| 胶囊最大宽度 | 600 px |
| 文字字数上限 | **15 字符** — 达到立即触发停止 |
| 文字 | 多行允许（达到 max width 后换行） |
| 阴影 | DropShadow，blur 12，offset (0,4)，alpha 160（仿 Win11 elevation） |
| 窗口 | Frameless + Translucent + Tool（不抢焦点，不进任务栏） |
| 字体 | "Microsoft YaHei UI" 16pt 白色 |
| 动画 | QPropertyAnimation，缓动 `OutCubic`，width 200ms |

### 4.4 焦点显示器定位

```python
cursor = win32api.GetCursorPos()
monitor = next(m for m in EnumDisplayMonitors() if rect_contains(m.rcMonitor, cursor))
work = SystemParametersInfo(SPI_GETWORKAREA, monitor=monitor)  # 已避开任务栏
x = (work.left + work.right) / 2 - window.width() / 2
y = work.bottom - window.height() - bottom_offset_px  # 默认 bottom_offset_px = 20
```

---

## 5. STT

| 项 | 值 |
|---|---|
| 模型 | `sherpa-onnx-streaming-zipformer-bilingual-zh-en` |
| 采样率 | 16 kHz mono |
| 首次启动 | 自动下载到 `./models/`（带进度提示） |
| Provider | `cuda` 优先，失败回 `cpu` |
| 流式回调 | partial 通过 Qt signal 发 UI |
| 截断 | 识别到第 15 字符 → 立即停止 + 进入处理态 |

---

## 6. 命令匹配

三层（按顺序，全部在 `matcher.py`）：

1. **字面完全匹配** → 命中即执行
2. **拼音+声调 embedding**：文本先转 `拼音+声调`（`清空回收站 → qing1kong1hui2shou1zhan4`）再编码，余弦相似度 ≥ `pinyin_similarity_threshold`（默认 **0.88**）命中。STT 同音误识别因此能以极高相似度命中（“晴空挥手站 → qing2kong1hui1shou3zhan4”，sim ≈ 0.99；“锁屏/所评”sim = 1.0）
3. **原文 embedding 兜底**：≥ `embedding_similarity_threshold`（默认 0.85）命中，覆盖与读音无关的语义变体

> 所有触发词（系统+自定义+应用）在启动时预编码 **两套** 向量（拼音版 + 原文版），dispatch 时只需对单条输入做两次 encode + matmul。
> 拼音层阈值单独提高是因为无关拼音串的基线相似度约 0.7–0.8，必须与“原文层”阈值分开。
> “打开 X”路径只在与 X 相关的应用触发词里匹配（字面 → 拼音 → 原文）。
> 无命中 → 失败状态 + debug 日志记录原文 + 最佳分数。

---

## 7. 命令系统

### 7.1 内置系统命令（写死，触发词在 `commands/system.py`）

| 触发词 | 行为 | 状态 |
|---|---|---|
| 关机 | `shutdown /s /t 15` | ✅ |
| 重启 | `shutdown /r /t 15` | ✅ |
| 睡眠 | 15s 后 `rundll32 powrprof,SetSuspendState 0,1,0` | ✅ |
| 注销 | `shutdown /l /t 15` | ✅ |
| 保持开机 / 取消关机 | `shutdown /a` | ✅ |
| 锁屏 | `rundll32 user32.dll,LockWorkStation` | ✅ |
| 音量加 / 音量减 | VK_VOLUME_UP / DOWN | ✅ |
| 静音 | VK_VOLUME_MUTE | ✅ |
| 暂停 / 播放 | VK_MEDIA_PLAY_PAUSE | ✅ |
| 下一首 / 上一首 | VK_MEDIA_NEXT_TRACK / PREV_TRACK | ✅ |
| 关闭当前窗口 | 前台窗口发 WM_CLOSE | ✅ |
| 最小化全部 | Shell.Application MinimizeAll | ✅ |
| 打开资源管理器 | `explorer.exe` | ✅ |
| 清空回收站 | `SHEmptyRecycleBin` | ✅ |
| 定时关机（`<N>分钟后关机`） | `shutdown /s /t <N*60>`；支持阿拉伯/中文数字与「半小时」 | ✅ |

> 模式：`^(\d+|[零一二两三四五六七八九十半百]+)个?(分钟|小时|钟头)后?再?关机$`，上限 24 小时。
> **如果某条实现遇阻**：先注册触发词入口，但执行体替换为「占位提示音」（播 `assets/success.wav`）+ 日志记录 "TODO"，不阻塞整体上线。

### 7.2 用户自定义命令（`config/commands.json`）

```json
[
  {"trigger": "吃饭", "script": "scripts/del_des_png.bat", "args": []}
]
```

设置窗口提供 **添加 / 编辑 / 删除**。脚本路径相对项目根目录。

### 7.3 「打开」命令（`config/apps.json`）

特殊语法：`打开 <触发词>` —— 解析器拆出动词 `打开` 后查 apps.json。

```json
[
  {"trigger": "code", "path": "C:\\Users\\Administrator\\AppData\\Local\\Programs\\Microsoft VS Code\\Code.exe", "args": ["."]},
  {"trigger": "微信", "path": "C:\\Program Files\\Tencent\\WeChat\\WeChat.exe", "args": []}
]
```

设置 UI：「**添加新的打开**」按钮 → 弹窗（触发词 / 路径 / args，路径可文件选择器）。

---

## 8. 配置

### 8.1 `config/settings.json`

```json
{
  "hotkey": {
    "start": "left ctrl+right alt",
    "stop": "right alt",
    "cancel": "esc"
  },
  "stop_mode": "vad",
  "vad_silence_ms": 500,
  "max_chars": 15,
  "shutdown_delay_seconds": 15,
  "ui": {
    "color_idle": "#00C853",
    "color_error": "#E53935",
    "bottom_offset_px": 20,
    "max_capsule_width_px": 600,
    "circle_diameter_px": 80,
    "result_text_ms": 1000
  },
  "match": {
    "embedding_similarity_threshold": 0.85,
    "pinyin_similarity_threshold": 0.88
  },
  "sound": {
    "success_enabled": true,
    "error_enabled": true
  }
}
```

### 8.2 设置窗口（暴露的项）

- 修改三组热键
- **停止模式**（hotkey / vad）+ VAD 静音时长（默认 500ms，范围 200–5000ms）
- **识别结果展示时长**（默认 1000ms，范围 0–10000ms；0 = 识别后立即执行）
- 自定义命令 CRUD（`commands.json`）
- 「打开」映射 CRUD（`apps.json`）
- 提示音开关
- 开机自启动（debug 模式锁定）
- _未来扩展项位置_：用户提到"还在想"，预留菜单项

托盘菜单：**设置 / 帮助 / 重新加载配置 / 退出**。「帮助」弹窗列出全部内置命令、定时关机语法与「打开 X」可用触发词。

---

## 9. 日志

- 触发：`python main.py --debug`
- 路径：`./logs/voice-cmds-YYYYMMDD.log`（按日 rotate；示例：`voice-cmds-20260425.log`）
- 内容：
  - 启动 / 退出
  - 每次识别原文 + partial 数
  - 匹配过程（候选 + 分数 + 命中层级）
  - 执行命令 + 退出码
  - 异常堆栈
- 非 debug 模式：仅 stderr WARNING+

---

## 10. conda 环境（`environment.yml`）

```yaml
name: voice-cmds
channels:
  - conda-forge
dependencies:
  - python=3.11
  - pip
  - pip:
      - PySide6>=6.6
      - sounddevice
      - soundfile
      - numpy
      - sherpa-onnx
      - onnxruntime-gpu>=1.20  # CPU 用户可换 onnxruntime
      - tokenizers            # ONNX embedder 分词
      - pypinyin              # 拼音+声调匹配层
      - keyboard
      - pywin32
      - requests
      - tqdm
```

---

## 11. 待定 / 未来 TODO

- [x] ~~VAD 自动停止~~（0.1.0：静音 0.5s，设置可切换 hotkey/vad）
- [x] ~~开机自启注册~~（设置 → 通用）
- [x] ~~embedder 换直接 ONNX~~（0.1.0：onnxruntime + tokenizers，CLS 池化与 torch 逐条一致）
- [x] ~~托盘「帮助」菜单~~（0.1.0）
- [ ] 自定义提示音文件路径（替换默认 wav）
- [ ] 识别历史窗口（最近 50 条）
- [ ] 设置窗口"其他设置项"区域（占位）
- [ ] 多语言 UI（目前中文）
- [ ] 卸载脚本
- [ ] 提示音 wav 尚未打包进发布产物

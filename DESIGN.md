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
| Embedding | `Xenova/bge-small-zh-v1.5`（ONNX，onnxruntime + tokenizers） | 直接 ONNX 推理，CLS 池化 + L2 归一化与 torch 版逐条一致（cosine = 1.0）；省掉 torch ~700MB 打包体积。下载链：huggingface.co → modelscope.cn → hf-mirror.com |
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
│   ├── matcher.py            ← 命令匹配（定时语法 → 字面 → 拼音 → embedding）
│   ├── executor.py           ← 命令分发与执行
│   ├── scheduler.py          ← 定时任务调度（once/daily/delay/loop）
│   ├── logger.py             ← --debug 日志
│   ├── commands/
│   │   ├── __init__.py
│   │   ├── system.py         ← 关机/重启/睡眠/注销/保持开机/锁屏/音量/媒体/...
│   │   └── apps.py           ← "打开 <触发词>"
│   └── ui/
│       ├── overlay.py        ← 录音浮窗（圆 → 胶囊动画）
│       ├── tray.py           ← 托盘菜单
│       ├── settings.py       ← 设置窗口
│       └── tasks.py          ← 定时任务列表 + 添加/编辑窗口
├── scripts/                  ← 用户自定义 .bat / .ps1
│   └── .keep                  ← 占位（用户脚本放这里）
├── config/
│   ├── settings.json         ← 全局设置
│   ├── apps.json             ← "打开 XX" 触发词→路径
│   ├── commands.json         ← 自定义命令触发词→脚本
│   └── tasks.json            ← 定时任务（§7.3）
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
> **停止模式**（设置 → 通用）：`hotkey`（默认）/ `vad`。
> - `hotkey`：仅按停止键结束识别，识别后**立即执行**（结果展示时长恒为 0）；录音期间胶囊为**可编辑文本框**（语音 partial 插入光标位置，可与打字混排，录音时允许抢焦点，结束即归还）；
> - `vad`：识别到文字后静音 ≥ `vad_silence_ms`（默认 500ms，≥200ms）自动结束识别；识别后先展示结果 `result_text_ms`（默认 1000ms，≥0）再执行；**VAD 模式不支持编辑内容**；
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
   │ 停止键 / VAD 静音 0.5s
   ▼
[Processing 胶囊 + Loading 蒙层]
   │ 处理完成
   ▼
[Result 胶囊 + 识别文本]  ←── 绿=匹配成功 / 红=未匹配或失败；仅 VAD 模式停留 result_text_ms（默认 1000ms），热键模式恒 0（立即执行）
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
| 首次启动 | 自动下载到 `./models/`（带进度提示）。来源链：huggingface.co → hf-mirror.com，全部失败后回退 GitHub Releases 整包 tar.bz2 |
| Provider | `cuda` 优先，失败回 `cpu` |
| 流式回调 | partial 通过 Qt signal 发 UI |

---

## 6. 命令匹配

四层（按顺序，全部在 `matcher.py`）：

0. **“打开 X”路径**：只与 X 相关的应用触发词匹配（字面 → 拼音 → 原文）
1. **定时模式**：`<时间>后<命令>` → kind=`schedule`，交由调度器延迟执行（§7.3）。时间支持阿拉伯/中文数字（时 0–167，分/秒 0–59，超范围不匹配 → 红叉）
2. **字面完全匹配** → 命中即执行
3. **拼音+声调 embedding**：文本先转 `拼音+声调`（`清空回收站 → qing1kong1hui2shou1zhan4`）再编码，余弦相似度 ≥ `pinyin_similarity_threshold`（默认 **0.88**）命中。STT 同音误识别因此能以极高相似度命中（“晴空挥手站 → qing2kong1hui1shou3zhan4”，sim ≈ 0.99；“锁屏/所评”sim = 1.0）
4. **原文 embedding 兜底**：≥ `embedding_similarity_threshold`（默认 0.85）命中，覆盖与读音无关的语义变体

> 所有触发词（系统+自定义+应用）在启动时预编码 **两套** 向量（拼音版 + 原文版），dispatch 时只需对单条输入做两次 encode + matmul。
> 拼音层阈值单独提高是因为无关拼音串的基线相似度约 0.7–0.8，必须与“原文层”阈值分开。
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
| 清空回收站 | `SHEmptyRecycleBin` | ✅ |

> 「打开资源管理器」不再是内置命令——它作为**默认自带的「打开<触发词>」条目**存在于 `config/apps.json`（触发词 `资源管理器`，路径 `C:\Windows\explorer.exe`，无附加参数）。默认配置不含其它条目。

> **如果某条实现遇阻**：先注册触发词入口，执行体替换为占位实现 + 日志记录 "TODO"，不阻塞整体上线。

### 7.3 定时任务（`scheduler.py` + `ui/tasks.py`）

- **语音语法**：`<时间>后<命令>`，如 `3小时后打开资源管理器`、`一小时四十一分十二秒后锁屏`、`半小时后静音`、`十五秒后关机`。
  - 时间 = 若干 `数字+单位` 片段（单位：小时/钟头/时、分钟/分、秒；支持“半”“两”及中文数字 0–99/百）。
  - 范围校验：时 0–167、分 0–59、秒 0–59；超出范围或时间全 0 → 不匹配（红叉）。
  - 命令部分走正常匹配管线；**不再使用原生 `shutdown /t` 定时**。
- **任务类型**（持久化于 `config/tasks.json`，重启恢复）：
  | kind | 说明 | 图标 |
  |---|---|---|
  | `once` | 指定日期时刻执行一次 | 执行后 ✓/✗ |
  | `daily` | 每日重复：**首次执行在设置的日期时间点**；时间早于现在则立即按每天 HH:MM 生效（年月日失去意义） | 永不显示图标 |
  | `delay` | 添加起 N 秒后执行一次 | 执行后 ✓/✗ |
  | `loop` | 每 N 秒循环执行 | 永不显示图标 |
- **重启恢复规则**：
  - `once`/`delay` 已过期 → ✗，原因“程序未运行时已过期”
  - `daily` 不补跑；`loop` 下次触发 = `created + k*period`（k 为使时间落在未来的最小整数，即 `(now-created)%period` 推算）
- **托盘 → 定时任务**：任务列表窗口（右键行 → 编辑 / 删除；底部「删除全部已执行/失败任务」）。✗ 悬停显示失败原因。
- **添加/编辑对话框**（三态布局，切换时组件位置不变——两页内容置于 QStackedWidget 中固定尺寸）：
  - `口定时 口循环执行` + 「在添加该任务 [时 0–167]时 [分 0–59]分 [秒 0–59]秒 后执行」
  - `✔定时 口每日重复` + 「日期时间选择器 开始执行」
  - `✔定时 ✔每日重复` + 「日期时间选择器 开始重复执行」
  - 命令输入框 + 「立刻执行 / 保存 / 取消」（立刻执行只跑一次命令，不保存不关窗）。
- **托盘双击图标 → 打开设置窗口**。
- **“取消关机”**：`shutdown /a` 之外同时取消所有未执行且命令匹配 关机/重启 的定时任务。
- **语音添加反馈**：与执行命令共用 `result_text_ms` 等待——先显示识别原文 X ms，之后才注册任务；随后胶囊显示 `已定时：<时间>后 <命令>`（2s）+ 托盘气泡「已添加定时任务：<时间>后 <命令>」。

### 7.2 命令（自定义命令与「打开」已合并）

「打开 X」与自定义命令的实现相近，统一在**设置 → 命令**页管理，添加弹窗第一行为单选：

| 单选 | 说法规则 | 例（触发词填 `code`） | 存储 |
|---|---|---|---|
| **打开<触发词>** | 必须说「打开code」才执行 | `打开 code` | `apps.json`（key: `path`） |
| **触发词** | 必须说「code」才执行 | `code` | `commands.json`（key: `script`） |

- 单选只影响**触发词的说法规则**；下面的**路径**与**附加参数**两种模式共用。
- 路径选择器支持 `.bat / .cmd / .ps1 / .py / .exe / .lnk`（.lnk 经 cmd.exe 解析快捷方式）。
- 「打开<触发词>」的触发词支持 `;` / `；` 多别名：`code;vs` → 说「打开code」和「打开vs」打开同一个东西。
- 匹配器规则：app 条目**只**通过「打开 X」路径命中（不参与裸触发词字面/embedding 匹配）；custom 条目只在一般触发词集合中。
- **导入 / 导出**：命令页支持 .jsonl 备份与迁移，每行一个条目：`{"kind":"app","trigger":…,"path":…,"args":[]}` 或 `{"kind":"custom","trigger":…,"script":…,"args":[]}`；导入跳过无效行并提示数量。

```json
// config/apps.json
[{"trigger": "code;vs", "path": "C:\\...\\Code.exe", "args": []}]
// config/commands.json
[{"trigger": "吃饭", "script": "scripts/我的脚本.bat", "args": []}]
```

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
  "stop_mode": "hotkey",
  "vad_silence_ms": 500,
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
  }
}
```

### 8.2 设置窗口（暴露的项）

- **热键录制**（§4.1）：点击输入框后按键盘/鼠标键直接录制（开始录音两个键，结束/取消一个键；支持右键，不支持左键；Esc 取消录制）；每行带「重置」按钮恢复默认。保存前用 keyboard 库校验格式
- **结束方式**（默认 hotkey）+ 静音时长（默认 500ms，范围 200–5000ms）
- **结果展示时长**（仅 VAD 模式生效，默认 1000ms，范围 0–10000ms；**热键模式恒 0 = 识别后立即执行**，该输入框在热键模式下禁用）
- **命令页（合并）**：统一管理「打开 X」与自定义命令（§7.2，添加弹窗内单选决定触发词规则），支持 jsonl 导入/导出
- 开机自启动（debug 模式锁定）
- **关于页**：软件版本、开源声明（MIT + 署名与邮件通知要求）、Releases / 作者主页 / 建议邮箱链接；「检查更新」按钮查询 GitHub Releases 最新版（一致 → 绿色“已经是最新版本”；不一致 → 出现“更新到最新版本”按钮，自动更新未实现前按钮置灰）

托盘菜单：**设置 / 帮助 / 定时任务 / 退出**。「帮助」为独立窗口，列出当前热键、结束方式、全部内置命令、定时任务语法、打开/自定义命令与匹配规则；「定时任务」打开任务列表（§7.3）。

### 8.3 统一错误弹窗（`ui/errorbox.py` + `errors.py`）

所有致命/重要错误（启动下载失败、模型加载失败、热键注册失败等）走同一个弹窗组件，与其余对话框共用样式：

- **错误分类**（自动按 traceback 关键字归类）：网络 / 下载失败、模型 / 程序加载失败、未知错误；
- **引导语**：每类配一句下一步建议（如“下载失败，请尝试改变网络环境后重试”）；
- **详细信息**：默认折叠，可展开；文本框只读可选，另有「复制」按钮一键复制全文。

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
- [ ] 识别历史窗口（最近 50 条）
- [ ] 设置窗口"其他设置项"区域（占位）
- [ ] 多语言 UI（目前中文）
- [ ] **自动更新**（差分下载：manifest + HTTP Range 按需拉取变更文件，启动时应用）
- [x] ~~卸载脚本~~（0.5.2：Inno Setup 卸载器，清理自启动与运行数据）

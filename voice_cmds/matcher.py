"""Multi-layer command matcher: literal → toned-pinyin embedding → raw embedding.

The embedding layers encode **pinyin with tone numbers** instead of the raw
Chinese text, so STT homophone errors match their intended trigger with very
high similarity:

    清空回收站 -> qing1kong1hui2shou1zhan4
    晴空挥手站 -> qing2kong1hui1shou3zhan4   (sim ≈ 0.99)

A raw-text embedding pass remains as the final fallback for semantically
similar utterances that share no phonetics.

`prepare_embedder(status_cb)` is a free function so the Bootstrap worker can
download/load the model with splash status visible — without constructing the
matcher (which needs a Config too).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("voice_cmds.matcher")

EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

# "<时间>后<命令>": 3小时后打开资源管理器 / 一小时四十一分十二秒后锁屏 /
# 半小时后静音 / 十五秒后关机. Ranges: 时 0-167, 分 0-59, 秒 0-59.
_TIME_TOKEN_RE = re.compile(
    r"(\d+|[零一二两三四五六七八九十百]+|半)\s*(?:个)?\s*(小时|钟头|时|分钟|分|秒)"
)


def _to_toned_pinyin(text: str) -> str:
    """'清空回收站' -> 'qing1kong1hui2shou1zhan4'.

    Non-Chinese characters pass through unchanged ('code' stays 'code'),
    so English app triggers keep working. Falls back to the lowered input
    on any error so the embedding layers degrade to raw text safely.
    """
    try:
        import pypinyin

        return "".join(
            pypinyin.lazy_pinyin(text, style=pypinyin.Style.TONE3)
        ).lower()
    except Exception:
        return text.lower()


def _cn_num_to_int(s: str) -> int | None:
    """'十' -> 10, '三十分钟' -> 30. Returns None for '半' or invalid input."""
    if s == "半":
        return None
    total = 0
    cur = 0
    for ch in s:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:  # pragma: no cover - regex already constrains input
            return None
    return total + cur


def _num_to_int(raw: str) -> float | None:
    if raw.isdigit():
        return float(raw)
    if raw == "半":
        return 0.5
    v = _cn_num_to_int(raw)
    return float(v) if v is not None else None


_UNIT_SECONDS = {"小时": 3600, "钟头": 3600, "时": 3600, "分钟": 60, "分": 60, "秒": 1}


def _parse_time_tokens(prefix: str) -> int | None:
    """Parse '3小时30分15秒' / '一小时零五分' -> total seconds, or None.

    The whole prefix must be consumed. Optional 零 / 个 fillers between
    tokens are tolerated. Range checks: 时 0-167, 分 0-59, 秒 0-59.
    """
    pos = 0
    h = m = s = 0.0
    found = False
    n = len(prefix)
    while pos < n:
        if prefix[pos].isspace() or prefix[pos] in "零个":
            pos += 1
            continue
        tok = _TIME_TOKEN_RE.match(prefix, pos)
        if not tok:
            return None
        value = _num_to_int(tok.group(1))
        if value is None:
            return None
        unit = tok.group(2)
        if unit in ("小时", "钟头", "时"):
            h += value
        elif unit in ("分钟", "分"):
            m += value
        else:
            s += value
        pos = tok.end()
        found = True
    if not found:
        return None
    if not (0 <= h <= 167) or not (0 <= m <= 59) or not (0 <= s <= 59):
        return None
    total = int(h * 3600 + m * 60 + s)
    return total if total > 0 else None


def format_delay(seconds: int) -> str:
    """167*3600-style total seconds -> '3小时30分15秒' (zero units omitted)."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}小时")
    if m:
        parts.append(f"{m}分")
    if s:
        parts.append(f"{s}秒")
    return "".join(parts) or "0秒"


def _split_aliases(trigger: str) -> list[str]:
    """'code;vs' / 'code；vs' -> ['code', 'vs'] (app trigger aliases)."""
    return [a.strip() for a in re.split(r"[;；]", trigger) if a.strip()]


def _parse_timed_command(text: str) -> tuple[int, str] | None:
    """'<时间>后<命令>' -> (delay_seconds, command), or None if not a match.

    Tries the leftmost '后' whose prefix parses fully as time tokens and
    whose suffix is a non-empty command.
    """
    idx = text.find("后")
    while idx >= 0:
        prefix, suffix = text[:idx], text[idx + 1:]
        cmd = suffix.strip()
        if cmd:
            seconds = _parse_time_tokens(prefix)
            if seconds is not None:
                return seconds, cmd
        idx = text.find("后", idx + 1)
    return None


@dataclass
class CommandSpec:
    trigger: str
    kind: str  # 'system' | 'app' | 'custom'
    payload: dict


@dataclass
class MatchResult:
    command: CommandSpec
    layer: str
    score: float
    arg: str = ""


def prepare_embedder(status_cb: Optional[Callable[[str], None]] = None):
    """Load the BGE-small-zh ONNX embedder (see voice_cmds/embedder.py).

    onnxruntime + tokenizers reproduce the torch sentence-transformers
    output exactly (CLS pooling + L2 normalization), without shipping the
    ~700 MB torch stack in the bundle.
    """
    from .embedder import prepare_embedder as _prepare_onnx

    return _prepare_onnx(status_cb=status_cb)


class CommandMatcher:
    """Resolves recognized text → CommandSpec."""

    OPEN_VERB = "打开"

    def __init__(self, config, embedder) -> None:
        self.config = config
        self.embedder = embedder
        # populated by _rebuild()
        self.specs: list[CommandSpec] = []
        self.app_triggers: dict[str, dict] = {}
        self._all_trigger_list: list[str] = []
        self._all_pinyin_embeddings = None   # numpy ndarray, normalized
        self._all_embeddings = None          # raw-text embeddings, normalized
        self._app_trigger_list: list[str] = []
        self._app_pinyin_embeddings = None
        self._app_embeddings = None
        self._rebuild()

    # --- trigger registry -------------------------------------------------
    def _rebuild(self) -> None:
        self.specs = []
        # Built-in system commands
        from .commands.system import SYSTEM_COMMANDS
        for trigger, fn_name in SYSTEM_COMMANDS:
            self.specs.append(CommandSpec(trigger, "system", {"fn": fn_name}))
        # Custom user commands
        for entry in self.config.commands:
            self.specs.append(
                CommandSpec(
                    entry["trigger"],
                    "custom",
                    {"script": entry["script"], "args": entry.get("args", [])},
                )
            )
        # Apps: trigger may list aliases separated by ';' / '；' —
        # "code;vs" registers 打开code and 打开vs to the same entry.
        # App entries are matched ONLY through the "打开 X" path.
        self.app_triggers = {}
        for entry in self.config.apps:
            for alias in _split_aliases(entry.get("trigger", "")):
                self.app_triggers[alias] = entry

        # Pre-encode all triggers: system + custom (apps live only in the
        # "打开 X" path, so they are not in the general set).
        self._all_trigger_list = [s.trigger for s in self.specs]
        if self._all_trigger_list:
            pinyin_list = [_to_toned_pinyin(t) for t in self._all_trigger_list]
            self._all_pinyin_embeddings = self.embedder.encode(
                pinyin_list, normalize_embeddings=True
            )
            self._all_embeddings = self.embedder.encode(
                self._all_trigger_list, normalize_embeddings=True
            )
        else:
            self._all_pinyin_embeddings = None
            self._all_embeddings = None

        # App triggers separately for the "打开 X" path
        self._app_trigger_list = list(self.app_triggers)
        if self._app_trigger_list:
            pinyin_list = [_to_toned_pinyin(t) for t in self._app_trigger_list]
            self._app_pinyin_embeddings = self.embedder.encode(
                pinyin_list, normalize_embeddings=True
            )
            self._app_embeddings = self.embedder.encode(
                self._app_trigger_list, normalize_embeddings=True
            )
        else:
            self._app_pinyin_embeddings = None
            self._app_embeddings = None

        logger.info(
            "Matcher ready: %d specs (built-in+custom), %d apps",
            len(self.specs), len(self.app_triggers),
        )

    # --- matching ---------------------------------------------------------
    @staticmethod
    def _normalize(text: str) -> str:
        text = re.sub(r"[\s,。.，！!？?、:：]+", " ", text)
        return text.strip()

    def match(self, text: str) -> Optional[MatchResult]:
        if not text:
            return None
        text = self._normalize(text)
        logger.debug("Matching normalized: %r", text)

        # 0. "打开 X" special path — match X against apps only
        if text.startswith(self.OPEN_VERB):
            arg = text[len(self.OPEN_VERB):].strip()
            r = self._match_app(arg)
            if r:
                return r
            # The 打开 prefix is part of the trigger intent: after the app
            # path fails, only triggers that themselves start with 打开
            # (e.g. a user-defined custom trigger like 打开空调) stay
            # eligible — a bare custom trigger like "code" must not answer
            # to "打开 code".
            return self._match_open_prefixed(text)

        # 1. Timed task pattern: "<时间>后<命令>" — e.g. "三小时后打开资源管理器",
        #    "3小时30分15秒后锁屏". Returns a schedule command (executed by the
        #    scheduler after the delay) rather than a native shutdown.
        timed = _parse_timed_command(text)
        if timed is not None:
            delay_s, cmd = timed
            logger.info("Timed command parsed: %s -> %d s later: %r", text, delay_s, cmd)
            return MatchResult(
                CommandSpec(
                    f"{format_delay(delay_s)}后 {cmd}",
                    "schedule",
                    {"delay_seconds": delay_s, "command": cmd},
                ),
                "pattern",
                1.0,
                arg=str(delay_s),
            )

        # 2. Literal full match against any trigger (system or custom)
        for s in self.specs:
            if text == s.trigger:
                return MatchResult(s, "literal", 1.0)

        # 3. Toned-pinyin embedding, then raw-text embedding fallback
        threshold = self.config.settings["match"]["embedding_similarity_threshold"]
        return self._match_embedding(text, threshold)

    def _match_open_prefixed(self, text: str) -> Optional[MatchResult]:
        """Literal/pinyin/raw matching restricted to triggers starting with 打开."""
        candidates = [s for s in self.specs if s.trigger.startswith(self.OPEN_VERB)]
        if not candidates:
            return None
        for s in candidates:
            if text == s.trigger:
                return MatchResult(s, "literal", 1.0)
        pinyin_threshold = self.config.settings["match"].get(
            "pinyin_similarity_threshold", 0.88
        )
        raw_threshold = self.config.settings["match"]["embedding_similarity_threshold"]
        trigs = [s.trigger for s in candidates]
        q = self.embedder.encode(
            [_to_toned_pinyin(text)], normalize_embeddings=True
        )[0]
        embs = self.embedder.encode(
            [_to_toned_pinyin(t) for t in trigs], normalize_embeddings=True
        )
        sims = embs @ q
        idx = int(sims.argmax())
        if float(sims[idx]) >= pinyin_threshold:
            return MatchResult(candidates[idx], "pinyin", float(sims[idx]))
        q = self.embedder.encode([text], normalize_embeddings=True)[0]
        embs = self.embedder.encode(trigs, normalize_embeddings=True)
        sims = embs @ q
        idx = int(sims.argmax())
        if float(sims[idx]) >= raw_threshold:
            return MatchResult(candidates[idx], "embedding", float(sims[idx]))
        return None

    def _match_app(self, arg: str) -> Optional[MatchResult]:
        if not arg or not self.app_triggers:
            return None
        # Literal first
        if arg in self.app_triggers:
            entry = self.app_triggers[arg]
            return MatchResult(
                CommandSpec(arg, "app", entry), "literal", 1.0, arg=arg
            )
        raw_threshold = self.config.settings["match"]["embedding_similarity_threshold"]
        pinyin_threshold = self.config.settings["match"].get(
            "pinyin_similarity_threshold", 0.88
        )
        # Toned-pinyin embedding among app triggers only
        if self._app_pinyin_embeddings is not None:
            q = self.embedder.encode(
                [_to_toned_pinyin(arg)], normalize_embeddings=True
            )[0]
            sims = self._app_pinyin_embeddings @ q
            best_idx = int(sims.argmax())
            if float(sims[best_idx]) >= pinyin_threshold:
                trig = self._app_trigger_list[best_idx]
                entry = self.app_triggers[trig]
                return MatchResult(
                    CommandSpec(trig, "app", entry),
                    "pinyin",
                    float(sims[best_idx]),
                    arg=trig,
                )
        # Raw-text embedding fallback
        if self._app_embeddings is not None:
            q = self.embedder.encode([arg], normalize_embeddings=True)[0]
            sims = self._app_embeddings @ q
            best_idx = int(sims.argmax())
            if float(sims[best_idx]) >= raw_threshold:
                trig = self._app_trigger_list[best_idx]
                entry = self.app_triggers[trig]
                return MatchResult(
                    CommandSpec(trig, "app", entry),
                    "embedding",
                    float(sims[best_idx]),
                    arg=trig,
                )
        return None

    def _match_embedding(self, text: str, threshold: float) -> Optional[MatchResult]:
        if self._all_pinyin_embeddings is None:
            return None
        pinyin_threshold = self.config.settings["match"].get(
            "pinyin_similarity_threshold", 0.88
        )
        best_overall = 0.0
        # Toned-pinyin layer (homophone / STT error tolerant). Unrelated
        # pinyin strings still score ~0.7-0.8, so this layer needs a higher
        # bar than the raw-text layer to avoid garbage matches.
        q = self.embedder.encode(
            [_to_toned_pinyin(text)], normalize_embeddings=True
        )[0]
        sims = self._all_pinyin_embeddings @ q
        best_idx = int(sims.argmax())
        score = float(sims[best_idx])
        best_overall = score
        if score >= pinyin_threshold:
            return self._result_for_trigger(best_idx, "pinyin", score)

        # Raw-text layer (semantic fallback)
        if self._all_embeddings is not None:
            q = self.embedder.encode([text], normalize_embeddings=True)[0]
            sims = self._all_embeddings @ q
            best_idx = int(sims.argmax())
            score = float(sims[best_idx])
            best_overall = max(best_overall, score)
            if score >= threshold:
                return self._result_for_trigger(best_idx, "embedding", score)

        logger.info(
            "No embedding match for %r (best=%.3f < %.2f)",
            text, best_overall, threshold,
        )
        return None

    def _result_for_trigger(self, idx: int, layer: str, score: float) -> Optional[MatchResult]:
        trig = self._all_trigger_list[idx]
        for s in self.specs:
            if s.trigger == trig:
                return MatchResult(s, layer, score)
        return None

    # --- help text --------------------------------------------------------
    def help_text(self) -> str:
        s = self.config.settings
        hotkeys = s["hotkey"]
        vad_ms = int(s.get("vad_silence_ms", 500))
        result_ms = int(s.get("ui", {}).get("result_text_ms", 1000))
        stop_mode = s.get("stop_mode", "vad")
        py_thr = float(s["match"].get("pinyin_similarity_threshold", 0.88))
        em_thr = float(s["match"]["embedding_similarity_threshold"])
        if stop_mode == "vad":
            stop_desc = f"静音达到 {vad_ms} ms 自动结束识别"
        else:
            stop_desc = f"按下结束键 {hotkeys['stop']} 结束识别"
        parts = [
            "<h3>使用方式</h3>",
            "<ul>",
            f"<li>开始录音：<b>{hotkeys['start']}</b></li>",
            f"<li>结束识别：{stop_desc}</li>",
            f"<li>取消（仅录音中）：<b>{hotkeys['cancel']}</b></li>",
            f"<li>识别结果在胶囊中展示 {result_ms} ms，之后才执行命令</li>",
            "</ul>",
            "<h3>内置命令</h3>",
            "<p>" + "、".join(sorted({sp.trigger for sp in self.specs if sp.kind == "system"})) + "</p>",
            "<h3>定时任务</h3>",
            "<ul>",
            "<li>语音：&lt;时间&gt;后&lt;命令&gt;，例：3小时后打开资源管理器、半小时后静音、十五秒后关机</li>",
            "<li>时间范围：小时 0–167，分钟 0–59，秒 0–59；支持阿拉伯与中文数字</li>",
            "<li>托盘 → 定时任务：查看、编辑、删除任务；手动添加支持指定时刻、每日重复、循环执行</li>",
            "<li>「取消关机」同时取消未执行的关机/重启定时任务</li>",
            "</ul>",
        ]
        apps = [a.get("trigger", "") for a in self.config.apps]
        if apps:
            parts += ["<h3>打开应用</h3>", "<p>打开&lt;触发词&gt;：" + "、".join(apps) + "</p>"]
        customs = [c.get("trigger", "") for c in self.config.commands]
        if customs:
            parts += ["<h3>自定义命令</h3>", "<p>" + "、".join(customs) + "</p>"]
        parts += [
            "<h3>匹配方式</h3>",
            "<p>按顺序：定时模式 → 打开 X → 字面匹配 → 拼音+声调相似度（≥"
            f"{py_thr:.2f}）→ 语义相似度（≥{em_thr:.2f}）。识别与命令不完全一致也能命中。</p>",
        ]
        return "".join(parts)

    def reload(self) -> None:
        self._rebuild()

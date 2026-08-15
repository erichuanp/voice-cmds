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

# "10分钟后关机" / "十分钟后关机" / "半小时后关机" / "一小时后关机"
_TIMED_SHUTDOWN_RE = re.compile(
    r"^(?P<num>\d+|[零一二两三四五六七八九十半百]+)"
    r"(?:个)?\s*(?P<unit>分钟|小时|钟头)"
    r"(?:后)?\s*(?:再)?\s*关机$"
)

_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_UNIT_SECONDS = {"分钟": 60, "小时": 3600, "钟头": 3600}


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
    """'十' -> 10, '三十分钟' -> 30, '半' -> None (caller handles halves)."""
    if s == "半":
        return None  # caller resolves half of the unit
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


def _parse_timed_shutdown(text: str) -> int | None:
    """Return shutdown delay in seconds, or None if the text isn't a timed shutdown."""
    m = _TIMED_SHUTDOWN_RE.match(text)
    if not m:
        return None
    num_raw, unit = m.group("num"), m.group("unit")
    unit_s = _UNIT_SECONDS[unit]
    if num_raw.isdigit():
        value = int(num_raw)
    else:
        if num_raw == "半":
            value = 0.5
        else:
            cn = _cn_num_to_int(num_raw)
            if cn is None:
                return None
            value = float(cn)
    seconds = int(value * unit_s)
    if seconds <= 0 or seconds > 24 * 3600:
        return None
    return seconds


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
        # Apps
        self.app_triggers = {entry["trigger"]: entry for entry in self.config.apps}

        # Pre-encode all triggers (system + custom + apps): toned pinyin + raw
        self._all_trigger_list = [s.trigger for s in self.specs] + list(self.app_triggers)
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

        # 1. Timed shutdown pattern: "10分钟后关机" / "半小时后关机" / …
        seconds = _parse_timed_shutdown(text)
        if seconds is not None:
            logger.info("Timed shutdown parsed: %s -> %d s", text, seconds)
            return MatchResult(
                CommandSpec(
                    f"{seconds // 60}分钟后关机",
                    "system",
                    {"fn": "delayed_shutdown", "seconds": seconds},
                ),
                "pattern",
                1.0,
                arg=str(seconds),
            )

        # 2. Literal full match against any trigger (commands or apps)
        for s in self.specs:
            if text == s.trigger:
                return MatchResult(s, "literal", 1.0)
        if text in self.app_triggers:
            entry = self.app_triggers[text]
            return MatchResult(
                CommandSpec(text, "app", entry), "literal", 1.0, arg=text
            )

        # 3. Toned-pinyin embedding, then raw-text embedding fallback
        threshold = self.config.settings["match"]["embedding_similarity_threshold"]
        return self._match_embedding(text, threshold)

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
        if trig in self.app_triggers:
            entry = self.app_triggers[trig]
            return MatchResult(
                CommandSpec(trig, "app", entry), layer, score, arg=trig
            )
        for s in self.specs:
            if s.trigger == trig:
                return MatchResult(s, layer, score)
        return None

    # --- help text --------------------------------------------------------
    def help_text(self) -> str:
        hotkeys = self.config.settings["hotkey"]
        stop_mode = self.config.settings.get("stop_mode", "hotkey")
        lines = [
            "<h3>voice-cmds 使用帮助</h3>",
            f"<b>开始录音：</b>{hotkeys['start']}（说一句命令）",
            f"<b>结束：</b>{hotkeys['stop']}"
            + ("　|　静音 0.5 秒自动执行" if stop_mode == "vad" else ""),
            f"<b>取消：</b>{hotkeys['cancel']}（仅录音中）",
            "<hr/>",
            "<b>内置命令：</b>",
        ]
        system_triggers = sorted({s.trigger for s in self.specs if s.kind == "system"})
        lines.append("、".join(system_triggers))
        lines += [
            "<br/><b>定时关机：</b>“10分钟后关机”、“半小时后关机”、“一小时后关机”（单位：分钟/小时/钟头）",
            "<br/><b>取消关机：</b>说“取消关机”或“保持开机”",
        ]
        if self.config.apps:
            app_triggers = sorted(e["trigger"] for e in self.config.apps)
            lines.append(f"<br/><b>打开应用：</b>“打开 X”（X 可以是：{'、'.join(app_triggers)}）")
        if self.config.commands:
            custom_triggers = sorted(e["trigger"] for e in self.config.commands)
            lines.append(f"<br/><b>自定义命令：</b>{'、'.join(custom_triggers)}")
        lines += [
            "<hr/>",
            "<i>识别结果与命令不完全一致也没关系：程序会按“字面 → 拼音+声调 → 语义”逐层模糊匹配。</i>",
        ]
        return "".join(lines)

    def reload(self) -> None:
        self._rebuild()

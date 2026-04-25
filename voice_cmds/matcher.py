"""Two-layer command matcher: literal → embedding.

Embedder is preloaded at app startup and passed in. All trigger embeddings
are pre-computed in `_rebuild()` so dispatch is just one encode + matmul.

`prepare_embedder(status_cb)` is a free function so the Bootstrap worker can
download the model with splash status visible — without constructing the
matcher (which needs a Config too).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("voice_cmds.matcher")

EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


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
    if status_cb:
        status_cb(f"正在加载语义匹配模型 ({EMBED_MODEL_NAME})…")
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBED_MODEL_NAME)


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
        self._all_embeddings = None  # numpy ndarray, normalized
        self._app_trigger_list: list[str] = []
        self._app_embeddings = None
        self._rebuild()

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

        # Pre-encode all triggers (system + custom + apps) for non-"打开" path
        self._all_trigger_list = [s.trigger for s in self.specs] + list(self.app_triggers)
        if self._all_trigger_list:
            self._all_embeddings = self.embedder.encode(
                self._all_trigger_list, normalize_embeddings=True
            )
        else:
            self._all_embeddings = None

        # Pre-encode app triggers separately for the "打开 X" path
        self._app_trigger_list = list(self.app_triggers)
        if self._app_trigger_list:
            self._app_embeddings = self.embedder.encode(
                self._app_trigger_list, normalize_embeddings=True
            )
        else:
            self._app_embeddings = None

        logger.info(
            "Matcher ready: %d specs (built-in+custom), %d apps",
            len(self.specs), len(self.app_triggers),
        )

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

        # 1. Literal full match against any trigger (commands or apps)
        for s in self.specs:
            if text == s.trigger:
                return MatchResult(s, "literal", 1.0)
        if text in self.app_triggers:
            entry = self.app_triggers[text]
            return MatchResult(
                CommandSpec(text, "app", entry), "literal", 1.0, arg=text
            )

        # 2. Embedding fallback against full set
        threshold = self.config.settings["match"]["embedding_similarity_threshold"]
        return self._match_embedding(text, threshold)

    def _match_app(self, arg: str) -> Optional[MatchResult]:
        if not arg or self._app_embeddings is None:
            return None
        # Literal first
        if arg in self.app_triggers:
            entry = self.app_triggers[arg]
            return MatchResult(
                CommandSpec(arg, "app", entry), "literal", 1.0, arg=arg
            )
        # Embedding among app triggers only
        threshold = self.config.settings["match"]["embedding_similarity_threshold"]
        q = self.embedder.encode([arg], normalize_embeddings=True)[0]
        sims = self._app_embeddings @ q
        best_idx = int(sims.argmax())
        if float(sims[best_idx]) < threshold:
            return None
        trig = self._app_trigger_list[best_idx]
        entry = self.app_triggers[trig]
        return MatchResult(
            CommandSpec(trig, "app", entry),
            "embedding",
            float(sims[best_idx]),
            arg=trig,
        )

    def _match_embedding(self, text: str, threshold: float) -> Optional[MatchResult]:
        if self._all_embeddings is None:
            return None
        q = self.embedder.encode([text], normalize_embeddings=True)[0]
        sims = self._all_embeddings @ q
        best_idx = int(sims.argmax())
        score = float(sims[best_idx])
        if score < threshold:
            logger.info("No embedding match (best=%.3f < %.2f)", score, threshold)
            return None
        trig = self._all_trigger_list[best_idx]
        if trig in self.app_triggers:
            entry = self.app_triggers[trig]
            return MatchResult(
                CommandSpec(trig, "app", entry), "embedding", score, arg=trig
            )
        for s in self.specs:
            if s.trigger == trig:
                return MatchResult(s, "embedding", score)
        return None

    def reload(self) -> None:
        self._rebuild()

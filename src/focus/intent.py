from __future__ import annotations

import re
import unicodedata
from enum import Enum


class FocusIntent(str, Enum):
    START = "start"
    STOP = "stop"
    STATUS = "status"
    CANCEL = "cancel"


_KEYWORDS: tuple[tuple[FocusIntent, tuple[str, ...]], ...] = (
    (FocusIntent.CANCEL, ("取消专注", "不要统计了", "取消统计")),
    (FocusIntent.STOP, ("结束专注", "停止统计", "生成总结")),
    (FocusIntent.STATUS, ("专注情况", "统计到哪了", "现在怎么样")),
    (FocusIntent.START, ("开始专注", "开始统计", "进入专注")),
)


def normalize_zh_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[\s\W_]+", "", normalized, flags=re.UNICODE)


def match_focus_intent(text: str) -> FocusIntent | None:
    normalized = normalize_zh_text(text)
    for intent, keywords in _KEYWORDS:
        if any(keyword in normalized for keyword in keywords):
            return intent
    return None

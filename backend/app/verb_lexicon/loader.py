"""verb_lexicon 加载器（spec §16.3）。

词表与代码分离：``lexicon.json`` 是持续维护资产（架构模块表），
命中判定由 validate/verbs.py 实现（ASCII 词做边界匹配，中文词包含匹配），
本模块只负责加载与常量暴露。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_LEXICON_PATH = Path(__file__).resolve().parent / "lexicon.json"


@lru_cache(maxsize=1)
def _load() -> dict:
    with _LEXICON_PATH.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("version"), int):
        raise ValueError("lexicon.json 结构非法：缺少整型 version")
    for key in ("dominant", "participatory"):
        values = data.get(key)
        if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
            raise ValueError(f"lexicon.json 结构非法：{key} 必须是字符串数组")
    return data


def lexicon_version() -> int:
    """词表版本（写入判定 detail，供回归对比）。"""
    return int(_load()["version"])


#: 版本常量（启动即可读，避免调用方到处调用函数）
LEXICON_VERSION = lexicon_version()


def dominant_verbs() -> list[str]:
    """主导级动词（命中 → 出处必须 individual 且已 gate1 确认）。"""
    return list(_load()["dominant"])


def participatory_verbs() -> list[str]:
    """参与级动词（不主张个人主导，任何归因都可用）。"""
    return list(_load()["participatory"])

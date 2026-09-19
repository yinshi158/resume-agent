"""anomaly 输入/输出模型（纯函数层，不依赖数据库）。

为保持模块单向依赖（ard/0008），输入为普通 dict 结构（由 fact_store
组装），规则内部按需容错读取，不做严格模型校验。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FactForRules:
    """一条事实源的规则输入视图。"""

    fact_id: str
    section: str
    raw_quote: str
    payload: dict = field(default_factory=dict)  # fields / date_interpretations / entities
    quote_verified: bool = True                  # quote 回验是否通过（≥1 处匹配）
    quote_match_count: int = 1                   # quote 在原文中的匹配数（>1 = 歧义，不锚定）

    def date_interpretations(self) -> list[dict]:
        value = self.payload.get("date_interpretations") or []
        return [item for item in value if isinstance(item, dict)]

    def entities(self) -> dict:
        value = self.payload.get("entities") or {}
        return value if isinstance(value, dict) else {}

    def numbers(self) -> list[dict]:
        value = self.entities().get("numbers") or []
        return [item for item in value if isinstance(item, dict)]

    def fields(self) -> dict:
        value = self.payload.get("fields") or {}
        return value if isinstance(value, dict) else {}


@dataclass
class FlagDraft:
    """待入库的异象标记。"""

    fact_id: str
    rule: str
    detail: dict[str, Any]

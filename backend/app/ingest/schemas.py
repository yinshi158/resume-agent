"""抽取 Schema（LLM 输出，spec §5）。

日期归一化在本层完成（结果进 ``date_interpretations``，不进原文层，
ard/0001）；歧义日期 ``normalized=None`` 且 ``ambiguous=True``，
由 anomaly 标记、gate1 人工确认。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Section = Literal["summary", "work", "project", "education", "skill", "other"]
Attribution = Literal["individual", "team", "mixed", "unknown"]


class DateInterpretation(BaseModel):
    """一条日期表达式的解释（原始 ↔ 归一化）。"""

    raw: str                      # "19.3-21.6"
    normalized: str | None = None  # "2019-03 至 2021-06"；歧义时为 None
    ambiguous: bool = False
    candidates: list[str] = Field(default_factory=list)  # 歧义候选解释，gate1 展示


class NumberEntity(BaseModel):
    """实体数字（anomaly 复用：百分比边界、金额量级）。"""

    value: float
    unit: str = ""


class Entities(BaseModel):
    numbers: list[NumberEntity] = Field(default_factory=list)
    orgs: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)


class ExtractedFact(BaseModel):
    """一条事实源条目（quote 为唯一锚点，回验用）。"""

    section: Section
    quote: str                                     # 原文逐字（回验用，唯一锚点）
    fields: dict = Field(default_factory=dict)     # 按 section 的结构化字段
    date_interpretations: list[DateInterpretation] = Field(default_factory=list)
    attribution: Attribution = "unknown"           # 默认站保守一侧（ard/0002）
    entities: Entities = Field(default_factory=Entities)


class ExtractionResult(BaseModel):
    facts: list[ExtractedFact] = Field(default_factory=list)

"""validate 输入/输出模型（spec §15.2 改写输出、§16 四层校验）。

- ``RewrittenSentence`` / ``DerivedNumber``：改写 LLM 输出契约（151 数据
  模型级契约，ard/0005）——**逐句带锚点**，无出处的句子程序拒收；
- ``ValidationFact``：校验层使用的事实源视图（由调用方从 DB 行组装，
  validate 不反向 import 上游模块，ard/0008）；
- ``LayerVerdict``：单层判定（layer × pass/fail + detail），
  detail 必须含人类可读 ``message``（gate2 直接展示，spec §16.2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Section = Literal["summary", "work", "project", "education", "skill", "other"]
Layer = Literal["L0", "verb", "L1", "L2"]
Verdict = Literal["pass", "fail"]


# ---------------------------------------------------------------------------
# 改写输出（LLM 输出，逐句带锚点；spec §15.2）
# ---------------------------------------------------------------------------

class DerivedNumber(BaseModel):
    """衍生数字：稿面呈现值 + 白名单可求值 formula + 参与推导的出处。"""

    value: str                    # "30%"；区间形式两端点分列（如 "30%~35%"）
    formula: str                  # "(910000-700000)/700000"；白名单求值见 §16.4
    source_ids: list[str] = Field(default_factory=list)  # 参与推导的事实源条目


class RewrittenSentence(BaseModel):
    """一句改写（无 source_fact_ids 的句子程序拒收，ard/0003）。"""

    section: Section
    text: str
    source_fact_ids: list[str] = Field(min_length=1)     # 非空
    derived_numbers: list[DerivedNumber] = Field(default_factory=list)
    verbs: list[str] = Field(default_factory=list)       # 模型自报，仅参考（§16.3）
    requirement_ids: list[str] = Field(default_factory=list)


class RewriteResult(BaseModel):
    """改写 LLM 顶层输出。"""

    sentences: list[RewrittenSentence] = Field(default_factory=list)


class L2Candidate(BaseModel):
    """L2 蕴含判定的 LLM 输出（spec §16.5）。

    映射不上（supported_fact_ids 为空或含未声明 id）即标红 fail。
    """

    entailed: bool = False
    supported_fact_ids: list[str] = Field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# 校验层事实源视图与判定
# ---------------------------------------------------------------------------

@dataclass
class ValidationFact:
    """校验层事实源视图（fact_store 行 → 本结构，组装在 rewrite/gate2 io）。"""

    fact_id: str
    section: str
    raw_quote: str
    searchable_text: str                       # 原文 + 结构化字段 + 实体文本
    numbers: list[tuple[float, str]] = field(default_factory=list)  # (value, unit)
    attribution: str = "unknown"
    attribution_confirmed: bool = False
    confirmed: bool = False


@dataclass
class LayerVerdict:
    """单层判定结果（持久化到 sentence_validations）。"""

    layer: Layer
    verdict: Verdict
    detail: dict                               # {"message": 人类可读一句话, ...参数}

    @property
    def passed(self) -> bool:
        return self.verdict == "pass"

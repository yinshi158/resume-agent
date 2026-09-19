"""diagnose 输入/输出模型（spec §12.4/§12.5）。

LLM 输出层只做候选生成；status 的最终写定在程序验证之后
（evidence.py），模型声明不被信任（ard/0001 贯穿原则）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

Priority = Literal["must", "preferred"]
ReqStatus = Literal["direct", "nearest", "gap"]


# ---------------------------------------------------------------------------
# LLM 输出（候选，须经程序验证）
# ---------------------------------------------------------------------------

class RequirementItem(BaseModel):
    """JD 要求清单条目（LLM 拆解输出，spec §12.4）。"""

    priority: Priority
    text: str
    keywords: list[str]  # 含中英文别名


class EvidenceCandidate(BaseModel):
    """单条要求的举证候选（LLM 输出；找不到证据必须全 None）。"""

    req_index: int
    quote: str | None = None           # 逐字证据；无证据必须为 None（禁止转述）
    fact_id: str | None = None         # 声明的出处（仅参考，程序复验）
    nearest_fact_id: str | None = None  # 无直接证据时声明的最接近条目


# ---------------------------------------------------------------------------
# 程序验证后的写定结果
# ---------------------------------------------------------------------------

@dataclass
class FactView:
    """举证/最近邻使用的事实源视图（来自 fact_store，confirmed 条目）。"""

    fact_id: str
    section: str
    raw_quote: str
    searchable_text: str  # raw_quote + 结构化字段文本（确定性检索用）
    # 入库时 quote 唯一定位（span >= 0）；降级候选只用可定位条目（保证捞回可用）
    locatable: bool = True


@dataclass
class VerifiedRequirement:
    """程序验证后的要求条目（入库与报告的依据）。"""

    req_index: int
    priority: Priority
    text: str
    keywords: list[str]
    status: ReqStatus
    quote: str | None = None          # status=direct 时的逐字证据（已唯一定位）
    fact_id: str | None = None        # 证据/最近邻所在条目
    nearest_note: str | None = None   # gap/nearest 时的人类可读说明
    user_revived: bool = False


# ---------------------------------------------------------------------------
# 报告（spec §12.5）
# ---------------------------------------------------------------------------

@dataclass
class ReportSummary:
    must_total: int
    must_direct: int
    must_nearest: int
    must_gap: int
    preferred_total: int
    preferred_direct: int
    worth_applying: bool
    notice: str | None = None


@dataclass
class DiagnosisReport:
    summary: ReportSummary
    requirements: list[VerifiedRequirement] = field(default_factory=list)
    gaps: list[VerifiedRequirement] = field(default_factory=list)  # M3 分母修正用

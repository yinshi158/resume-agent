"""rewrite 的领域模型（spec §15.3 目标清单与失败分流）。

LLM 输出句集模型在 ``validate.schemas``（validate 是本模块的调用契约，
rewrite → validate 单向，ard/0008）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RewriteTarget:
    """改写目标条目（分母修正后，spec §15.3）。

    目标清单 = diagnosis requirements 中 ``status ∈ {direct, nearest}``
    或 ``user_revived=1`` 的条目；纯 gap（未捞回）一律剔除——
    注定无证据的条目留在分母 = 制造擦边压力（ard/0004）。
    """

    requirement_id: str
    req_index: int
    priority: str                        # must | preferred
    text: str
    status: str                          # direct | nearest（user_revived 亦为 direct）
    quote: str | None = None             # direct 时的逐字证据
    fact_id: str | None = None           # 证据/最近邻所在条目
    keywords: list[str] = field(default_factory=list)


@dataclass
class RewriteEscalation:
    """升级清单条目（随产物返回，不进入改写与重试）。"""

    kind: str                            # requirement_gap | l2_unentailed
    message: str
    requirement_id: str | None = None
    sentence_id: str | None = None
    detail: dict = field(default_factory=dict)

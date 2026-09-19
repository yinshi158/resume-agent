"""validate：四层校验（spec §16，ard/0003 举证式校验）。

- L0：确定性实体比对（偷渡数字 / 无出处技能词），全稿扫描；
- verb：归因动词检查（verb_lexicon，主导级动词要求 individual 出处）；
- L1：formula 白名单求值 + 衍生数字重算（禁用 eval）；
- L2：LLM 蕴含判定（唯一 LLM 层，"声明的出处是否蕴含这句话"）。

依赖方向：fact_store → validate（读取事实源视图由调用方组装，
validate 不反向依赖上游模块）；由 rewrite 调用（ard/0008）。
"""

from . import core, io, l0, l1, l2, mock_rules, prompts, verbs
from .schemas import (
    DerivedNumber,
    L2Candidate,
    LayerVerdict,
    RewrittenSentence,
    RewriteResult,
    ValidationFact,
)

__all__ = [
    "DerivedNumber",
    "L2Candidate",
    "LayerVerdict",
    "RewrittenSentence",
    "RewriteResult",
    "ValidationFact",
    "core",
    "io",
    "l0",
    "l1",
    "l2",
    "mock_rules",
    "prompts",
    "verbs",
]

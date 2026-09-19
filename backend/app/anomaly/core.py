"""anomaly 计算入口（一次计算，随事实源版本失效，spec §6）。"""

from __future__ import annotations

from . import rules
from .schemas import FactForRules, FlagDraft

# 单 fact 规则（顺序即展示顺序）
_FACT_RULES = (
    ("date_order", rules.date_order),
    ("date_overlap", None),  # 跨 fact，占位见下
    ("date_ambiguous", rules.date_ambiguous),
    ("percent_bound", rules.percent_bound),
    ("missing_field", rules.missing_field),
)


def compute_flags(
    facts: list[FactForRules],
    today: tuple[int, int] | None = None,
) -> list[FlagDraft]:
    """对一次抽取的全部条目计算异象标记。

    :param today: 供 date_overlap 判定"至今"的当前年月（测试注入用）。
    """
    drafts: list[FlagDraft] = []
    for fact in facts:
        for name, rule_fn in _FACT_RULES:
            if rule_fn is None:
                continue
            for detail in rule_fn(fact):
                drafts.append(FlagDraft(fact_id=fact.fact_id, rule=name, detail=detail))

    for fact_id, detail in rules.date_overlap(facts, today=today):
        drafts.append(FlagDraft(fact_id=fact_id, rule="date_overlap", detail=detail))

    for fact_id, detail in rules.amount_magnitude(facts):
        drafts.append(FlagDraft(fact_id=fact_id, rule="amount_magnitude", detail=detail))

    return drafts

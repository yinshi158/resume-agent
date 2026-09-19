"""validate 四层编排（spec §16，ard/0003 举证式校验）。

层序固定：L0（偷渡数字/实体）→ verb（归因动词）→ L1（衍生数字重算）
→ L2（LLM 蕴含）。前三层纯确定性；L2 是唯一 LLM 层。

分流（spec §15.3，ard/0004）：
- ``REPAIRABLE_LAYERS``（L0 / verb / L1）失败 = 可修复 → 带反馈重写 ≤2 轮；
- L2 失败 = 改写中新暴露的硬性缺口 → 升级回诊断报告，不重试
  （``detail.error=true`` 的调用/解析失败除外——那是链路故障，不算缺口）。
"""

from __future__ import annotations

from . import l0, l1, l2, verbs
from .l0 import build_entity_vocab
from .schemas import LayerVerdict, RewrittenSentence, ValidationFact

#: 可修复层：允许进入重写轮次（带判定 detail 进 prompt）
REPAIRABLE_LAYERS: tuple[str, ...] = ("L0", "verb", "L1")


def run_deterministic_layers(
    sentence: RewrittenSentence,
    *,
    all_facts: list[ValidationFact],
    facts_by_id: dict[str, ValidationFact],
    entity_vocab: list[str],
) -> list[LayerVerdict]:
    """L0 → verb → L1（不调 LLM；编辑重校验与首轮校验共用）。"""
    return [
        l0.check_l0(
            sentence.text,
            all_facts=all_facts,
            derived_numbers=sentence.derived_numbers,
            entity_vocab=entity_vocab,
        ),
        verbs.check_verb(
            sentence.text,
            source_fact_ids=sentence.source_fact_ids,
            facts_by_id=facts_by_id,
        ),
        l1.check_l1(
            sentence.text,
            source_fact_ids=sentence.source_fact_ids,
            facts_by_id=facts_by_id,
            derived_numbers=sentence.derived_numbers,
        ),
    ]


def run_l2(
    sentence: RewrittenSentence,
    *,
    facts_by_id: dict[str, ValidationFact],
) -> LayerVerdict:
    """L2 判定（1 次 LLM 调用）。"""
    return l2.check_l2(
        sentence.text,
        source_fact_ids=sentence.source_fact_ids,
        facts_by_id=facts_by_id,
    )


def run_all_layers(
    sentence: RewrittenSentence,
    *,
    all_facts: list[ValidationFact],
    facts_by_id: dict[str, ValidationFact],
    entity_vocab: list[str],
) -> list[LayerVerdict]:
    """四层完整校验（L0 / verb / L1 / L2），按层序返回。"""
    verdicts = run_deterministic_layers(
        sentence,
        all_facts=all_facts,
        facts_by_id=facts_by_id,
        entity_vocab=entity_vocab,
    )
    verdicts.append(run_l2(sentence, facts_by_id=facts_by_id))
    return verdicts


def repairable_failures(verdicts: list[LayerVerdict]) -> list[LayerVerdict]:
    """可修复失败（L0/verb/L1 fail）——进入重写轮次。"""
    return [v for v in verdicts if not v.passed and v.layer in REPAIRABLE_LAYERS]


def l2_failures(verdicts: list[LayerVerdict]) -> list[LayerVerdict]:
    """L2 失败中属于"真实缺口"的部分（排除调用/解析故障）。"""
    return [
        v
        for v in verdicts
        if v.layer == "L2" and not v.passed and not v.detail.get("error")
    ]


__all__ = [
    "REPAIRABLE_LAYERS",
    "build_entity_vocab",
    "l2_failures",
    "repairable_failures",
    "run_all_layers",
    "run_deterministic_layers",
    "run_l2",
]

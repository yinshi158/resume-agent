"""归因动词检查（确定性，先于 L2；spec §16.3，ard/0002 保守默认）。

规则（程序扫描句文本，模型自报 verbs 仅参考）：

- 命中 **主导级** 动词（verb_lexicon）→ 要求该句全部 source_fact_ids
  的 attribution == 'individual' 且已 gate1 确认（confirmed +
  attribution_confirmed）；任一不满足即 fail——team/mixed/unknown
  只许参与级动词（PRD 3.4：团队成果不会被写成我主导的）；
- 未命中主导级动词 → pass（参与级动词不主张个人主导，任何归因可用）；
- 词表漏词 = 漏报方向、过宽 = 误报方向；初版宁窄勿宽（R3），
  漏报由 L2 与人工兜底。
"""

from __future__ import annotations

import re

from ..verb_lexicon import LEXICON_VERSION, dominant_verbs, participatory_verbs
from .schemas import LayerVerdict, ValidationFact

_ASCII_KW_RE = r"(?<![a-z0-9+#]){}(?![a-z0-9+#])"


def _hit(text: str, word: str) -> bool:
    """ASCII 词做边界匹配，中文词直接包含。"""
    if not word:
        return False
    if word.isascii():
        return re.search(_ASCII_KW_RE.format(re.escape(word)), text, re.IGNORECASE) is not None
    return word in text


def scan_verbs(text: str) -> tuple[list[str], list[str]]:
    """程序扫描：返回 (主导级命中, 参与级命中)，保持词表顺序。"""
    dominant = [word for word in dominant_verbs() if _hit(text, word)]
    participatory = [word for word in participatory_verbs() if _hit(text, word)]
    return dominant, participatory


def _attribution_note(fact: ValidationFact) -> str:
    label = {"individual": "个人", "team": "团队", "mixed": "个人+团队", "unknown": "未表态"}
    text = label.get(fact.attribution, fact.attribution)
    if fact.attribution != "individual" and not fact.attribution_confirmed:
        text += "（归因未确认）"
    return text


def check_verb(
    sentence_text: str,
    *,
    source_fact_ids: list[str],
    facts_by_id: dict[str, ValidationFact],
) -> LayerVerdict:
    """归因动词层判定。"""
    dominant, participatory = scan_verbs(sentence_text)
    base_detail = {
        "dominant_hits": dominant,
        "participatory_hits": participatory,
        "lexicon_version": LEXICON_VERSION,
    }
    if not dominant:
        return LayerVerdict(
            layer="verb",
            verdict="pass",
            detail={**base_detail, "message": "未命中主导级动词，归因无冲突"},
        )

    problems: list[str] = []
    for fact_id in source_fact_ids:
        fact = facts_by_id.get(fact_id)
        if fact is None:
            problems.append(f"出处条目 {fact_id} 缺失，无法核对归因")
            continue
        if not fact.confirmed:
            problems.append(f"出处「{fact.raw_quote[:24]}…」尚未完成 gate1 校对")
            continue
        if fact.attribution != "individual" or not fact.attribution_confirmed:
            problems.append(
                f"出处「{fact.raw_quote[:24]}…」归因={_attribution_note(fact)}，"
                f"不支持主导级表述"
            )

    if problems:
        word_list = "、".join(f"「{w}」" for w in dominant)
        return LayerVerdict(
            layer="verb",
            verdict="fail",
            detail={
                **base_detail,
                "message": f"主导级动词{word_list}要求全部出处为个人主导（individual）：{problems[0]}",
                "problems": problems,
            },
        )
    return LayerVerdict(
        layer="verb",
        verdict="pass",
        detail={
            **base_detail,
            "message": f"主导级动词{'、'.join(dominant)}：全部出处归因为个人主导且已确认",
        },
    )

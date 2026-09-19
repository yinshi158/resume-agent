"""L2 LLM 蕴含判定（唯一 LLM 层，spec §16.5，ard/0003）。

- 判定"声明的出处是否蕴含这句话"（借壳加料检测），不评价措辞；
- 输出：句子→条目 ID 映射 + 不蕴含点说明；映射不上即标红 fail；
- 调用失败 / 输出非法 → 保守标 fail 并置 ``detail.error=true``
  （gate2 仍可人工处置；误差不静默通过）。
"""

from __future__ import annotations

from pydantic import ValidationError

from ..platform import llm
from . import mock_rules, prompts
from .schemas import L2Candidate, LayerVerdict, ValidationFact


def check_l2(
    sentence_text: str,
    *,
    source_fact_ids: list[str],
    facts_by_id: dict[str, ValidationFact],
) -> LayerVerdict:
    """单句 L2 判定（含 1 次 LLM 调用）。"""
    facts = [facts_by_id[fid] for fid in source_fact_ids if fid in facts_by_id]
    if not facts:
        return LayerVerdict(
            layer="L2",
            verdict="fail",
            detail={"message": "声明出处条目缺失，无法判定蕴含", "error": True},
        )

    messages = prompts.build_l2_messages(sentence_text, facts)
    try:
        text, is_mock = llm.chat_json(messages, mock_provider=mock_rules.build_l2_response)
    except RuntimeError as exc:
        return LayerVerdict(
            layer="L2",
            verdict="fail",
            detail={"message": f"L2 判定调用失败：{exc}", "error": True, "mock": False},
        )

    try:
        candidate = L2Candidate.model_validate(llm.parse_json(text))
    except (ValueError, ValidationError) as exc:
        return LayerVerdict(
            layer="L2",
            verdict="fail",
            detail={
                "message": f"L2 判定输出无法解析（保守标红）：{exc}",
                "error": True,
                "mock": is_mock,
            },
        )

    declared = set(source_fact_ids)
    supported = [fid for fid in candidate.supported_fact_ids if fid in declared]
    unknown = [fid for fid in candidate.supported_fact_ids if fid not in declared]
    passed = bool(candidate.entailed and supported and not unknown)

    if unknown:
        message = f"L2：判定映射到未声明的出处 {unknown}，视为映射不上（保守标红）"
    elif not supported:
        message = "L2：句子的断言未映射到任何声明出处（映射不上即标红）"
    elif candidate.entailed:
        message = "L2：声明出处蕴含该句"
    else:
        message = f"L2：声明出处不蕴含该句——{candidate.reason or '未给出原因'}"

    return LayerVerdict(
        layer="L2",
        verdict="pass" if passed else "fail",
        detail={
            "message": message,
            "entailed": candidate.entailed,
            "supported_fact_ids": supported,
            "unknown_fact_ids": unknown,
            "reason": candidate.reason,
            "mock": is_mock,
        },
    )

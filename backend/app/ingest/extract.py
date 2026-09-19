"""LLM 抽取 + 逐条 schema 校验（spec §4）。

- 抽取要求每字段带 quote（prompt 约束，prompts.py）；
- 逐条 pydantic 校验：非法条目跳过并记 warning（保守——不把不确定的
  数据塞进事实源；缺失由 anomaly 标记引导人工补）；
- quote → span 的程序回验在 fact_store 落库时执行（模块职责见 ard/0008）。
"""

from __future__ import annotations

from pydantic import ValidationError

from ..platform import llm
from . import mock_rules, prompts
from .schemas import ExtractedFact


def run_extraction(
    canonical: str, markdown_view: str
) -> tuple[list[ExtractedFact], bool, list[str]]:
    """调用 LLM（mock 模式走规则抽取）并校验结果。

    :returns: ``(facts, is_mock, warnings)``
    """
    messages = prompts.build_messages(canonical, markdown_view)
    text, is_mock = llm.chat_json(messages, mock_provider=mock_rules.build_mock_response)
    data = llm.parse_json(text)

    raw_facts = data.get("facts")
    if not isinstance(raw_facts, list):
        raise ValueError("LLM 返回缺少 facts 列表")

    facts: list[ExtractedFact] = []
    warnings: list[str] = []
    for index, item in enumerate(raw_facts, start=1):
        if not isinstance(item, dict):
            warnings.append(f"第 {index} 条抽取结果不是 JSON 对象，已跳过")
            continue
        try:
            facts.append(ExtractedFact.model_validate(item))
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            warnings.append(
                f"第 {index} 条抽取结果不合法（{first.get('loc')}: {first.get('msg')}），已跳过"
            )
    return facts, is_mock, warnings

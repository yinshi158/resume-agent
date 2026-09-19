"""JD 文本消毒 + LLM（或 mock 规则）拆要求清单（spec §12.3）。

- JD 与简历输入走同一 sanitize 模块（spec §9：双向防注入）；
- 逐条 pydantic 校验，非法条目跳过记 warning——与 ingest 同款保守
  （不把不确定的数据塞进下游）；引用方向依赖 ingest/sanitize 属 spec
  §12.1 明文允许（"复用 ingest/sanitize"）；
- 拆不出任何有效要求 → 拒绝诊断（宁可报错，不给空报告误导用户）。
"""

from __future__ import annotations

from pydantic import ValidationError

from ..ingest import sanitize
from ..platform import llm
from . import mock_rules, prompts
from .schemas import RequirementItem


def parse_requirements(
    jd_text: str,
) -> tuple[str, list[RequirementItem], bool, list[str]]:
    """消毒 JD 并拆解要求清单。

    :returns: ``(消毒后的 JD 原文, 要求清单, is_mock, warnings)``。
    :raises ValueError: JD 为空 / LLM 返回结构非法 / 未能拆出有效要求。
    :raises RuntimeError: LLM 调用失败（网络/鉴权/超时等）。
    """
    cleaned, warnings = sanitize.clean(jd_text)
    if not cleaned.strip():
        raise ValueError("JD 内容为空，请粘贴目标岗位的职位描述后再诊断")

    messages = prompts.build_parse_messages(cleaned)
    text, is_mock = llm.chat_json(
        messages, mock_provider=mock_rules.build_requirements_response
    )
    data = llm.parse_json(text)

    raw_items = data.get("requirements")
    if not isinstance(raw_items, list):
        raise ValueError("LLM 返回缺少 requirements 列表")

    items: list[RequirementItem] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            warnings.append(f"第 {index} 条要求不是 JSON 对象，已跳过")
            continue
        try:
            items.append(RequirementItem.model_validate(item))
        except ValidationError as exc:
            first = exc.errors()[0] if exc.errors() else {}
            warnings.append(
                f"第 {index} 条要求不合法（{first.get('loc')}: {first.get('msg')}），已跳过"
            )
    if not items:
        raise ValueError("未能从 JD 中拆出有效要求（可能内容过短或格式异常）")
    return cleaned, items, is_mock, warnings

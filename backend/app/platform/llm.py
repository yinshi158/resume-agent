"""LiteLLM 客户端 + mock 切换（ard/0007）。

- 真实模式：统一走 LiteLLM 网关；模型名不带 ``/`` 前缀时按
  OpenAI 兼容端点处理（``openai/<model>`` + 用户配置的 base_url），
  默认 DeepSeek，可切 GLM/Kimi/Claude 等任意兼容端点。
- mock 模式（无 key 或显式 mock）：**不发起任何网络请求**，由调用方注入
  ``mock_provider`` 生成响应（M1 为 ingest 的规则抽取），保证全链路可跑通；
  界面与响应头须标示 mock（spec §4），mock 解析质量不得冒充真实水平。

本模块位于平台层，禁止反向依赖任何业务模块（ard/0008）：mock 的具体
规则由调用方（ingest）通过参数注入。
"""

from __future__ import annotations

import json
import re
from typing import Callable, Sequence

from . import config

# mock 模式下未注入 provider 时的兜底响应
_EMPTY_MOCK_RESPONSE = json.dumps({"facts": []}, ensure_ascii=False)

MockProvider = Callable[[Sequence[dict]], str]


def chat_json(
    messages: Sequence[dict],
    *,
    mock_provider: MockProvider | None = None,
    temperature: float = 0.0,
) -> tuple[str, bool]:
    """请求 LLM 并返回原始响应文本。

    :returns: ``(response_text, is_mock)``；``is_mock=True`` 时响应来自
        mock provider，调用方须在响应头/界面标示 mock。
    :raises RuntimeError: 真实调用失败（网络/鉴权/超时等）。
    """
    if config.is_mock():
        if mock_provider is not None:
            return mock_provider(messages), True
        return _EMPTY_MOCK_RESPONSE, True

    settings = config.get_settings()
    model = settings["llm_model"].strip() or "deepseek-chat"
    if "/" not in model:
        # 用户配置的是 OpenAI 兼容端点，模型名按 openai/ 前缀交给 LiteLLM
        model = f"openai/{model}"

    try:
        import litellm  # 懒加载：mock 模式无需安装/初始化 litellm

        resp = litellm.completion(
            model=model,
            api_key=settings["llm_api_key"],
            api_base=settings["llm_base_url"],
            messages=list(messages),
            temperature=temperature,
            response_format={"type": "json_object"},
            timeout=180,
        )
        content = resp.choices[0].message.content or ""
        return content, False
    except Exception as exc:  # noqa: BLE001 —— 统一转换为业务层可处理的错误
        raise RuntimeError(f"LLM 调用失败：{exc}") from exc


_CODE_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


def parse_json(text: str) -> dict:
    """解析 LLM 返回的 JSON 对象（容忍 ```json 围栏与前后噪声）。"""
    cleaned = _CODE_FENCE_RE.sub("", text).strip()
    if not cleaned:
        raise ValueError("LLM 返回为空")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # 兜底：截取第一个 { 到最后一个 } 之间的内容
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"LLM 返回不是有效 JSON：{cleaned[:200]}") from None
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM 返回的顶层结构必须是 JSON 对象")
    return data

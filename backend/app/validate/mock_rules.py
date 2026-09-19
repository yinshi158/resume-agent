"""mock 模式的 L2 判定规则（无 key 全链路可跑通，ard/0007）。

确定性规则：句子与任一出处原文存在 ≥6 字连续公共片段 → 视为蕴含，
support 列出这些出处；否则不蕴含（映射不上即标红，spec §16.5）。
**质量不代表真实 LLM 水平**，界面与响应头须标示 mock。

阈值 6 的依据：mock 改写句以"逐字取自出处"为主（长公共片段必然存在），
而 mock 的"无支撑例句"与任何出处的最长公共片段 < 6 字——两个方向的
演示行为都确定；真实环境走 LLM，不依赖此阈值。
"""

from __future__ import annotations

import json
import re
from typing import Sequence

from . import prompts

_FACT_LINE_RE = re.compile(r"^- \[(?P<fid>[0-9a-fA-F]+)\] section=\S+ \| (?P<quote>.+)$")

_MIN_OVERLAP = 6


def _longest_common_substring_len(a: str, b: str) -> int:
    """最长公共连续子串长度（DP，句子级短文本可接受）。"""
    if not a or not b:
        return 0
    previous = [0] * (len(b) + 1)
    best = 0
    for i in range(1, len(a) + 1):
        current = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                current[j] = previous[j - 1] + 1
                best = max(best, current[j])
        previous = current
    return best


def build_l2_response(messages: Sequence[dict]) -> str:
    """mock provider：按"≥6 字连续公共片段"判定蕴含（与真实 LLM 同结构）。"""
    sentence = prompts.extract_sentence(messages)
    supported: list[str] = []
    for line in prompts.extract_fact_lines(messages):
        match = _FACT_LINE_RE.match(line)
        if match is None:
            continue
        if _longest_common_substring_len(sentence, match.group("quote")) >= _MIN_OVERLAP:
            supported.append(match.group("fid"))
    entailed = bool(supported)
    payload = {
        "entailed": entailed,
        "supported_fact_ids": supported,
        "reason": (
            "mock 规则判定："
            + ("与声明出处存在 ≥6 字连续公共片段" if entailed else "与声明出处无足够逐字重叠")
        ),
    }
    return json.dumps(payload, ensure_ascii=False)

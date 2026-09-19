"""mock 模式的改写规则（无 key 全链路可跑通，ard/0007；spec §15.4）。

- 首轮：每条目标要求取 evidence 出处的原文作为句子（"逐字引用式改写"），
  外加两个演示句（固定判定，模板见 mock_rewrite.json）：
  ① 带哨兵数字的句子 → L0 拦截 → 进入重写轮 → 修复为出处原文；
  ② 无支撑断言句 → L2 判否 → 升级路径 + gate2 强标红；
- 重写轮：按反馈中的判定详情做确定性修复（L0 → 回归出处原文；
  verb → 主导级动词替换为"参与"；L1 → 去掉 derived_numbers）；
- **质量不代表真实 LLM 水平**，界面与响应头须标示 mock。
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from ..validate.l0 import extract_numbers
from ..validate.verbs import scan_verbs
from . import prompts

_TEMPLATE_PATH = Path(__file__).resolve().parent / "mock_rewrite.json"

_TARGET_LINE_RE = re.compile(
    r"^- \[(?P<rid>[^\]]+)\] priority=(?P<priority>\S+) status=(?P<status>\S+) "
    r"evidence=(?P<fid>\S+) \| (?P<text>.+)$"
)
_FACT_LINE_RE = re.compile(
    r"^- \[(?P<fid>[^\]]+)\] section=(?P<section>\S+) attribution=(?P<attr>\S+) "
    r"\| (?P<quote>.+?)(?: \| fields=.*)?$"
)


@lru_cache(maxsize=1)
def _template() -> dict:
    with _TEMPLATE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 首轮：目标句 + 演示句
# ---------------------------------------------------------------------------

def _sentinel_percent(quotes: list[str]) -> str:
    """确定性哨兵百分比：保证不在任何出处（quote）的百分比集合中。"""
    used = {
        token.value
        for quote in quotes
        for token in extract_numbers(quote)
        if token.unit == "%"
    }
    value = 3.7
    step = 1.9
    for _ in range(60):
        if not any(abs(value - u) < 1e-9 for u in used):
            return f"{value:.1f}%"
        value += step
    return "97.3%"  # 理论不可达：60 次迭代足够覆盖任意真实语料


def _first_round_sentences(messages: Sequence[dict]) -> list[dict]:
    facts: dict[str, dict] = {}
    for line in prompts.extract_fact_lines(messages):
        match = _FACT_LINE_RE.match(line)
        if match:
            facts[match.group("fid")] = {
                "section": match.group("section"),
                "quote": match.group("quote").strip(),
            }

    sentences: list[dict] = []
    for line in prompts.extract_target_lines(messages):
        match = _TARGET_LINE_RE.match(line)
        if not match:
            continue
        fid = match.group("fid")
        fact = facts.get(fid)
        if fact is None:
            continue  # 目标无可用出处（或出处行缺失）→ 不硬写
        sentences.append(
            {
                "section": fact["section"],
                "text": fact["quote"],
                "source_fact_ids": [fid],
                "derived_numbers": [],
                "verbs": [],
                "requirement_ids": [match.group("rid")],
            }
        )

    cap = int(_template().get("max_target_sentences", 12))
    sentences = sentences[:cap]

    # 演示句：需要一个出处（取事实源第一条）
    if facts:
        first_id, first_fact = next(iter(facts.items()))
        suffix = _template()["fabricated_suffix_template"].format(
            percent=_sentinel_percent([f["quote"] for f in facts.values()])
        )
        sentences.append(
            {
                "section": first_fact["section"],
                "text": first_fact["quote"] + suffix,
                "source_fact_ids": [first_id],
                "derived_numbers": [],
                "verbs": [],
                "requirement_ids": [],
            }
        )
        sentences.append(
            {
                "section": "summary",
                "text": _template()["unbacked_claim"],
                "source_fact_ids": [first_id],
                "derived_numbers": [],
                "verbs": [],
                "requirement_ids": [],
            }
        )
    return sentences


# ---------------------------------------------------------------------------
# 重写轮：按判定详情确定性修复
# ---------------------------------------------------------------------------

def _fact_quotes(messages: Sequence[dict]) -> dict[str, str]:
    quotes: dict[str, str] = {}
    for line in prompts.extract_fact_lines(messages):
        match = _FACT_LINE_RE.match(line)
        if match:
            quotes[match.group("fid")] = match.group("quote").strip()
    return quotes


def _repair_sentence(item: dict, fact_quotes: dict[str, str]) -> dict | None:
    text = item["text"]
    layers = {issue.split("：", 1)[0] for issue in (item.get("issues") or []) if "：" in issue}

    if "L0" in layers and item.get("fact_ids"):
        # L0 失败（数字/实体无出处）→ 回归出处原文（最保守的修复）
        first_id = item["fact_ids"][0]
        if first_id in fact_quotes:
            text = fact_quotes[first_id]

    if "verb" in layers:
        # 归因冲突 → 主导级动词替换为参与级表述
        for word in scan_verbs(text)[0]:
            text = text.replace(word, "参与")

    if not item.get("fact_ids"):
        return None  # 无出处可锚定 → 不产出（避免非法句）
    return {
        "section": item.get("section") or "other",
        "text": text,
        "source_fact_ids": item["fact_ids"],
        "derived_numbers": [],  # L1 相关失败一律去掉衍生数字
        "verbs": [],
        "requirement_ids": item.get("requirement_ids") or [],
    }


def _retry_sentences(messages: Sequence[dict]) -> list[dict]:
    fact_quotes = _fact_quotes(messages)
    out: list[dict] = []
    for item in prompts.extract_feedback_items(messages):
        repaired = _repair_sentence(item, fact_quotes)
        if repaired is not None:
            out.append(repaired)
    return out


def build_rewrite_response(messages: Sequence[dict]) -> str:
    """mock provider：首轮 = 目标句 + 演示句；重写轮 = 确定性修复。"""
    feedback = prompts.extract_feedback_items(messages)
    sentences = _retry_sentences(messages) if feedback else _first_round_sentences(messages)
    return json.dumps({"sentences": sentences}, ensure_ascii=False)

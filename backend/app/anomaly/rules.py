"""异象规则（纯函数，spec §6）。

每条规则输出 ``detail`` 字典：必须含人类可读一句话说明（gate1 直接展示）
+ 机器可读参数。规则宁可漏报不可误报——异象是给人看的引导，泛滥会淹没用户。

| rule             | 判定                                       |
| ---------------- | ------------------------------------------ |
| date_order       | 同段经历结束日期早于开始日期                    |
| date_overlap     | 多段 work 时间区间重叠 >3 个月                  |
| date_ambiguous   | ambiguous=True 或无法归一化                   |
| percent_bound    | 百分比 >1000% 或负增长率语义矛盾                |
| amount_magnitude | 同 resume 金额类数字量级差 >10^4                |
| missing_field    | schema 必填字段缺失 / quote 回验失败            |
"""

from __future__ import annotations

import re
from datetime import date

from .schemas import FactForRules

# ---------------------------------------------------------------------------
# 日期解析（normalized 格式见 ingest/prompts.py 约定）
# ---------------------------------------------------------------------------

_RANGE_SPLIT_RE = re.compile(r"\s*至\s*")
_YM_RE = re.compile(r"^(?P<y>\d{4})(?:-(?P<m>\d{2}))?$")
_END_OPEN = "至今"

# 年月的比较键（月份缺省为 1）
YM = tuple[int, int]


def _parse_ym(text: str) -> YM | None:
    match = _YM_RE.match(text.strip())
    if not match:
        return None
    year = int(match.group("y"))
    month = int(match.group("m")) if match.group("m") else 1
    return (year, month)


def parse_range(normalized: str | None) -> tuple[YM, YM | None] | None:
    """解析 "YYYY-MM 至 YYYY-MM" / "YYYY-MM 至今"；无法解析返回 None。"""
    if not normalized:
        return None
    text = normalized.strip()
    if text.endswith(_END_OPEN):  # 进行中：先剥离"至今"再解析起点
        start = _parse_ym(text[: -len(_END_OPEN)].strip().rstrip("至").strip())
        return (start, None) if start is not None else None
    parts = _RANGE_SPLIT_RE.split(text)
    if len(parts) != 2:
        return None
    start = _parse_ym(parts[0])
    end = _parse_ym(parts[1])
    if start is None or end is None:
        return None
    return (start, end)


def _months_between(a: YM, b: YM) -> int:
    return (b[0] - a[0]) * 12 + (b[1] - a[1])


def _today() -> YM:
    today = date.today()
    return (today.year, today.month)


# ---------------------------------------------------------------------------
# 单 fact 规则
# ---------------------------------------------------------------------------

def date_order(fact: FactForRules) -> list[dict]:
    """同段经历结束日期早于开始日期。"""
    details: list[dict] = []
    for item in fact.date_interpretations():
        parsed = parse_range(item.get("normalized"))
        if parsed is None:
            continue
        start, end = parsed
        if end is not None and _months_between(start, end) < 0:
            details.append(
                {
                    "message": f"日期区间颠倒：{item.get('normalized')}（结束早于开始），疑似录入或解析错误",
                    "raw": item.get("raw"),
                    "normalized": item.get("normalized"),
                    "start": list(start),
                    "end": list(end),
                }
            )
    return details


def date_ambiguous(fact: FactForRules) -> list[dict]:
    """日期歧义或无法归一化。"""
    details: list[dict] = []
    for item in fact.date_interpretations():
        normalized = item.get("normalized")
        if item.get("ambiguous") or normalized is None:
            candidates = item.get("candidates") or []
            hint = f"，候选解释：{'、'.join(str(c) for c in candidates)}" if candidates else ""
            details.append(
                {
                    "message": f"日期格式歧义或无法归一化：{item.get('raw')}{hint}",
                    "raw": item.get("raw"),
                    "candidates": list(candidates),
                }
            )
    return details


def percent_bound(fact: FactForRules) -> list[dict]:
    """百分比 >1000%，或负增长率语义矛盾。"""
    details: list[dict] = []
    for number in fact.numbers():
        unit = str(number.get("unit", "")).strip()
        value = number.get("value")
        if unit == "%" and isinstance(value, (int, float)) and value > 1000:
            details.append(
                {
                    "message": f"百分比超出常规范围：{value:g}%（>1000%）",
                    "value": value,
                    "unit": "%",
                }
            )
    growth_words = ("增长", "提升", "提高", "上涨", "上升", "增加")
    if any(word in fact.raw_quote for word in growth_words):
        match = re.search(r"(?:-|负)\s*(\d+(?:\.\d+)?)\s*%", fact.raw_quote)
        if match:
            details.append(
                {
                    "message": (
                        f"负增长率语义存疑：原文含增长类表述，但数值为 -{match.group(1)}%。"
                        "可能是语义矛盾，也可能是合理表述（如“将亏损收窄”），请人工判断"
                    ),
                    "value": -float(match.group(1)),
                    "unit": "%",
                    "raw": match.group(0),
                }
            )
    return details


# section 必填字段（schema 必填字段缺失）
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "work": ("organization",),
    "project": ("name",),
    "education": ("school",),
}


def missing_field(fact: FactForRules) -> list[dict]:
    """schema 必填字段缺失 / quote 回验失败 / quote 歧义不锚定。"""
    details: list[dict] = []
    if not fact.quote_verified:
        details.append(
            {
                "message": "quote 回验失败：抽取片段未能在规范原文中逐字定位，需人工核对",
                "quote": fact.raw_quote[:120],
            }
        )
    elif fact.quote_match_count > 1:
        details.append(
            {
                "message": (
                    f"quote 在原文中出现 {fact.quote_match_count} 次，无法唯一定位："
                    "已按保守策略不锚定（本条无高亮联动），请人工核对或补充更长上下文"
                ),
                "quote": fact.raw_quote[:120],
                "match_count": fact.quote_match_count,
            }
        )
    if not fact.raw_quote.strip():
        details.append({"message": "quote 为空：该条目没有可定位的原文依据"})
    fields = fact.fields()
    for name in REQUIRED_FIELDS.get(fact.section, ()):
        if not fields.get(name):
            details.append(
                {
                    "message": f"{fact.section} 条目缺少必填字段：{name}",
                    "section": fact.section,
                    "field": name,
                }
            )
    return details


# ---------------------------------------------------------------------------
# 跨 fact 规则（同 resume 聚合）
# ---------------------------------------------------------------------------

def _work_ranges(facts: list[FactForRules], today: YM) -> list[tuple[str, YM, YM]]:
    """收集 work 条目的可解析时间区间（结束为 None 时按当前年月）。"""
    ranges: list[tuple[str, YM, YM]] = []
    for fact in facts:
        if fact.section != "work":
            continue
        for item in fact.date_interpretations():
            parsed = parse_range(item.get("normalized"))
            if parsed is None:
                continue
            start, end = parsed
            ranges.append((fact.fact_id, start, end if end is not None else today))
    return ranges


def date_overlap(facts: list[FactForRules], today: YM | None = None) -> list[tuple[str, dict]]:
    """多段 work 时间区间重叠 >3 个月（两两比较，双方各挂一条标记）。"""
    today = today or _today()
    ranges = _work_ranges(facts, today)
    output: list[tuple[str, dict]] = []
    for i in range(len(ranges)):
        for j in range(i + 1, len(ranges)):
            fid_a, start_a, end_a = ranges[i]
            fid_b, start_b, end_b = ranges[j]
            if fid_a == fid_b:
                continue
            overlap_start = max(start_a, start_b)
            overlap_end = min(end_a, end_b)
            if overlap_end <= overlap_start:
                continue
            months = _months_between(overlap_start, overlap_end)
            if months > 3:
                detail = {
                    "message": (
                        f"工作经历时间重叠 {months} 个月（超过 3 个月）："
                        f"{overlap_start[0]}-{overlap_start[1]:02d} 至 {overlap_end[0]}-{overlap_end[1]:02d}"
                    ),
                    "overlap_months": months,
                    "overlap_start": list(overlap_start),
                    "overlap_end": list(overlap_end),
                    "other_fact_id": "",
                }
                output.append((fid_a, {**detail, "other_fact_id": fid_b}))
                output.append((fid_b, {**detail, "other_fact_id": fid_a}))
    return output


# 显式金额单位（"万/亿" 等数量词可能是 DAU 量级，不计入金额比较——防误报）
_AMOUNT_UNITS = frozenset({"元", "块", "人民币", "rmb", "美元", "美金", "usd", "¥", "￥"})


def amount_magnitude(facts: list[FactForRules]) -> list[tuple[str, dict]]:
    """同 resume 金额类数字量级差 >10^4（挂最大与最小金额所在条目）。"""
    amounts: list[tuple[str, float]] = []
    for fact in facts:
        for number in fact.numbers():
            unit = str(number.get("unit", "")).strip().lower()
            value = number.get("value")
            if unit in _AMOUNT_UNITS and isinstance(value, (int, float)) and value > 0:
                amounts.append((fact.fact_id, float(value)))
    if len(amounts) < 2:
        return []
    min_item = min(amounts, key=lambda item: item[1])
    max_item = max(amounts, key=lambda item: item[1])
    if min_item[1] <= 0:
        return []
    ratio = max_item[1] / min_item[1]
    if ratio <= 10_000:
        return []
    detail = {
        "message": (
            f"金额量级差异异常：{min_item[1]:g} 元与 {max_item[1]:g} 元相差 {ratio:,.0f} 倍（>1 万倍）"
        ),
        "min": {"fact_id": min_item[0], "value": min_item[1]},
        "max": {"fact_id": max_item[0], "value": max_item[1]},
        "ratio": ratio,
    }
    output: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for fact_id in (min_item[0], max_item[0]):
        if fact_id not in seen:
            seen.add(fact_id)
            output.append((fact_id, detail))
    return output

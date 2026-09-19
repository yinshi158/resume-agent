"""L0 确定性实体比对（spec §16.2，不看声明出处、全稿扫描）。

两类检查（纯粹确定性，不调 LLM）：

1. **数字**：正则抽取句中全部数字；每个数字必须 ∈ 事实源
   ``entities.numbers``，或 ∈ 本句 ``derived_numbers``（转 L1 裁决）；
   其余 = 偷渡，fail（PRD 3.3："编造的数字被 L0 拦下"）；
2. **公司名/组织/技能词**：以"词表"（诊断要求清单关键词，含中英文别名）
   识别句中疑似实体词，命中者必须能在事实源中找到出处，否则 fail。
   词表之外的实体识别不了 = 漏报方向（与 R3 动词词表同一纪律）。

数字抽取规则与 ingest ``mock_rules._extract_numbers`` 完全一致
（"70万" → value=700000, unit="万"；"30%" → value=30, unit="%"），
两侧同规则才能保证"出处里的数字"与"句面数字"可直接比对。
"""

from __future__ import annotations

import re

from .schemas import DerivedNumber, LayerVerdict, ValidationFact

# ---------------------------------------------------------------------------
# 数字抽取（镜像 ingest 的实体数字格式，勿单侧修改）
# ---------------------------------------------------------------------------

_SCALE = {"万": 10_000, "亿": 100_000_000}

_NUMBER_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<scale>[万亿])?\s*"
    r"(?P<unit>%|DAU|MAU|GMV|PV|UV|ROI|人|次|个|家|名|元|美元|美金|台|件|小时|分钟|天|个月)?",
    re.IGNORECASE,
)

# 日期表达式（19xx/20xx 年 + 可选月；可选区间段）
#
# 为什么在数字抽取前屏蔽日期：ingest 的实体数字抽取**不收无单位小数**
# （"2019.3" 视为版本号/日期而非实体数字，spec §5），而句面日期
# （"2019.3 - 2021.6""2020.4"）在真实改写句中极常见——不屏蔽会成片
# 误报"日期数字无出处"。日期的一致性由 date_interpretations + gate1
# 人工确认承接（ard/0001），本层只对齐事实源数字。屏蔽方向 = 漏报
# （编造的年份日期不在此拦下，交 L2 与人工）。
_DATE_LIKE_RE = re.compile(
    r"(?:19|20)\d{2}\s*(?:[./年]\s*\d{1,2}\s*月?)?"
    r"(?:\s*[-—~～至]\s*(?:(?:19|20)\d{2}\s*(?:[./年]\s*\d{1,2}\s*月?)?|至今|今))?"
)


def mask_dates(text: str) -> str:
    """把日期表达式替换为空格（详见 _DATE_LIKE_RE 说明）。"""
    return _DATE_LIKE_RE.sub(" ", text or "")


class NumberToken:
    """句面/出处数字 token（value 已按万/亿折算，display 为展示数字，decimals 为呈现精度）。"""

    __slots__ = ("display", "decimals", "scale", "unit", "value", "raw")

    def __init__(self, display: float, scale: str, unit: str, raw: str) -> None:
        self.display = display
        self.decimals = len(match_decimals(raw))
        self.scale = scale
        self.unit = unit
        self.value = display * _SCALE.get(scale, 1)
        self.raw = raw

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"NumberToken({self.raw!r} → value={self.value}, unit={self.unit!r})"


def match_decimals(raw: str) -> str:
    """取 token 原文中小数点后的数字串（呈现精度，R6 舍入依据）。"""
    match = re.search(r"\d+\.(\d+)", raw or "")
    return match.group(1) if match else ""


def extract_numbers(text: str) -> list[NumberToken]:
    """抽取文本中的全部数字 token（与 ingest 实体数字规则一致；日期先屏蔽）。"""
    tokens: list[NumberToken] = []
    for match in _NUMBER_RE.finditer(mask_dates(text)):
        display = float(match.group("num"))
        scale = match.group("scale") or ""
        unit = match.group("unit") or ""
        if not unit and scale:
            unit = scale
        tokens.append(NumberToken(display, scale, unit, match.group(0)))
    return tokens


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, abs(b) * 1e-9)


def _unit_compatible(unit_a: str, unit_b: str) -> bool:
    """单位可比：相等（大小写不敏感）或任一侧为空（漏报方向，不因单位缺失误报）。"""
    return unit_a.casefold() == unit_b.casefold() or not unit_a or not unit_b


def _number_sourced(token: NumberToken, source_numbers: list[tuple[float, str]]) -> bool:
    return any(
        _close(token.value, value) and _unit_compatible(token.unit, unit)
        for value, unit in source_numbers
    )


def _derived_tokens(derived: list[DerivedNumber]) -> list[NumberToken]:
    tokens: list[NumberToken] = []
    for item in derived:
        tokens.extend(extract_numbers(item.value or ""))
    return tokens


# ---------------------------------------------------------------------------
# 实体词识别与出处比对
# ---------------------------------------------------------------------------

_TERM_RE = re.compile(r"[A-Za-z\u4e00-\u9fff]")


def _term_hit(text: str, term: str) -> bool:
    """词表中词是否出现在文本（ASCII 大小写不敏感，二者都用子串匹配 = 漏报方向）。"""
    if not term:
        return False
    return term.casefold() in (text or "").casefold()


def _term_known(term: str, all_facts: list[ValidationFact]) -> bool:
    return any(_term_hit(fact.searchable_text, term) for fact in all_facts)


def build_entity_vocab(keyword_groups: list[list[str]]) -> list[str]:
    """实体识别词表 = 诊断要求关键词去重（含中英文别名）。

    过滤：长度 < 2 的词与无字母/汉字的纯符号词不入表（避免噪声命中）。
    """
    vocab: list[str] = []
    seen: set[str] = set()
    for group in keyword_groups:
        for term in group or []:
            term = str(term).strip()
            key = term.casefold()
            if len(term) < 2 or key in seen or not _TERM_RE.search(term):
                continue
            seen.add(key)
            vocab.append(term)
    return vocab


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def check_l0(
    sentence_text: str,
    *,
    all_facts: list[ValidationFact],
    derived_numbers: list[DerivedNumber],
    entity_vocab: list[str],
) -> LayerVerdict:
    """L0 判定：偷渡数字 / 无出处实体词。"""
    source_numbers: list[tuple[float, str]] = []
    for fact in all_facts:
        source_numbers.extend(fact.numbers)
    derived_tokens = _derived_tokens(derived_numbers)

    failures: list[dict] = []
    for token in extract_numbers(sentence_text):
        if any(
            _close(token.value, d.value) and _unit_compatible(token.unit, d.unit)
            for d in derived_tokens
        ):
            continue  # 衍生数字 → 交 L1 裁决（§16.2）
        if not _number_sourced(token, source_numbers):
            failures.append(
                {
                    "kind": "number",
                    "token": token.raw.strip(),
                    "message": f"数字「{token.raw.strip()}」在事实源中无出处",
                }
            )

    for term in entity_vocab:
        if _term_hit(sentence_text, term) and not _term_known(term, all_facts):
            failures.append(
                {
                    "kind": "entity",
                    "term": term,
                    "message": f"技能/实体词「{term}」在事实源中无出处",
                }
            )

    if failures:
        first = failures[0]["message"]
        extra = "" if len(failures) == 1 else f"（共 {len(failures)} 处）"
        return LayerVerdict(
            layer="L0",
            verdict="fail",
            detail={"message": first + extra, "failures": failures},
        )
    return LayerVerdict(
        layer="L0",
        verdict="pass",
        detail={
            "message": "句中数字与实体词均可在事实源中定位",
            "numbers": [token.raw.strip() for token in extract_numbers(sentence_text)],
        },
    )

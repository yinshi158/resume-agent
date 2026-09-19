"""anomaly 六条规则单测：正例、反例、边界（spec §6/§10）。"""

from __future__ import annotations

from app.anomaly import core, rules
from app.anomaly.schemas import FactForRules


def make_fact(
    fact_id: str = "f1",
    section: str = "work",
    quote: str = "",
    payload: dict | None = None,
    verified: bool = True,
    match_count: int = 1,
) -> FactForRules:
    return FactForRules(
        fact_id=fact_id,
        section=section,
        raw_quote=quote,
        payload=payload or {},
        quote_verified=verified,
        quote_match_count=match_count,
    )


def di(raw: str, normalized: str | None, ambiguous: bool = False, candidates: list[str] | None = None) -> dict:
    return {"raw": raw, "normalized": normalized, "ambiguous": ambiguous, "candidates": candidates or []}


def payload(dates: list[dict] | None = None, numbers: list[dict] | None = None, fields: dict | None = None) -> dict:
    return {
        "fields": fields or {},
        "date_interpretations": dates or [],
        "entities": {"numbers": numbers or [], "orgs": [], "skills": []},
    }


def work_fact(fact_id: str, start: str, end: str) -> FactForRules:
    return make_fact(
        fact_id=fact_id,
        section="work",
        payload=payload(dates=[di(f"{start}~{end}", f"{start} 至 {end}")]),
    )


# ---------------------------------------------------------------------------
# date_order
# ---------------------------------------------------------------------------

def test_date_order_positive() -> None:
    fact = make_fact(payload=payload(dates=[di("21.6-19.3", "2021-06 至 2019-03")]))
    details = rules.date_order(fact)
    assert len(details) == 1
    assert "结束早于开始" in details[0]["message"]


def test_date_order_negative_and_boundary() -> None:
    assert rules.date_order(make_fact(payload=payload(dates=[di("19.3-21.6", "2019-03 至 2021-06")]))) == []
    assert rules.date_order(make_fact(payload=payload(dates=[di("同月", "2019-03 至 2019-03")]))) == []
    assert rules.date_order(make_fact(payload=payload(dates=[di("进行中", "2019-03 至今")]))) == []
    assert rules.date_order(make_fact(payload=payload(dates=[di("不可解析", "2021 年 6 月")]))) == []


# ---------------------------------------------------------------------------
# date_overlap
# ---------------------------------------------------------------------------

def test_date_overlap_positive_both_sides_flagged() -> None:
    facts = [work_fact("a", "2021-06", "2023-03"), work_fact("b", "2022-09", "2024-06")]
    flags = rules.date_overlap(facts)
    assert {fact_id for fact_id, _ in flags} == {"a", "b"}
    assert "重叠 6 个月" in flags[0][1]["message"]


def test_date_overlap_boundary_three_months_not_reported() -> None:
    facts = [work_fact("a", "2020-01", "2020-08"), work_fact("b", "2020-04", "2020-07")]
    assert rules.date_overlap(facts) == []  # 恰好 3 个月不报（>3 才报）


def test_date_overlap_boundary_four_months_reported() -> None:
    facts = [work_fact("a", "2020-01", "2020-08"), work_fact("b", "2020-03", "2020-07")]
    assert rules.date_overlap(facts)


def test_date_overlap_negative_disjoint() -> None:
    facts = [work_fact("a", "2019-01", "2021-01"), work_fact("b", "2021-06", "2023-01")]
    assert rules.date_overlap(facts) == []


def test_date_overlap_ignores_non_work_sections() -> None:
    facts = [
        make_fact(fact_id="a", section="project", payload=payload(dates=[di("x", "2021-01 至 2023-01")])),
        make_fact(fact_id="b", section="project", payload=payload(dates=[di("y", "2021-01 至 2023-01")])),
    ]
    assert rules.date_overlap(facts) == []


def test_date_overlap_ongoing_uses_today() -> None:
    facts = [
        make_fact(fact_id="a", section="work", payload=payload(dates=[di("2025.1-至今", "2025-01 至今")])),
        make_fact(fact_id="b", section="work", payload=payload(dates=[di("2025.5-2026.12", "2025-05 至 2026-12")])),
    ]
    # "至今" 按注入的 today=(2026,9) 展开 → 与 b 重叠 2025-05 至 2026-09（16 个月）
    flags = rules.date_overlap(facts, today=(2026, 9))
    assert flags
    assert all("other_fact_id" in detail for _, detail in flags)


# ---------------------------------------------------------------------------
# date_ambiguous
# ---------------------------------------------------------------------------

def test_date_ambiguous_positive() -> None:
    fact = make_fact(
        payload=payload(dates=[di("20.13-21.6", None, ambiguous=True, candidates=["2020 年 13 月：月份非法"])])
    )
    details = rules.date_ambiguous(fact)
    assert len(details) == 1
    assert "月份非法" in details[0]["message"]


def test_date_ambiguous_negative() -> None:
    assert rules.date_ambiguous(make_fact(payload=payload(dates=[di("19.3", "2019-03")]))) == []


# ---------------------------------------------------------------------------
# percent_bound
# ---------------------------------------------------------------------------

def test_percent_bound_positive() -> None:
    fact = make_fact(quote="转化率 1200%", payload=payload(numbers=[{"value": 1200, "unit": "%"}]))
    assert len(rules.percent_bound(fact)) == 1


def test_percent_bound_boundary_1000_not_reported() -> None:
    fact = make_fact(payload=payload(numbers=[{"value": 1000, "unit": "%"}]))
    assert rules.percent_bound(fact) == []


def test_percent_bound_negative_growth_conflict() -> None:
    fact = make_fact(quote="新增用户提升 -20%", payload=payload(numbers=[{"value": 20, "unit": "%"}]))
    details = rules.percent_bound(fact)
    assert len(details) == 1
    # 关键词判定存在极少数误伤（"提升效率，亏损从 -30% 收窄"），
    # detail 必须明确要求人工核对语义（审查 P3）
    assert "人工判断" in details[0]["message"]


def test_percent_bound_decrease_not_conflict() -> None:
    fact = make_fact(quote="成本下降 20%", payload=payload(numbers=[{"value": 20, "unit": "%"}]))
    assert rules.percent_bound(fact) == []


# ---------------------------------------------------------------------------
# amount_magnitude
# ---------------------------------------------------------------------------

def test_amount_magnitude_positive() -> None:
    facts = [
        make_fact(fact_id="a", payload=payload(numbers=[{"value": 500, "unit": "元"}])),
        make_fact(fact_id="b", payload=payload(numbers=[{"value": 50_000_000, "unit": "元"}])),
    ]
    flags = rules.amount_magnitude(facts)
    assert {fact_id for fact_id, _ in flags} == {"a", "b"}
    assert "1 万倍" in flags[0][1]["message"] or "相差" in flags[0][1]["message"]


def test_amount_magnitude_boundary_ratio_exactly_1e4_not_reported() -> None:
    facts = [
        make_fact(fact_id="a", payload=payload(numbers=[{"value": 1, "unit": "元"}])),
        make_fact(fact_id="b", payload=payload(numbers=[{"value": 10_000, "unit": "元"}])),
    ]
    assert rules.amount_magnitude(facts) == []


def test_amount_magnitude_ignores_non_currency_units() -> None:
    """'万/人/次' 等数量词不算金额（防 DAU 量级误报）。"""
    facts = [
        make_fact(fact_id="a", payload=payload(numbers=[{"value": 700_000, "unit": "万"}, {"value": 3, "unit": "人"}])),
        make_fact(fact_id="b", payload=payload(numbers=[{"value": 2, "unit": "次"}])),
    ]
    assert rules.amount_magnitude(facts) == []


def test_amount_magnitude_single_amount_not_reported() -> None:
    facts = [make_fact(fact_id="a", payload=payload(numbers=[{"value": 500, "unit": "元"}]))]
    assert rules.amount_magnitude(facts) == []


# ---------------------------------------------------------------------------
# missing_field
# ---------------------------------------------------------------------------

def test_missing_field_required_field() -> None:
    fact = make_fact(section="work", payload=payload(fields={"title": "工程师"}))
    details = rules.missing_field(fact)
    assert any("organization" in detail["message"] for detail in details)


def test_missing_field_quote_unverified() -> None:
    fact = make_fact(quote="原文里没有的片段", verified=False)
    details = rules.missing_field(fact)
    assert any("回验失败" in detail["message"] for detail in details)


def test_missing_field_ok() -> None:
    fact = make_fact(
        section="work",
        quote="甲公司 | 工程师",
        payload=payload(fields={"organization": "甲公司"}),
    )
    assert rules.missing_field(fact) == []


def test_missing_field_quote_ambiguous() -> None:
    """quote 多处匹配 → 歧义标记（审查 P2：不静默锚第一个实例）。"""
    fact = make_fact(
        section="skill",
        quote="Python",
        payload=payload(fields={"items": ["Python"]}),
        verified=True,
        match_count=3,
    )
    details = rules.missing_field(fact)
    assert any("3 次" in detail["message"] and "无法唯一定位" in detail["message"] for detail in details)
    # 唯一匹配不误报
    single = make_fact(
        section="skill",
        quote="Python",
        payload=payload(fields={"items": ["Python"]}),
        match_count=1,
    )
    assert rules.missing_field(single) == []


# ---------------------------------------------------------------------------
# compute_flags 聚合（对 04 号量化 fixture 的规则语义验收）
# ---------------------------------------------------------------------------

def test_compute_flags_on_quantified_fixture() -> None:
    from app.anomaly.schemas import FactForRules as F
    from app.ingest import mock_rules
    from conftest import canonical_of, load_fixture_text

    canonical = canonical_of(load_fixture_text("04_quantified.txt"))
    raw = mock_rules.extract_facts(canonical)
    rules_input = [
        F(
            fact_id=f"f{index}",
            section=item["section"],
            raw_quote=item["quote"],
            payload={
                "fields": item["fields"],
                "date_interpretations": item["date_interpretations"],
                "entities": item["entities"],
            },
        )
        for index, item in enumerate(raw)
    ]
    flags = core.compute_flags(rules_input)
    labels = {flag.rule for flag in flags}
    assert {"date_order", "date_overlap", "percent_bound", "amount_magnitude"} <= labels
    for flag in flags:
        assert flag.detail.get("message"), "每条 detail 必须含人类可读说明（gate1 直接展示）"

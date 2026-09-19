"""M3-1 确定性校验层单测（spec §22 单测行、§16）。

覆盖：formula 白名单求值器（合法式 / 区间 / 百分号字面量 / 注入拒绝）、
verb_lexicon 命中 × 归因组合矩阵、L0 偷渡数字与合法衍生、
L1 重算（精度容差、字面量溯源、模糊量化词区间）。
"""

from __future__ import annotations

import pytest

from app.validate import l0, verbs
from app.validate.l0 import build_entity_vocab, extract_numbers
from app.validate.l1 import FormulaError, check_l1, evaluate_formula, parse_formula
from app.validate.schemas import DerivedNumber, ValidationFact
from app.verb_lexicon import LEXICON_VERSION


# ---------------------------------------------------------------------------
# 公共构造
# ---------------------------------------------------------------------------

def make_fact(
    fact_id: str = "f1",
    *,
    quote: str = "负责推荐系统开发",
    numbers: list[tuple[float, str]] | None = None,
    attribution: str = "individual",
    attribution_confirmed: bool = True,
    confirmed: bool = True,
) -> ValidationFact:
    return ValidationFact(
        fact_id=fact_id,
        section="work",
        raw_quote=quote,
        searchable_text=quote,
        numbers=numbers or [],
        attribution=attribution,
        attribution_confirmed=attribution_confirmed,
        confirmed=confirmed,
    )


# ---------------------------------------------------------------------------
# formula 白名单求值器（§16.4）
# ---------------------------------------------------------------------------

class TestFormulaEvaluator:
    @pytest.mark.parametrize(
        ("formula", "expected"),
        [
            ("1+2*3", 7.0),
            ("(910000-700000)/700000", 0.3),
            ("(100-80)/100*100", 20.0),
            ("30%", 0.3),                # 百分号字面量 → 0.3
            ("50% / 2", 0.25),
            ("-5+10", 5.0),
            ("+3*2", 6.0),
            ("(1+2)*(3+4)", 21.0),
            ("1000000/3", 1000000 / 3),
        ],
    )
    def test_legal_formulas(self, formula: str, expected: float) -> None:
        assert evaluate_formula(formula) == pytest.approx(expected)

    @pytest.mark.parametrize(
        "formula",
        [
            "__import__('os').system('calc')",   # 调用 + 属性
            "open('x')",                         # 调用
            "x + 1",                             # 名称
            "a.b",                               # 属性
            "lst[0]",                            # 下标
            "1 if 2 else 3",                     # 条件表达式
            "1 == 1",                            # 比较
            "2 ** 8",                            # 幂
            "1 // 2",                            # 整除
            "1 % 2",                             # 取模（% 不是求模运算符）
            "'a' + 'b'",                         # 字符串
            "[1, 2]",                            # 列表
            "lambda: 1",                         # lambda
            "1 < 2 < 3",                         # 比较链
            "True + 1",                          # 布尔常量
        ],
    )
    def test_injection_rejected(self, formula: str) -> None:
        with pytest.raises(FormulaError):
            evaluate_formula(formula)

    def test_syntax_and_limits(self) -> None:
        with pytest.raises(FormulaError):
            evaluate_formula("")
        with pytest.raises(FormulaError):
            evaluate_formula("1+")
        with pytest.raises(FormulaError):
            evaluate_formula("1+2" + "+2" * 200)  # 超长
        with pytest.raises(FormulaError):
            evaluate_formula("1/0")
        with pytest.raises(FormulaError):
            evaluate_formula("(1+2")

    def test_parse_formula_does_not_evaluate(self) -> None:
        # 白名单外节点在 parse 阶段可通过，evaluate 阶段才拒绝（分层清晰）
        assert parse_formula("x + 1") is not None
        with pytest.raises(FormulaError):
            evaluate_formula("x + 1")

    def test_extract_numbers_mirrors_ingest(self) -> None:
        tokens = extract_numbers("把 DAU 从 70万 提升到 91万，增长 30%")
        assert (tokens[0].value, tokens[0].unit) == (700000, "万")
        assert (tokens[1].value, tokens[1].unit) == (910000, "万")
        assert (tokens[2].value, tokens[2].unit) == (30, "%")


# ---------------------------------------------------------------------------
# 归因动词层（§16.3）：命中 × 归因组合矩阵
# ---------------------------------------------------------------------------

class TestVerbLayer:
    def _facts(self, *attributions: str, **kwargs) -> tuple[dict, list[str]]:
        facts = {}
        ids = []
        for index, attribution in enumerate(attributions):
            fid = f"f{index}"
            facts[fid] = make_fact(fid, attribution=attribution, **kwargs)
            ids.append(fid)
        return facts, ids

    @pytest.mark.parametrize("attribution", ["team", "mixed", "unknown"])
    def test_dominant_with_non_individual_fails(self, attribution: str) -> None:
        facts, ids = self._facts(attribution)
        verdict = verbs.check_verb("主导了推荐系统的重构", source_fact_ids=ids, facts_by_id=facts)
        assert verdict.verdict == "fail"
        assert "主导" in verdict.detail["message"]

    def test_dominant_with_individual_passes(self) -> None:
        facts, ids = self._facts("individual")
        verdict = verbs.check_verb("主导了推荐系统的重构", source_fact_ids=ids, facts_by_id=facts)
        assert verdict.verdict == "pass"
        assert verdict.detail["dominant_hits"] == ["主导"]

    def test_dominant_with_unconfirmed_individual_fails(self) -> None:
        facts, ids = self._facts("individual", attribution_confirmed=False)
        verdict = verbs.check_verb("牵头完成了架构升级", source_fact_ids=ids, facts_by_id=facts)
        assert verdict.verdict == "fail"

    def test_dominant_with_mixed_sources_fails(self) -> None:
        facts = {
            "f0": make_fact("f0", attribution="individual"),
            "f1": make_fact("f1", attribution="team"),
        }
        verdict = verbs.check_verb("独立完成了系统迁移", source_fact_ids=["f0", "f1"], facts_by_id=facts)
        assert verdict.verdict == "fail"

    def test_participatory_passes_for_all_attributions(self) -> None:
        for attribution in ("individual", "team", "mixed", "unknown"):
            facts, ids = self._facts(attribution)
            verdict = verbs.check_verb("参与并配合完成了系统迁移", source_fact_ids=ids, facts_by_id=facts)
            assert verdict.verdict == "pass", attribution
            assert "参与" in verdict.detail["participatory_hits"]

    def test_no_verb_hit_passes(self) -> None:
        facts, ids = self._facts("unknown")
        verdict = verbs.check_verb("完成推荐系统的重构与上线", source_fact_ids=ids, facts_by_id=facts)
        assert verdict.verdict == "pass"
        assert verdict.detail["dominant_hits"] == []

    def test_missing_source_fact_fails(self) -> None:
        verdict = verbs.check_verb("主导了系统建设", source_fact_ids=["missing"], facts_by_id={})
        assert verdict.verdict == "fail"
        assert "缺失" in verdict.detail["message"]

    def test_lexicon_version_exposed(self) -> None:
        facts, ids = self._facts("individual")
        verdict = verbs.check_verb("主导了系统建设", source_fact_ids=ids, facts_by_id=facts)
        assert verdict.detail["lexicon_version"] == LEXICON_VERSION


# ---------------------------------------------------------------------------
# L0 确定性实体比对（§16.2）
# ---------------------------------------------------------------------------

class TestL0Layer:
    def test_smuggled_number_fails(self) -> None:
        facts = [make_fact("f0", numbers=[(700000, "万")])]
        verdict = l0.check_l0(
            "将 DAU 从 70 万提升到 92 万",
            all_facts=facts,
            derived_numbers=[],
            entity_vocab=[],
        )
        assert verdict.verdict == "fail"
        assert "92" in verdict.detail["message"]

    def test_sourced_number_passes(self) -> None:
        facts = [make_fact("f0", numbers=[(700000, "万"), (910000, "万")])]
        verdict = l0.check_l0(
            "将 DAU 从 70 万提升到 91 万",
            all_facts=facts,
            derived_numbers=[],
            entity_vocab=[],
        )
        assert verdict.verdict == "pass"

    def test_derived_number_delegated_to_l1(self) -> None:
        facts = [make_fact("f0", numbers=[(700000, "万")])]
        derived = [DerivedNumber(value="30%", formula="(910000-700000)/700000", source_ids=["f0"])]
        # 30% 不在事实源数字中，但属于本句 derived → 不算 L0 偷渡（转 L1 裁决）
        verdict = l0.check_l0(
            "规模提升 30%",
            all_facts=facts,
            derived_numbers=derived,
            entity_vocab=[],
        )
        assert verdict.verdict == "pass"

    def test_entity_vocab_smuggling_fails(self) -> None:
        facts = [make_fact("f0", quote="使用 Python 与 Spark 完成离线特征链路改造")]
        verdict = l0.check_l0(
            "熟练使用 Redis 构建缓存层",
            all_facts=facts,
            derived_numbers=[],
            entity_vocab=["Redis", "Python"],
        )
        assert verdict.verdict == "fail"
        assert "Redis" in verdict.detail["message"]

    def test_entity_vocab_found_in_facts_passes(self) -> None:
        facts = [make_fact("f0", quote="使用 Python 与 Spark 完成离线特征链路改造")]
        verdict = l0.check_l0(
            "使用 Python 与 Spark 完成特征链路改造",
            all_facts=facts,
            derived_numbers=[],
            entity_vocab=["Python", "Spark"],
        )
        assert verdict.verdict == "pass"

    def test_date_numbers_masked(self) -> None:
        """日期数字不进比对：ingest 实体不收无单位小数（"2019.3"），
        句面日期不屏蔽会成片误报（真实走查 2026-09-18 发现的 P2 级误报）。"""
        facts = [make_fact("f0", numbers=[])]
        for text in (
            "在腾讯科技有限公司担任后端开发工程师（2019.3 - 2021.6）",
            "推荐系统重构项目（2020.4 至 2021.6）",
            "获 2020 年度腾讯技术突破奖。",
            "2021.7 至今在字节跳动担任高级后端工程师",
        ):
            verdict = l0.check_l0(
                text, all_facts=facts, derived_numbers=[], entity_vocab=[]
            )
            assert verdict.verdict == "pass", (text, verdict.detail)

    def test_date_masking_keeps_real_numbers(self) -> None:
        facts = [make_fact("f0", numbers=[])]
        verdict = l0.check_l0(
            "2019.3 - 2021.6 期间将 DAU 提升 92%",
            all_facts=facts,
            derived_numbers=[],
            entity_vocab=[],
        )
        assert verdict.verdict == "fail"
        assert "92" in verdict.detail["message"]

    def test_unit_mismatch_fails(self) -> None:
        facts = [make_fact("f0", numbers=[(30, "%")])]
        verdict = l0.check_l0(
            "管理 30 人的团队",
            all_facts=facts,
            derived_numbers=[],
            entity_vocab=[],
        )
        assert verdict.verdict == "fail"

    def test_build_entity_vocab_filters_noise(self) -> None:
        vocab = build_entity_vocab([["SQL", "sql", "a", "++", "推荐系统"], ["Redis"]])
        assert vocab == ["SQL", "推荐系统", "Redis"]


# ---------------------------------------------------------------------------
# L1 衍生数字（§16.4）
# ---------------------------------------------------------------------------

class TestL1Layer:
    def _facts(self) -> dict[str, ValidationFact]:
        return {
            "f0": make_fact("f0", numbers=[(700000, "万"), (910000, "万")]),
            "f1": make_fact("f1", numbers=[(30, "%")]),
        }

    def test_recompute_pass(self) -> None:
        verdict = check_l1(
            "规模提升 30%",
            source_fact_ids=["f0", "f1"],
            facts_by_id=self._facts(),
            derived_numbers=[
                DerivedNumber(value="30%", formula="(910000-700000)/700000", source_ids=["f0"])
            ],
        )
        assert verdict.verdict == "pass"

    def test_recompute_precision_rounding(self) -> None:
        facts = {"f0": make_fact("f0", numbers=[(1000, ""), (1430, "")])}
        # (1430-1000)/1000 = 0.43 → 43%（按 value 呈现精度 0 位舍入相等）
        ok = check_l1(
            "效率提升 43%",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="43%", formula="(1430-1000)/1000", source_ids=["f0"])
            ],
        )
        assert ok.verdict == "pass"
        bad = check_l1(
            "效率提升 44%",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="44%", formula="(1430-1000)/1000", source_ids=["f0"])
            ],
        )
        assert bad.verdict == "fail"
        assert "重算" in bad.detail["message"]

    def test_recompute_precision_with_decimal_value(self) -> None:
        facts = {"f0": make_fact("f0", numbers=[(1000, ""), (1125, "")])}
        # (1125-1000)/1000 = 0.125 → 12.5% 按 1 位小数舍入相等；12.3% 不等
        ok = check_l1(
            "提升 12.5%",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="12.5%", formula="(1125-1000)/1000", source_ids=["f0"])
            ],
        )
        assert ok.verdict == "pass"

    def test_percent_literal_formula(self) -> None:
        facts = {"f0": make_fact("f0", numbers=[(30, "%")])}
        verdict = check_l1(
            "转化率提升 15%",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="15%", formula="30% / 2", source_ids=["f0"])
            ],
        )
        assert verdict.verdict == "pass"

    def test_pure_constant_formula_rejected(self) -> None:
        # "0.5" 重算 50% 成立，但纯常数公式 = 偷渡数字的另一种写法 → 拒绝
        facts = {"f0": make_fact("f0", numbers=[(30, "%")])}
        verdict = check_l1(
            "转化率提升 50%",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="50%", formula="0.5", source_ids=["f0"])
            ],
        )
        assert verdict.verdict == "fail"
        assert "公式必须由出处数字推导" in verdict.detail["message"]

    def test_literal_not_sourced_fails(self) -> None:
        verdict = check_l1(
            "规模提升 30%",
            source_fact_ids=["f0"],
            facts_by_id=self._facts(),
            derived_numbers=[
                DerivedNumber(value="30%", formula="(3+4)/7*100/100", source_ids=["f0"])
            ],
        )
        assert verdict.verdict == "fail"
        assert "不在所选出处的数字" in verdict.detail["message"]

    def test_scale_conversion_literal_sourced(self) -> None:
        # 出处 "91 万"（value=910000, unit=万）→ 字面量 91 可回溯（91*10000）
        verdict = check_l1(
            "规模达到 91 万",
            source_fact_ids=["f0"],
            facts_by_id=self._facts(),
            derived_numbers=[
                DerivedNumber(value="91 万", formula="91*10000", source_ids=["f0"])
            ],
        )
        assert verdict.verdict == "pass"

    def test_source_ids_constraints(self) -> None:
        facts = self._facts()
        empty = check_l1(
            "提升 30%", source_fact_ids=["f0"], facts_by_id=facts,
            derived_numbers=[DerivedNumber(value="30%", formula="(910000-700000)/700000", source_ids=[])],
        )
        assert empty.verdict == "fail"
        outside = check_l1(
            "提升 30%", source_fact_ids=["f0"], facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="30%", formula="(910000-700000)/700000", source_ids=["f1"])
            ],
        )
        assert outside.verdict == "fail"
        unknown = check_l1(
            "提升 30%", source_fact_ids=["f0"], facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="30%", formula="(910000-700000)/700000", source_ids=["nope"])
            ],
        )
        assert unknown.verdict == "fail"

    def test_range_endpoints_compared_pairwise(self) -> None:
        facts = {
            "f0": make_fact("f0", numbers=[(700000, "万"), (910000, "万"), (931000, "万")])
        }
        ok = check_l1(
            "规模提升 30%~33%",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(
                    value="30%~33%",
                    formula="(910000-700000)/700000~(931000-700000)/700000",
                    source_ids=["f0"],
                )
            ],
        )
        assert ok.verdict == "pass"
        shape = check_l1(
            "规模提升 30%~33%",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="30%~33%", formula="(910000-700000)/700000", source_ids=["f0"])
            ],
        )
        assert shape.verdict == "fail"
        assert "两端点分列" in shape.detail["message"]

    def test_fuzzy_quantifier_requires_interval_value(self) -> None:
        facts = {"f0": make_fact("f0", numbers=[(50000, ""), (100000, "")])}
        # 出处数字 100000/50000，formula 求值 2.0 ∈ [1.9, 2.1] → 支撑"翻倍"
        ok = check_l1(
            "管理规模实现翻倍",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[
                DerivedNumber(value="2倍", formula="100000/50000", source_ids=["f0"])
            ],
        )
        assert ok.verdict == "pass"
        bad = check_l1(
            "管理规模实现翻倍",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[],
        )
        assert bad.verdict == "fail"
        assert "模糊量化词" in bad.detail["message"]

    def test_fuzzy_word_quoted_from_source_passes(self) -> None:
        facts = {"f0": make_fact("f0", quote="主导用户规模翻倍增长", numbers=[(50000, "")])}
        verdict = check_l1(
            "主导用户规模翻倍增长",
            source_fact_ids=["f0"],
            facts_by_id=facts,
            derived_numbers=[],
        )
        assert verdict.verdict == "pass"

    def test_no_derived_numbers_pass_without_fuzzy(self) -> None:
        verdict = check_l1(
            "负责推荐系统的日常开发",
            source_fact_ids=["f0"],
            facts_by_id=self._facts(),
            derived_numbers=[],
        )
        assert verdict.verdict == "pass"

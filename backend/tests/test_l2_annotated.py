"""L2 标注集回归（spec §16.5 / R5，种子 = M2 的 3 条 boundary:m3-l2 条目）。

- 每个 case：句子 + 声明出处 → L2 判定 → 与 ``expect_entailed`` 比对；
- LLM 响应来源：``RECORD_VCR=1`` 录制（真实 LLM，主库设置导入）>
  ``l2/vcr/{case}.json`` 回放 > mock 规则（无 key 环境全链路可跑通）；
- **mock 规则的判定质量不代表真实水平**（6 字重叠启发式）——无录制时
  只输出趋势统计，严格吻合断言以录制为准（与 §13.2"draft 只观测趋势"
  同一纪律）；有录制时必须逐条吻合，不吻合 = 真实差异，须人工复核；
- 3 条 seed（boundary-003/006/007）的逐条翻正验证（C9）：录制就绪时自动
  断言翻正方向；测试同时在终端输出 seed 状态。
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import conftest
import pytest

from app.platform import config, llm
from app.validate import l2
from app.validate.schemas import ValidationFact

L2_DIR = conftest.FIXTURES_DIR / "l2"
CASES_DIR = L2_DIR / "cases"
VCR_DIR = L2_DIR / "vcr"

RECORD_VCR = os.environ.get("RECORD_VCR") == "1"

#: 3 条 boundary seed 的翻正方向（expect_entailed）
SEED_EXPECTATIONS = {
    "boundary-003": True,   # Spark：有逐字证据 → 应蕴含
    "boundary-006": True,   # SQL 技能行 → 应蕴含
    "boundary-007": False,  # Kafka 半对证据 → 不得蕴含
}


def _case_paths() -> list[Path]:
    return sorted(CASES_DIR.glob("*.json"))


def _load_case(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_ids() -> list[str]:
    return [path.stem for path in _case_paths()]


def _facts_of(case: dict) -> dict[str, ValidationFact]:
    facts: dict[str, ValidationFact] = {}
    for fact_id in case["source_fact_ids"]:
        source = case["sources"][fact_id]
        facts[fact_id] = ValidationFact(
            fact_id=fact_id,
            section=source.get("section") or "other",
            raw_quote=source["raw_quote"],
            searchable_text=source["raw_quote"],
            attribution=source.get("attribution") or "individual",
            attribution_confirmed=True,
            confirmed=True,
        )
    return facts


@contextmanager
def _llm_source(case_id: str, monkeypatch) -> Iterator[str]:
    """L2 的 LLM 响应来源：录制（RECORD_VCR=1）> VCR 回放 > mock 规则。"""
    vcr_path = VCR_DIR / f"{case_id}.json"

    if RECORD_VCR:
        conftest.import_live_settings_from_main_db()
        if config.is_mock():
            pytest.fail("RECORD_VCR=1 需要真实模型：请在设置页配置 key 并置 llm_mode=live/auto")
        calls: list[str] = []
        real_chat_json = llm.chat_json

        def recording(messages, **kwargs):
            text, is_mock = real_chat_json(messages, **kwargs)
            if is_mock:
                pytest.fail("录制过程中出现 mock 响应——请检查 live 配置")
            calls.append(text)
            return text, False

        monkeypatch.setattr(llm, "chat_json", recording)
        try:
            yield "record"
        finally:
            VCR_DIR.mkdir(parents=True, exist_ok=True)
            vcr_path.write_text(
                json.dumps({"case": case_id, "calls": calls}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return

    if vcr_path.exists():
        state = {"n": 0}
        calls = json.loads(vcr_path.read_text(encoding="utf-8")).get("calls", [])

        def playback(messages, **kwargs):
            index = state["n"]
            if index >= len(calls):
                raise RuntimeError(f"VCR 录制不足（{case_id}）：请 RECORD_VCR=1 重录")
            state["n"] += 1
            return calls[index], False

        monkeypatch.setattr(llm, "chat_json", playback)
        yield "vcr"
        return

    yield "mock"


def test_l2_annotation_set_integrity() -> None:
    """标注集完整性守卫（R5）：5–10 组、3 条 seed、comment 齐备。"""
    paths = _case_paths()
    assert 5 <= len(paths) <= 10, "R5：以 3 条 boundary 为种子扩 5–10 组"
    seeds: list[str] = []
    for path in paths:
        case = _load_case(path)
        assert case.get("sentence"), f"[{path.stem}] 缺少 sentence"
        assert case.get("source_fact_ids"), f"[{path.stem}] 缺少 source_fact_ids"
        assert isinstance(case.get("expect_entailed"), bool), f"[{path.stem}] 缺少期望判定"
        assert case.get("comment"), f"[{path.stem}] 缺少 comment（差异复核依据）"
        for fact_id in case["source_fact_ids"]:
            assert fact_id in case["sources"], f"[{path.stem}] 出处 {fact_id} 未定义"
        if case.get("seed"):
            seeds.append(case["seed"])
    assert sorted(seeds) == sorted(SEED_EXPECTATIONS), (
        f"3 条 boundary seed 必须全部在场（C9）：当前 {seeds}"
    )


@pytest.mark.parametrize("case_id", _case_ids())
def test_l2_case(case_id: str, monkeypatch) -> None:
    case = _load_case(CASES_DIR / f"{case_id}.json")
    facts = _facts_of(case)

    with _llm_source(case_id, monkeypatch) as source:
        verdict = l2.check_l2(
            case["sentence"],
            source_fact_ids=case["source_fact_ids"],
            facts_by_id=facts,
        )

    actual = verdict.verdict == "pass"
    expected = case["expect_entailed"]
    agree = actual == expected
    seed = case.get("seed")
    seed_note = f" · seed={seed}" if seed else ""

    conftest.L2_STATS.append(
        f"[{case_id}] 来源={source}{seed_note} 期望={'蕴含' if expected else '不蕴含'}"
        f" · 实际={'蕴含' if actual else '不蕴含'}"
        + ("" if agree else f" · ✗差异：{verdict.detail.get('message')}")
    )

    if source in ("record", "vcr"):
        assert agree, (
            f"[{case_id}] L2 判定与标注不符（{'录制' if source == 'record' else '回放'}）："
            f"期望 {'蕴含' if expected else '不蕴含'}，实际 {verdict.detail.get('message')}；"
            f"comment：{case['comment']}"
        )
    else:
        # mock 规则只观测趋势：机制必须产出判定，但不断言与真实期望吻合
        assert verdict.layer == "L2"


def test_seed_flip_directions() -> None:
    """C9 翻正方向守卫：3 条 seed 的期望方向与 boundary 处置记录一致。"""
    for path in _case_paths():
        case = _load_case(path)
        if not case.get("seed"):
            continue
        assert case["expect_entailed"] is SEED_EXPECTATIONS[case["seed"]], (
            f"[{path.stem}] seed {case['seed']} 的期望方向被改动——"
            f"翻正口径变更须同步 spec §13.2/§16.5 并说明原因"
        )

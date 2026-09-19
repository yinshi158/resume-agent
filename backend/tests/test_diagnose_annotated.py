"""标注测试集回归（spec §13）。

每对（简历, JD）跑完整诊断链路——简历走 mock 规则抽取入库为 confirmed
事实源（确定性），诊断的 LLM 响应优先走 VCR 回放，无录制时走 mock 规则
（无 key 环境全链路可跑）：

- 比对方式：``text_contains`` 模糊匹配实际拆出的要求，逐条比对 expect_status；
- 统计口径（ard/0004 不对称性）：direct 命中率与 gap 误判率（期望可达但仍标
  gap）分开统计，后者容忍度更高但需重点观察；终端摘要每次运行自动输出；
- **边界条目**（expected 里标 ``"boundary": "m3-l2"``）：已知的机制边界，
  不计入吻合率分母，单独统计其当前实际状态——它们是 M3 L2 语义校验的
  验收输入，M3 落地后应逐条翻正；不这样做的话，长期存在的红项会训练
  大家忽略差异输出；
- VCR 录制：配好 key（live 模式）并设 ``RECORD_VCR=1`` 运行本测试 →
  写 ``annotated/vcr/{pair}.json``；此后测试优先回放，prompt 改动时才重录；
- 阈值（spec §13.2，2026-09-18 复核定稿后写回）：非边界条目吻合率
  ≥ 0.85（当前基线 39/39 = 100%，VCR 回放确定；0.85 是重录时的回归预警线）。

标注状态：10 对已完成人工复核（2026-09-18，对照 deepseek-flash 真实录制）。
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager

import pytest

import conftest
from app.diagnose import core
from app.fact_store import core as fact_store_core
from app.fact_store import io as fact_store_io
from app.ingest import mock_rules as ingest_mock_rules
from app.platform import config, db, llm
from app.shared.normalize import NORMALIZE_VERSION
from conftest import FIXTURES_DIR, canonical_of

ANNOTATED_DIR = FIXTURES_DIR / "annotated"
PAIRS_DIR = ANNOTATED_DIR / "pairs"
VCR_DIR = ANNOTATED_DIR / "vcr"

RECORD_VCR = os.environ.get("RECORD_VCR") == "1"

# 吻合率门槛（spec §13.2，2026-09-18 人工复核定稿后写回）：
# 分母只计非边界条目；当前基线 39/39 = 100%（VCR 回放确定性），
# 0.85 为 prompt/模型改动重录时的回归预警线，低于此值必须人工复核差异。
_MIN_STATUS_AGREEMENT = 0.85


def _pair_ids() -> list[str]:
    return sorted(path.name[:3] for path in PAIRS_DIR.glob("*_expected.json"))


def _read_pair(pair_id: str) -> tuple[str, str, dict]:
    resume_text = (PAIRS_DIR / f"{pair_id}_resume.txt").read_text(encoding="utf-8")
    jd_text = (PAIRS_DIR / f"{pair_id}_jd.txt").read_text(encoding="utf-8")
    expected = json.loads((PAIRS_DIR / f"{pair_id}_expected.json").read_text(encoding="utf-8"))
    return resume_text, jd_text, expected


def _build_confirmed_resume(resume_text: str) -> str:
    """简历 → canonical → mock 规则事实源 → 全部 confirmed（确定性，不依赖 DB 外部状态）。"""
    canonical = canonical_of(resume_text)
    facts = ingest_mock_rules.extract_facts(canonical)
    assert facts, "规则抽取应至少产出一条事实"
    resume_id = uuid.uuid4().hex
    conn = db.connect()
    try:
        with conn:
            fact_store_io.insert_resume(
                conn,
                resume_id=resume_id,
                filename=f"annotated_{resume_id}.txt",
                stored_path="annotated",
                parser="mock",
                normalize_version=NORMALIZE_VERSION,
                status="reviewing",
            )
            fact_store_io.insert_canonical(conn, resume_id, canonical)
            fact_ids = fact_store_core.store_extraction(conn, resume_id, canonical, facts)
            for fact_id in fact_ids:
                conn.execute(
                    "UPDATE facts SET confirmed = 1, attribution = 'individual' WHERE id = ?",
                    (fact_id,),
                )
            fact_store_io.set_resume_status(conn, resume_id, "confirmed")
    finally:
        conn.close()
    return resume_id


def _import_live_settings_from_main_db() -> None:
    """RECORD_VCR 录制用：把主库的 LLM 设置复制到隔离测试库。

    测试的 isolated_data_dir 会新建空 settings（无 key → mock），而录制必须走
    用户真实配置；本地单体下主库（<项目根>/data/resume_agent.db）是唯一可靠来源。
    """
    import sqlite3

    from pathlib import Path

    main_db = Path(__file__).resolve().parents[2] / "data" / "resume_agent.db"
    if not main_db.exists():
        pytest.skip(f"未找到主库 {main_db}，无法读取真实 LLM 设置")
    conn = sqlite3.connect(main_db)
    try:
        rows = {row[0]: row[1] for row in conn.execute("SELECT key, value FROM settings")}
    finally:
        conn.close()
    patch = {
        key: rows[key]
        for key in ("llm_base_url", "llm_api_key", "llm_model", "llm_mode")
        if key in rows
    }
    if not patch.get("llm_api_key", "").strip():
        pytest.skip("主库未配置 llm_api_key，无法录制真实响应（请先在设置页配置）")
    config.update_settings(patch)


@contextmanager
def _llm_source(pair_id: str, monkeypatch):
    """诊断链路的 LLM 响应来源：录制（RECORD_VCR=1）> VCR 回放 > mock 规则。"""
    vcr_path = VCR_DIR / f"{pair_id}.json"

    if RECORD_VCR:
        _import_live_settings_from_main_db()
        if config.is_mock():
            pytest.fail(
                "RECORD_VCR=1 需要真实模型：请在设置页配置 key 并置 llm_mode=live/auto"
            )
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
            yield
        finally:
            # 即使断言失败也保存已录制的调用（真实响应可用于复现分析）
            VCR_DIR.mkdir(parents=True, exist_ok=True)
            vcr_path.write_text(
                json.dumps({"pair": pair_id, "calls": calls}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return

    if vcr_path.exists():
        calls = json.loads(vcr_path.read_text(encoding="utf-8")).get("calls", [])
        state = {"n": 0}

        def playback(messages, **kwargs):
            index = state["n"]
            if index >= len(calls):
                raise RuntimeError(
                    f"VCR 录制不足（{pair_id}）：第 {index + 1} 次调用无录制，请重录"
                )
            state["n"] += 1
            return calls[index], False

        monkeypatch.setattr(llm, "chat_json", playback)
        yield
        return

    yield  # 无录制 → 走 mock 规则（无 key 环境）


def _match(actual_requirements: list[dict], spec: dict) -> dict | None:
    """按 text_contains 模糊匹配实际拆出的要求（取 req_index 最小者）。"""
    hits = [req for req in actual_requirements if spec["text_contains"] in req["text"]]
    return hits[0] if hits else None


@pytest.mark.parametrize("pair_id", _pair_ids())
def test_annotated_pair(pair_id: str, monkeypatch) -> None:
    resume_text, jd_text, expected = _read_pair(pair_id)
    resume_id = _build_confirmed_resume(resume_text)

    with _llm_source(pair_id, monkeypatch):
        events = list(core.iter_diagnosis(resume_id, jd_text))
    assert events[-1]["type"] == "done", events[-1]
    detail = core.load_diagnosis(events[-1]["diagnosis_id"])
    actual = detail["requirements"]

    unmatched: list[str] = []
    mismatches: list[tuple[str, str, str]] = []
    gap_mispredictions: list[str] = []  # 期望 direct/nearest 但实际 gap（可达错标缺口）
    boundary_seen: list[str] = []       # 边界条目：只记录实际状态，不进吻合率分母
    direct_ok = 0
    direct_total = 0

    for spec in expected["requirements"]:
        found = _match(actual, spec)
        if found is None:
            unmatched.append(spec["text_contains"])
            continue
        if spec.get("boundary"):
            boundary_seen.append(f"{spec['text_contains']}→{found['status']}")
            continue
        if spec["expect_status"] == "direct":
            direct_total += 1
        if found["status"] == spec["expect_status"]:
            if (
                found["status"] == "direct"
                and spec.get("expect_quote_contains")
                and spec["expect_quote_contains"] not in (found["quote"] or "")
            ):
                mismatches.append(
                    (spec["text_contains"], f"direct/quote~{spec['expect_quote_contains']}", found["quote"] or "")
                )
            elif found["status"] == "direct":
                direct_ok += 1
        else:
            mismatches.append((spec["text_contains"], spec["expect_status"], found["status"]))
            if spec["expect_status"] in ("direct", "nearest") and found["status"] == "gap":
                gap_mispredictions.append(spec["text_contains"])

    total = len(expected["requirements"])
    matched = total - len(unmatched)
    scored = matched - len(boundary_seen)   # 吻合率分母：非边界条目
    agreed = scored - len(mismatches)
    source = "VCR 录制" if (VCR_DIR / f"{pair_id}.json").exists() else "mock 规则"

    conftest.ANNOTATED_STATS.append(
        f"[{pair_id}] 来源={source}"
        f" 期望 {total} 条 · 匹配 {matched} · 状态吻合 {agreed}/{scored}"
        f" · direct {direct_ok}/{direct_total} · gap 误判 {len(gap_mispredictions)}"
        + (f" · 边界 {boundary_seen}" if boundary_seen else "")
        + (f" · 差异 {mismatches}" if mismatches else "")
        + (f" · 未拆出 {unmatched}" if unmatched else "")
    )

    # 硬断言：链路健全 + 拆解召回（要求不丢，边界条目也必须被拆出）
    assert not unmatched, f"[{pair_id}] 期望要求未被拆出：{unmatched}"
    assert matched >= 1
    assert scored > 0, f"[{pair_id}] 全部条目都是边界标记，标注集退化"
    assert agreed / scored >= _MIN_STATUS_AGREEMENT, (
        f"[{pair_id}] 状态吻合率过低：{agreed}/{scored}；"
        f"gap 误判 {gap_mispredictions}；差异 {mismatches}"
    )


def test_annotation_set_is_human_reviewed() -> None:
    """标注集完整性守卫（spec §13.2）：

    - 10 对起（M2 开工条件，spec §13.3）；
    - 不得残留 ``draft: true``——draft 会把吻合率门槛静默关掉，
      等于用草稿冒充标注；
    - 边界条目必须同时给出 ``boundary`` 与 ``comment``：边界不进分母，
      没有理由说明就成了"把不达标项藏起来"的后门。
    """
    pair_ids = _pair_ids()
    assert len(pair_ids) >= 10, "M2 开工条件：先 10 对（spec §13.3）"
    boundary_total = 0
    for pair_id in pair_ids:
        expected = json.loads(
            (PAIRS_DIR / f"{pair_id}_expected.json").read_text(encoding="utf-8")
        )
        assert expected.get("draft") is not True, f"[{pair_id}] 仍是草稿，不得计入已标注"
        assert expected.get("note"), f"[{pair_id}] 缺少复核说明 note"
        for spec in expected["requirements"]:
            if spec.get("boundary"):
                boundary_total += 1
                assert spec.get("comment"), (
                    f"[{pair_id}] 边界条目缺 comment：{spec['text_contains']}"
                )
    # 边界条目总量设上限：占比过高说明机制在系统性失守，而非个别边界
    assert boundary_total <= 5, f"边界条目过多（{boundary_total}），需重新评估机制而非继续标注"

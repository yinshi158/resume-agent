"""M3-2 改写校验链路（spec §15/§16/§22）。

覆盖：
- 目标清单分母修正（纯 gap 剔除进升级清单；user_revived 保留）；
- mock 全链路：改写出句 → 四层校验 → 重写轮 ≤2 → 升级清单 → 一次事务入库；
- 失败分流：可修复失败进入重写轮、2 轮用尽不重试、L2 不蕴含不重试直接升级；
- 判定持久化完整性（按句按轮；非法句跳过记 warning）。
"""

from __future__ import annotations

import json
import re

import conftest
import pytest

from app.diagnose import core as diagnose_core
from app.platform import db, llm
from app.rewrite import core

RESUME = conftest.load_fixture_text("01_single_column.txt")

# 最后一条要求（会员体系）在简历中无关键词重叠 → mock 诊断应产 gap，
# 用于验证"纯 gap 不进改写分母"。
JD = """岗位职责：
- 负责推荐系统的后端服务开发与性能优化
- 参与高并发系统的架构设计与稳定性保障
任职要求：
- 熟练掌握 Python，具备 3 年以上后端开发经验
- 熟悉 Redis 缓存与消息队列的使用
- 有分布式系统设计经验
- 具备大型电商平台会员体系运营经验
加分项：了解 Kubernetes 者优先
"""


def _run_to_done(diagnosis_id: str) -> tuple[dict, list[dict]]:
    events = list(core.iter_rewrite(diagnosis_id))
    assert events[-1]["type"] == "done", events[-1]
    return events[-1], events


# ---------------------------------------------------------------------------
# 目标清单（§15.3）
# ---------------------------------------------------------------------------

class TestComputeTargets:
    def _row(self, **overrides) -> dict:
        row = {
            "id": "r1",
            "req_index": 0,
            "priority": "must",
            "text": "要求",
            "keywords": "[]",
            "status": "direct",
            "quote": "q",
            "fact_id": "f1",
            "nearest_note": None,
            "user_revived": 0,
        }
        row.update(overrides)
        return row

    def test_gap_excluded_and_escalated(self) -> None:
        targets, escalations = core.compute_targets(
            [
                self._row(id="r1", status="direct"),
                self._row(id="r2", status="gap", quote=None, fact_id=None),
                self._row(id="r3", status="nearest"),
            ]
        )
        assert [t.requirement_id for t in targets] == ["r1", "r3"]
        assert [e.requirement_id for e in escalations] == ["r2"]
        assert escalations[0].kind == "requirement_gap"

    def test_revived_gap_kept_in_targets(self) -> None:
        targets, escalations = core.compute_targets(
            [self._row(id="r2", status="direct", user_revived=1, quote="q", fact_id="f9")]
        )
        assert [t.requirement_id for t in targets] == ["r2"]
        assert escalations == []


# ---------------------------------------------------------------------------
# mock 全链路
# ---------------------------------------------------------------------------

class TestMockPipeline:
    def test_mock_full_chain(self) -> None:
        resume_id = conftest.build_confirmed_resume(RESUME)
        diagnosis_id = conftest.build_diagnosis(resume_id, JD)
        detail = diagnose_core.load_diagnosis(diagnosis_id)

        expected_targets = [
            r["id"]
            for r in detail["requirements"]
            if r["status"] in ("direct", "nearest") or r["user_revived"]
        ]
        expected_gaps = [
            r["id"]
            for r in detail["requirements"]
            if r["status"] == "gap" and not r["user_revived"]
        ]
        assert expected_gaps, "该 JD 应至少产出一条纯 gap（测试前提）"

        done, events = _run_to_done(diagnosis_id)
        assert done["mock"] is True
        assert done["rounds"] == 1, "mock 演示句应触发恰好 1 轮重写"
        assert any(
            event["type"] == "step" and event.get("step") == "escalated" for event in events
        )

        rewrite = core.load_rewrite(done["rewrite_id"])
        assert rewrite["status"] == "escalated"
        assert rewrite["mock"] is True
        assert set(rewrite["target_requirement_ids"]) == set(expected_targets)

        # 升级清单 = 诊断缺口 + L2 不蕴含，且都与产出句/要求对得上
        kinds = [e["kind"] for e in rewrite["escalations"]]
        assert "requirement_gap" in kinds
        assert "l2_unentailed" in kinds
        gap_entries = [e for e in rewrite["escalations"] if e["kind"] == "requirement_gap"]
        assert {e["requirement_id"] for e in gap_entries} == set(expected_gaps)

        # 句集 = 目标句 + 2 个演示句（带哨兵数字 / 无支撑断言）
        sentences = rewrite["sentences"]
        assert len(sentences) == len(expected_targets) + 2
        assert all(s["gate_status"] == "pending" for s in sentences)

        # 演示句 ①：L0 拦截 → 重写轮修复（round 0 fail → round 1 pass）
        # 哨兵百分比按出处数字集合确定性选择，用"有多轮判定"定位该句
        retried = [s for s in sentences if len(s["validations"]) == 2]
        assert len(retried) == 1, "恰好一个句子被重写一轮"
        retried_rounds = {v["round"] for v in retried[0]["validations"]}
        assert retried_rounds == {0, 1}
        round0 = {l["layer"]: l["verdict"] for l in retried[0]["validations"][0]["layers"]}
        round1 = {l["layer"]: l["verdict"] for l in retried[0]["validations"][1]["layers"]}
        assert round0["L0"] == "fail", "演示句应在 L0 被拦下（偷渡数字）"
        assert all(v == "pass" for v in round1.values()), "重写轮应修复演示句"
        assert "提升" not in retried[0]["text"], "修复 = 回归出处原文"

        # 演示句 ②：L2 不蕴含 → 升级且不重试（只有 round 0 判定）
        l2_red = [
            s
            for s in sentences
            if len(s["validations"]) == 1
            and any(
                l["layer"] == "L2" and l["verdict"] == "fail"
                for l in s["validations"][0]["layers"]
            )
        ]
        assert len(l2_red) == 1
        escalation_ids = {
            e["sentence_id"] for e in rewrite["escalations"] if e["kind"] == "l2_unentailed"
        }
        assert l2_red[0]["id"] in escalation_ids

        # 判定持久化：每个（句 × 轮）恰好四层
        conn = db.connect()
        try:
            for sentence in sentences:
                rows = conn.execute(
                    "SELECT round, layer FROM sentence_validations WHERE sentence_id = ?",
                    (sentence["id"],),
                ).fetchall()
                rounds = {row["round"] for row in rows}
                assert rounds == {v["round"] for v in sentence["validations"]}
                assert len(rows) == 4 * len(rounds)
            rewrite_row = conn.execute(
                "SELECT mock, rounds FROM rewrites WHERE id = ?", (done["rewrite_id"],)
            ).fetchone()
            assert rewrite_row["mock"] == 1
            assert rewrite_row["rounds"] == 1
        finally:
            conn.close()

    def test_revived_requirement_enters_targets(self) -> None:
        """人工捞回（ard/0004）后该要求进入改写目标，不再进升级清单。"""
        resume_id = conftest.build_confirmed_resume(RESUME)
        diagnosis_id = conftest.build_diagnosis(resume_id, JD)
        detail = diagnose_core.load_diagnosis(diagnosis_id)
        gap = next(r for r in detail["requirements"] if r["status"] == "gap")
        fact_id = conftest.confirmed_resume_fact_ids(resume_id)[0]

        diagnose_core.revive_requirement(gap["id"], fact_id)
        done, _ = _run_to_done(diagnosis_id)
        rewrite = core.load_rewrite(done["rewrite_id"])

        assert gap["id"] in rewrite["target_requirement_ids"]
        gap_entries = [e for e in rewrite["escalations"] if e["kind"] == "requirement_gap"]
        assert gap["id"] not in {e["requirement_id"] for e in gap_entries}


# ---------------------------------------------------------------------------
# 失败分流（fake LLM，控制每一层结果）
# ---------------------------------------------------------------------------

def _install_fake_llm(
    monkeypatch,
    *,
    sentence_text: str,
    fact_id: str,
    l2_entailed: bool = True,
    source_fact_ids: list[str] | None = None,
) -> list[dict]:
    """替换 llm.chat_json：改写固定输出一句；L2 按参数返回。

    返回改写调用记录（messages 列表），供断言重试轮次。
    """
    rewrite_calls: list[dict] = []

    def fake_chat_json(messages, **kwargs):
        content = messages[-1]["content"]
        if "【待判定句子】" in content:
            ids = re.findall(r"^- \[([^\]]+)\]", content, re.MULTILINE)
            return (
                json.dumps(
                    {
                        "entailed": l2_entailed,
                        "supported_fact_ids": ids if l2_entailed else [],
                        "reason": "fake",
                    },
                    ensure_ascii=False,
                ),
                False,
            )
        rewrite_calls.append(messages)
        return (
            json.dumps(
                {
                    "sentences": [
                        {
                            "section": "work",
                            "text": sentence_text,
                            "source_fact_ids": source_fact_ids or [fact_id],
                            "derived_numbers": [],
                            "verbs": [],
                            "requirement_ids": [],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            False,
        )

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    return rewrite_calls


class TestTriage:
    def test_repairable_failure_retries_until_limit(self, monkeypatch) -> None:
        resume_id = conftest.build_confirmed_resume(RESUME)
        diagnosis_id = conftest.build_diagnosis(resume_id, JD)
        fact_id = conftest.confirmed_resume_fact_ids(resume_id)[0]
        calls = _install_fake_llm(
            monkeypatch,
            sentence_text="完成了 9987 万次请求的调度与优化",  # 9987 万不在事实源 → L0 恒失败
            fact_id=fact_id,
        )

        done, events = _run_to_done(diagnosis_id)
        assert done["rounds"] == core.MAX_RETRY_ROUNDS == 2
        assert len(calls) == core.MAX_RETRY_ROUNDS + 1, "初稿 1 次 + 重写 2 次"
        # 第二次重写的 prompt 必须携带判定反馈（判定 detail 进 prompt）
        assert "【上一轮未通过】" in calls[1][-1]["content"]
        assert "L0：" in calls[1][-1]["content"]

        rewrite = core.load_rewrite(done["rewrite_id"])
        assert len(rewrite["sentences"]) == 1
        sentence = rewrite["sentences"][0]
        assert [v["round"] for v in sentence["validations"]] == [0, 1, 2]
        final_layers = {l["layer"]: l["verdict"] for l in sentence["validations"][-1]["layers"]}
        assert final_layers["L0"] == "fail", "2 轮未过 → 保持标红交 gate2"

    def test_l2_failure_escalates_without_retry(self, monkeypatch) -> None:
        resume_id = conftest.build_confirmed_resume(RESUME)
        diagnosis_id = conftest.build_diagnosis(resume_id, JD)
        fact_id = conftest.confirmed_resume_fact_ids(resume_id)[0]
        quote = None
        conn = db.connect()
        try:
            quote = conn.execute(
                "SELECT raw_quote FROM facts WHERE id = ?", (fact_id,)
            ).fetchone()["raw_quote"]
        finally:
            conn.close()
        calls = _install_fake_llm(
            monkeypatch,
            sentence_text=quote,  # 确定性三层全过
            fact_id=fact_id,
            l2_entailed=False,
        )

        done, events = _run_to_done(diagnosis_id)
        assert done["rounds"] == 0, "L2 不蕴含不重试（spec §15.3）"
        assert len(calls) == 1
        assert not any(
            event["type"] == "step" and event.get("step") == "writing" and event.get("round", 0) > 0
            for event in events
        )
        rewrite = core.load_rewrite(done["rewrite_id"])
        assert any(e["kind"] == "l2_unentailed" for e in rewrite["escalations"])

    def test_invalid_sentence_skipped_with_warning(self, monkeypatch) -> None:
        resume_id = conftest.build_confirmed_resume(RESUME)
        diagnosis_id = conftest.build_diagnosis(resume_id, JD)
        fact_id = conftest.confirmed_resume_fact_ids(resume_id)[0]

        def fake_chat_json(messages, **kwargs):
            content = messages[-1]["content"]
            if "【待判定句子】" in content:
                ids = re.findall(r"^- \[([^\]]+)\]", content, re.MULTILINE)
                return (
                    json.dumps(
                        {"entailed": True, "supported_fact_ids": ids, "reason": "fake"},
                        ensure_ascii=False,
                    ),
                    False,
                )
            return (
                json.dumps(
                    {
                        "sentences": [
                            {  # 非法：source_fact_ids 为空 → 程序拒收
                                "section": "work",
                                "text": "无出处的句子",
                                "source_fact_ids": [],
                                "derived_numbers": [],
                                "verbs": [],
                                "requirement_ids": [],
                            },
                            {  # 合法
                                "section": "work",
                                "text": "负责推荐系统重构",
                                "source_fact_ids": [fact_id],
                                "derived_numbers": [],
                                "verbs": [],
                                "requirement_ids": [],
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                False,
            )

        monkeypatch.setattr(llm, "chat_json", fake_chat_json)
        done, _ = _run_to_done(diagnosis_id)
        assert done["warnings"], "应记录被拒收句子的 warning"
        assert any("被程序拒收" in w for w in done["warnings"])
        rewrite = core.load_rewrite(done["rewrite_id"])
        assert len(rewrite["sentences"]) == 1

    def test_diagnosis_404_and_edit_guard(self) -> None:
        with pytest.raises(KeyError):
            core.assert_ready("nonexistent-diagnosis")


class TestStorageDiscipline:
    def test_failed_llm_leaves_no_partial_rows(self, monkeypatch) -> None:
        resume_id = conftest.build_confirmed_resume(RESUME)
        diagnosis_id = conftest.build_diagnosis(resume_id, JD)

        def broken_chat_json(messages, **kwargs):
            raise RuntimeError("网络错误")

        monkeypatch.setattr(llm, "chat_json", broken_chat_json)
        events = list(core.iter_rewrite(diagnosis_id))
        assert events[-1]["type"] == "error"
        assert "网络错误" in events[-1]["message"]

        conn = db.connect()
        try:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM rewrites"
            ).fetchone()["n"]
            assert count == 0, "失败不留半成品（与 ingest/diagnose 同款纪律）"
        finally:
            conn.close()

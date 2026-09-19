"""M3-3/M3-4 集成测试（spec §17/§18/§19/§22 集成与验收行）。

覆盖：
- /api/rewrites SSE 与读取接口（含 404/422 前置校验）；
- gate2 动作 × 判定快照配对落库（gate_events）；
- 编辑重校验（确定性三层必跑；L2 按 R2 条件重跑或沿用）；
- 导出咽喉（pending → 422；全部处置 → PDF；Playwright 缺失 → 422 + 指引）；
- §18.2 ATS 回测：PDF 用 pymupdf 读回，章节标题与句集 100% 可读回；
- ledger 三表（applications/outcomes 追加式记录）。
"""

from __future__ import annotations

import json
import re

import conftest
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.platform import db

RESUME = conftest.load_fixture_text("01_single_column.txt")

JD = """岗位职责：
- 负责推荐系统的后端服务开发与性能优化
- 参与高并发系统的架构设计与稳定性保障
任职要求：
- 熟练掌握 Python，具备 3 年以上后端开发经验
- 熟悉 Redis 缓存与消息队列的使用
- 有分布式系统设计经验
加分项：了解 Kubernetes 者优先
"""


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
                break
    return events


def _prepare_rewrite() -> str:
    """确认简历 → mock 诊断 → mock 改写，返回 rewrite_id。"""
    resume_id = conftest.build_confirmed_resume(RESUME)
    diagnosis_id = conftest.build_diagnosis(resume_id, JD)
    from app.rewrite import core as rewrite_core

    events = list(rewrite_core.iter_rewrite(diagnosis_id))
    assert events[-1]["type"] == "done", events[-1]
    return events[-1]["rewrite_id"]


def _gate(client: TestClient, sentence_id: str, action: str, text: str | None = None):
    body: dict = {"action": action}
    if text is not None:
        body["text"] = text
    return client.post(f"/api/sentences/{sentence_id}/gate", json=body)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _playwright_ready() -> bool:
    from app.platform import config

    return config.playwright_available()


# ---------------------------------------------------------------------------
# rewrite API
# ---------------------------------------------------------------------------

class TestRewriteApi:
    def test_rewrite_sse_and_read(self) -> None:
        with TestClient(app) as client:
            resume_id = conftest.build_confirmed_resume(RESUME)
            diagnosis_id = conftest.build_diagnosis(resume_id, JD)
            resp = client.post("/api/rewrites", json={"diagnosis_id": diagnosis_id})
            assert resp.status_code == 200, resp.text
            events = _parse_sse(resp.text)
            assert events[-1]["type"] == "done", events
            rewrite_id = events[-1]["rewrite_id"]

            got = client.get(f"/api/rewrites/{rewrite_id}")
            assert got.status_code == 200
            payload = got.json()
            assert payload["diagnosis_id"] == diagnosis_id
            assert payload["resume_id"] == resume_id
            assert payload["mock"] is True
            assert payload["sentences"]
            sentence = payload["sentences"][0]
            assert sentence["source_fact_ids"]
            assert sentence["source_facts"], "出处对照（含 span 跳转坐标）应随读返回"

            listed = client.get(f"/api/diagnoses/{diagnosis_id}/rewrites")
            assert listed.status_code == 200
            assert listed.json()["rewrites"][0]["id"] == rewrite_id

            assert client.get("/api/rewrites/nonexistent").status_code == 404
            assert client.get("/api/diagnoses/nonexistent/rewrites").status_code == 404

    def test_rewrite_404_and_unconfirmed_422(self) -> None:
        with TestClient(app) as client:
            assert (
                client.post("/api/rewrites", json={"diagnosis_id": "nope"}).status_code == 404
            )
            resume_id = conftest.build_confirmed_resume(RESUME)
            diagnosis_id = conftest.build_diagnosis(resume_id, JD)
            # 事实源退回校对状态 → 改写入口 422（ard/0002 咽喉）
            conn = db.connect()
            try:
                with conn:
                    conn.execute(
                        "UPDATE resumes SET status = 'reviewing' WHERE id = ?", (resume_id,)
                    )
            finally:
                conn.close()
            resp = client.post("/api/rewrites", json={"diagnosis_id": diagnosis_id})
            assert resp.status_code == 422
            assert "校对" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# gate2：动作 × 快照配对 + 编辑重校验（R2）
# ---------------------------------------------------------------------------

class TestGate2:
    def test_actions_write_events_with_snapshot(self) -> None:
        rewrite_id = _prepare_rewrite()
        with TestClient(app) as client:
            payload = client.get(f"/api/rewrites/{rewrite_id}").json()
            sentences = payload["sentences"]

            all_pass = next(
                s
                for s in sentences
                if all(l["verdict"] == "pass" for l in s["validations"][-1]["layers"])
            )
            l2_red = next(
                s
                for s in sentences
                if any(
                    l["layer"] == "L2" and l["verdict"] == "fail"
                    for l in s["validations"][-1]["layers"]
                )
            )

            resp = _gate(client, all_pass["id"], "confirm")
            assert resp.status_code == 200
            assert resp.json()["gate_status"] == "confirmed"

            resp = _gate(client, l2_red["id"], "reject")
            assert resp.status_code == 200
            assert resp.json()["gate_status"] == "rejected"

            # 动作 × 判定快照配对可查（误报/漏报分析原料，ard/0006）
            conn = db.connect()
            try:
                events = conn.execute(
                    "SELECT * FROM gate_events WHERE sentence_id = ?", (l2_red["id"],)
                ).fetchall()
                assert len(events) == 1
                snapshot = json.loads(events[0]["validation_snapshot"])
                assert events[0]["action"] == "reject"
                assert events[0]["before_text"] is None
                assert {item["layer"] for item in snapshot} == {"L0", "verb", "L1", "L2"}
                assert any(
                    item["layer"] == "L2" and item["verdict"] == "fail" for item in snapshot
                )
                row = conn.execute(
                    "SELECT gate_status FROM rewrite_sentences WHERE id = ?",
                    (l2_red["id"],),
                ).fetchone()
                assert row["gate_status"] == "rejected"
            finally:
                conn.close()

    def test_edit_reruns_deterministic_and_reuses_l2(self) -> None:
        """轻量编辑（数字未变且相似度高）→ 确定性三层重跑，L2 沿用。"""
        rewrite_id = _prepare_rewrite()
        with TestClient(app) as client:
            payload = client.get(f"/api/rewrites/{rewrite_id}").json()
            sentence = payload["sentences"][0]
            edited = sentence["text"] + "（持续迭代）"
            resp = _gate(client, sentence["id"], "edit", edited)
            assert resp.status_code == 200
            body = resp.json()
            assert body["gate_status"] == "edited"
            assert body["text"] == edited
            assert body["l2_rerun"] is False

            conn = db.connect()
            try:
                rows = conn.execute(
                    "SELECT round, layer, verdict, detail FROM sentence_validations "
                    "WHERE sentence_id = ? ORDER BY round, rowid",
                    (sentence["id"],),
                ).fetchall()
            finally:
                conn.close()
            new_round = max(row["round"] for row in rows)
            assert new_round == 1, "重校验写新一轮"
            new_rows = [row for row in rows if row["round"] == new_round]
            assert {row["layer"] for row in new_rows} == {"L0", "verb", "L1", "L2"}
            l2_row = next(row for row in new_rows if row["layer"] == "L2")
            detail = json.loads(l2_row["detail"])
            assert detail.get("reused") is True, "未触发重跑时沿用原判定入快照"

            # 事件 pair：edit 的 before/after 都落库
            conn = db.connect()
            try:
                event = conn.execute(
                    "SELECT * FROM gate_events WHERE sentence_id = ? ORDER BY rowid DESC",
                    (sentence["id"],),
                ).fetchone()
            finally:
                conn.close()
            assert event["action"] == "edit"
            assert event["before_text"] == sentence["text"]
            assert event["after_text"] == edited

    def test_edit_number_change_reruns_l2(self) -> None:
        """数字 token 集合变化 → L2 重新判定（R2 条件 ①）。"""
        rewrite_id = _prepare_rewrite()
        with TestClient(app) as client:
            payload = client.get(f"/api/rewrites/{rewrite_id}").json()
            sentence = next(s for s in payload["sentences"] if re.search(r"\d", s["text"]))
            edited = re.sub(
                r"\d+", lambda m: str(int(m.group(0)) + 1), sentence["text"], count=1
            )
            assert edited != sentence["text"]
            resp = _gate(client, sentence["id"], "edit", edited)
            assert resp.status_code == 200
            assert resp.json()["l2_rerun"] is True

            conn = db.connect()
            try:
                rows = conn.execute(
                    "SELECT round, layer, detail FROM sentence_validations "
                    "WHERE sentence_id = ? ORDER BY round DESC, rowid",
                    (sentence["id"],),
                ).fetchall()
            finally:
                conn.close()
            l2_row = next(row for row in rows if row["layer"] == "L2")
            assert json.loads(l2_row["detail"]).get("reused") is not True

    def test_gate_validation_errors(self) -> None:
        rewrite_id = _prepare_rewrite()
        with TestClient(app) as client:
            payload = client.get(f"/api/rewrites/{rewrite_id}").json()
            sentence_id = payload["sentences"][0]["id"]
            assert _gate(client, "nope", "confirm").status_code == 404
            assert _gate(client, sentence_id, "edit", "   ").status_code == 400
            assert (
                client.post(
                    f"/api/sentences/{sentence_id}/gate", json={"action": "approve"}
                ).status_code
                == 422
            )  # action 枚举校验


# ---------------------------------------------------------------------------
# 导出（§17.2 咽喉 + §18 ATS 回测）
# ---------------------------------------------------------------------------

def _handle_all(client: TestClient, rewrite_id: str) -> dict:
    """处置全部句子：L2 标红句 reject，其余 confirm；返回导出前的 payload。"""
    payload = client.get(f"/api/rewrites/{rewrite_id}").json()
    for sentence in payload["sentences"]:
        l2_fail = any(
            l["layer"] == "L2" and l["verdict"] == "fail"
            for l in sentence["validations"][-1]["layers"]
        )
        resp = _gate(client, sentence["id"], "reject" if l2_fail else "confirm")
        assert resp.status_code == 200, resp.text
    return payload


class TestExport:
    def test_pending_blocks_export(self) -> None:
        rewrite_id = _prepare_rewrite()
        with TestClient(app) as client:
            resp = client.post("/api/exports", json={"rewrite_id": rewrite_id})
            assert resp.status_code == 422
            assert "未处置" in resp.json()["detail"]
            assert client.post("/api/exports", json={"rewrite_id": "nope"}).status_code == 404

    def test_export_pdf_readback_and_versions(self) -> None:
        if not _playwright_ready():
            pytest.skip("未安装 Playwright 浏览器（R4），跳过 PDF 导出验收")

        rewrite_id = _prepare_rewrite()
        with TestClient(app) as client:
            payload = _handle_all(client, rewrite_id)
            resp = client.post("/api/exports", json={"rewrite_id": rewrite_id})
            assert resp.status_code == 200, resp.text
            events = _parse_sse(resp.text)
            assert events[-1]["type"] == "done", events
            version_id = events[-1]["version_id"]

            # versions 表一行 + rewrite 状态 exported
            conn = db.connect()
            try:
                version = conn.execute(
                    "SELECT * FROM versions WHERE id = ?", (version_id,)
                ).fetchone()
                assert version["template"] == "ats-v1"
                assert version["rewrite_id"] == rewrite_id
                pdf_path = db.data_dir() / version["path"]
                assert pdf_path.exists()
                status = conn.execute(
                    "SELECT status FROM rewrites WHERE id = ?", (rewrite_id,)
                ).fetchone()["status"]
                assert status == "exported"
            finally:
                conn.close()

            # 下载接口
            download = client.get(f"/api/exports/{version_id}/file")
            assert download.status_code == 200
            assert download.content[:4] == b"%PDF"
            assert client.get("/api/exports/nonexistent/file").status_code == 404

            # §18.2 ATS 可解析性回测：pymupdf 读回
            import fitz

            doc = fitz.open(pdf_path)
            try:
                extracted = _normalize("\n".join(page.get_text() for page in doc))
                assert doc.page_count >= 1
                for title in ("个人简介", "工作经历", "项目经历", "专业技能"):
                    assert title in extracted, f"标准章节名「{title}」应可读回"
                for sentence in payload["sentences"]:
                    l2_fail = any(
                        l["layer"] == "L2" and l["verdict"] == "fail"
                        for l in sentence["validations"][-1]["layers"]
                    )
                    if l2_fail:
                        assert _normalize(sentence["text"]) not in extracted, (
                            "rejected 句不应出现在导出 PDF 中"
                        )
                    else:
                        assert _normalize(sentence["text"]) in extracted, (
                            f"句集应 100% 可读回：{sentence['text'][:30]}"
                        )
                assert "核心关键词" in extracted
            finally:
                doc.close()

    def test_ats_self_check_blocks_export(self) -> None:
        if not _playwright_ready():
            pytest.skip("未安装 Playwright 浏览器（R4），跳过 PDF 导出验收")

        rewrite_id = _prepare_rewrite()
        with TestClient(app) as client:
            payload = client.get(f"/api/rewrites/{rewrite_id}").json()
            first = payload["sentences"][0]
            # 编辑引入表格字符 → ATS 自检 fail → error，不出文件
            resp = _gate(client, first["id"], "edit", first["text"] + " | 表格残留")
            assert resp.status_code == 200
            for sentence in payload["sentences"][1:]:
                assert _gate(client, sentence["id"], "confirm").status_code == 200

            resp = client.post("/api/exports", json={"rewrite_id": rewrite_id})
            events = _parse_sse(resp.text)
            assert events[-1]["type"] == "error"
            assert "ATS 自检未通过" in events[-1]["message"]

            conn = db.connect()
            try:
                count = conn.execute("SELECT COUNT(*) AS n FROM versions").fetchone()["n"]
                assert count == 0, "自检失败不出文件、不记版本"
            finally:
                conn.close()

    def test_playwright_missing_gives_guidance(self, monkeypatch) -> None:
        rewrite_id = _prepare_rewrite()
        with TestClient(app) as client:
            _handle_all(client, rewrite_id)
            monkeypatch.setattr(
                "app.export.api.config.playwright_available", lambda: False
            )
            resp = client.post("/api/exports", json={"rewrite_id": rewrite_id})
            assert resp.status_code == 422
            assert "playwright" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# ledger（§19 只记录不分析）
# ---------------------------------------------------------------------------

class TestLedger:
    def test_application_and_outcomes(self) -> None:
        with TestClient(app) as client:
            assert (
                client.post(
                    "/api/applications",
                    json={"version_id": "nope", "company": "A", "position": "B"},
                ).status_code
                == 404
            )

            if not _playwright_ready():
                pytest.skip("未安装 Playwright 浏览器（R4），跳过投递记录验收")
            rewrite_id = _prepare_rewrite()
            _handle_all(client, rewrite_id)
            export_events = _parse_sse(
                client.post("/api/exports", json={"rewrite_id": rewrite_id}).text
            )
            version_id = export_events[-1]["version_id"]

            created = client.post(
                "/api/applications",
                json={
                    "version_id": version_id,
                    "company": "某科技公司",
                    "position": "后端工程师",
                    "channel": "Boss 直聘",
                },
            )
            assert created.status_code == 200
            application_id = created.json()["application"]["id"]

            appended = client.post(
                f"/api/applications/{application_id}/outcomes",
                json={"stage": "interview", "note": "一面"},
            )
            assert appended.status_code == 200
            assert [item["stage"] for item in appended.json()["outcomes"]] == ["interview"]

            client.post(
                f"/api/applications/{application_id}/outcomes",
                json={"stage": "offer"},
            )
            listed = client.get("/api/applications").json()["applications"]
            assert len(listed) == 1
            assert listed[0]["company"] == "某科技公司"
            assert [item["stage"] for item in listed[0]["outcomes"]] == [
                "interview",
                "offer",
            ]

            assert (
                client.post(
                    f"/api/applications/{application_id}/outcomes",
                    json={"stage": "hired"},
                ).status_code
                == 422
            )  # stage 枚举
            assert (
                client.post(
                    "/api/applications/nonexistent/outcomes", json={"stage": "offer"}
                ).status_code
                == 404
            )

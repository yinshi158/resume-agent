"""诊断全链路集成（spec §13.4 集成层，M2 交付）。

- 未 confirmed 简历拒绝诊断（422）、简历不存在（404）、空 JD（400）；
- mock LLM 全链路：上传 → 校对确认 → SSE 诊断 → done → 报告结构；
- revive 后报告确定性重算（基于 requirements 行，不重新调 LLM）；
- mock 溯源按行（v3 迁移 mock 列）；
- fixtures/mock_diagnosis.json 固定演示诊断快照（spec §12.8）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.platform import db
from conftest import FILES_DIR, FIXTURES_DIR, write_or_compare_snapshot

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF = "application/pdf"

# 演示 JD：对 01 单栏简历应产生 direct + gap 分流（mock 规则链路确定性）
DEMO_JD = """任职要求：
1. 3 年以上后端开发经验，精通 Python，熟悉高并发与分布式系统；
2. 有推荐系统相关经验优先；
3. 有 5 年以上团队管理经验。
"""

# 纯缺口 JD：全部 must 无证据 → worth_applying=false 演示（ard/0004）
ALL_GAP_JD = """任职要求：
1. 有 5 年以上团队管理经验；
2. 有大规模召回排序算法落地经验。
"""


def _upload(client: TestClient, filename: str = "single_column.pdf") -> str:
    path = FILES_DIR / filename
    mime = _DOCX if filename.endswith(".docx") else _PDF
    resp = client.post("/api/resumes", files={"file": (filename, path.read_bytes(), mime)})
    assert resp.status_code == 200, resp.text
    return resp.json()["resume_id"]


def _confirm_all(client: TestClient, resume_id: str) -> list[dict]:
    facts = client.get(f"/api/resumes/{resume_id}/facts").json()["facts"]
    for fact in facts:
        resp = client.patch(
            f"/api/facts/{fact['id']}", json={"attribution": "individual", "confirmed": True}
        )
        assert resp.status_code == 200, resp.text
    assert client.get(f"/api/resumes/{resume_id}").json()["status"] == "confirmed"
    return facts


def _parse_sse(text: str) -> list[dict]:
    events: list[dict] = []
    for block in text.split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
                break
    return events


def _run_diagnosis(
    client: TestClient, resume_id: str, jd_text: str = DEMO_JD
) -> tuple[str, list[dict]]:
    resp = client.post("/api/diagnoses", json={"resume_id": resume_id, "jd_text": jd_text})
    assert resp.status_code == 200, resp.text
    events = _parse_sse(resp.text)
    assert events, "SSE 应至少产出一个事件"
    assert events[-1]["type"] == "done", events
    return events[-1]["diagnosis_id"], events


# ---------------------------------------------------------------------------
# schema 迁移（v3 诊断 mock 溯源列；v4 只追加 M3 新表，不动历史表）
# ---------------------------------------------------------------------------

def test_schema_migration_keeps_history() -> None:
    conn = db.connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == db.SCHEMA_VERSION
        # v3 纪律：诊断 mock 列仍在（M3 迁移不改 M1/M2 历史表）
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(diagnoses)")}
        assert "mock" in columns
        # v4：M3 新表全部就位
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for name in (
            "rewrites",
            "rewrite_sentences",
            "sentence_validations",
            "gate_events",
            "versions",
            "applications",
            "outcomes",
        ):
            assert name in tables, f"缺少 M3 表：{name}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 前置校验
# ---------------------------------------------------------------------------

def test_diagnose_rejects_unconfirmed_resume() -> None:
    """未完成校对的简历拒绝诊断（422，咽喉顺序 ard/0002）。"""
    with TestClient(app) as client:
        resume_id = _upload(client)
        resp = client.post(
            "/api/diagnoses", json={"resume_id": resume_id, "jd_text": DEMO_JD}
        )
        assert resp.status_code == 422
        assert "校对" in resp.json()["detail"]


def test_diagnose_rejects_missing_resume_and_empty_jd() -> None:
    with TestClient(app) as client:
        resp = client.post("/api/diagnoses", json={"resume_id": "nope", "jd_text": DEMO_JD})
        assert resp.status_code == 404

        resume_id = _upload(client)
        _confirm_all(client, resume_id)
        resp = client.post(
            "/api/diagnoses", json={"resume_id": resume_id, "jd_text": "   "}
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# mock 全链路（SSE）
# ---------------------------------------------------------------------------

def test_diagnose_mock_full_chain_events_and_report() -> None:
    with TestClient(app) as client:
        resume_id = _upload(client)
        _confirm_all(client, resume_id)
        diagnosis_id, events = _run_diagnosis(client, resume_id)

        # SSE 事件序列：parsing → evidencing 0..N → reporting → done
        assert events[0] == {"type": "step", "step": "parsing", "message": events[0].get("message")}
        evidencing = [e for e in events if e.get("step") == "evidencing"]
        assert evidencing and all(e["total"] == len(evidencing) for e in evidencing)
        assert [e["index"] for e in evidencing] == list(range(len(evidencing)))
        assert any(e.get("step") == "reporting" for e in events)
        assert events[-1]["mock"] is True  # mock 链路如实标示（ard/0007）

        detail = client.get(f"/api/diagnoses/{diagnosis_id}").json()
        assert detail["mock"] is True
        assert detail["resume_id"] == resume_id
        assert detail["jd_text"].strip()

        summary = detail["summary"]
        assert summary["must_total"] >= 1
        assert summary["must_direct"] >= 1
        assert summary["must_gap"] >= 1
        # 有 direct 证据 → 值得投（并非全 must 缺口）
        assert summary["worth_applying"] is True

        statuses = {r["status"] for r in detail["requirements"]}
        assert statuses <= {"direct", "nearest", "gap"}
        # direct 条目必须带 quote，且 quote_span 可在 canonical 高亮
        for req in detail["requirements"]:
            if req["status"] == "direct":
                assert req["quote"]
                assert req["quote_span"] is not None
        # 硬性缺口清单与 summary 一致（M3 分母修正依据）
        gap_ids = {r["id"] for r in detail["requirements"] if r["status"] == "gap"}
        assert set(detail["gaps"]) <= gap_ids

        # gap 条目：诚实措辞 + 降级候选（不声称"最接近"，只作可考虑的候选）
        gap_req = next(r for r in detail["requirements"] if r["status"] == "gap")
        assert "未找到关键词重叠" in gap_req["nearest_note"]
        assert "捞回" in gap_req["nearest_note"]
        assert gap_req["candidate_facts"], "gap 应给出降级候选（捞回入口可见）"
        assert len(gap_req["candidate_facts"]) <= 3
        assert all(
            {"id", "section", "raw_quote"} <= set(item) for item in gap_req["candidate_facts"]
        )
        assert gap_req["candidate_facts"][0]["section"] == "work"  # 经历类优先
        # direct/nearest 条目不带候选字段（只有 gap 需要兜底捞回列表）
        for req in detail["requirements"]:
            if req["status"] != "gap":
                assert "candidate_facts" not in req


def test_diagnose_deterministic_between_runs() -> None:
    """同一简历 + 同一 JD 的诊断链路确定性（mock 无随机性）。"""
    with TestClient(app) as client:
        resume_id = _upload(client)
        _confirm_all(client, resume_id)
        first_id, _ = _run_diagnosis(client, resume_id)
        second_id, _ = _run_diagnosis(client, resume_id)
        first = client.get(f"/api/diagnoses/{first_id}").json()
        second = client.get(f"/api/diagnoses/{second_id}").json()

        def normalize(detail: dict) -> list[dict]:
            return [
                {k: r[k] for k in ("priority", "text", "status", "quote", "nearest_note")}
                for r in detail["requirements"]
            ]

        assert normalize(first) == normalize(second)
        assert first["summary"] == second["summary"]


def test_diagnose_all_must_gap_not_worth_applying() -> None:
    with TestClient(app) as client:
        resume_id = _upload(client)
        _confirm_all(client, resume_id)
        diagnosis_id, _ = _run_diagnosis(client, resume_id, jd_text=ALL_GAP_JD)
        detail = client.get(f"/api/diagnoses/{diagnosis_id}").json()
        assert detail["summary"]["worth_applying"] is False
        assert "可能不值得投" in (detail["summary"]["notice"] or "")


def test_diagnose_list_per_resume() -> None:
    with TestClient(app) as client:
        resume_id = _upload(client)
        _confirm_all(client, resume_id)
        _run_diagnosis(client, resume_id)
        _run_diagnosis(client, resume_id, jd_text=ALL_GAP_JD)

        rows = client.get(f"/api/resumes/{resume_id}/diagnoses").json()["diagnoses"]
        assert len(rows) == 2
        assert rows[0]["created_at"] >= rows[1]["created_at"]
        assert all(row["mock"] is True and row["summary"] for row in rows)

        resp = client.get("/api/resumes/nope/diagnoses")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 人工捞回（ard/0004）
# ---------------------------------------------------------------------------

def test_revive_gap_recomputes_report_deterministically() -> None:
    with TestClient(app) as client:
        resume_id = _upload(client)
        facts = _confirm_all(client, resume_id)
        diagnosis_id, _ = _run_diagnosis(client, resume_id)
        before = client.get(f"/api/diagnoses/{diagnosis_id}").json()
        target = next(r for r in before["requirements"] if r["status"] == "gap")

        # 用户确认"最接近的经历"其实相关 → 捞回
        fact = next(f for f in facts if "带领" in f["raw_quote"])
        resp = client.post(
            f"/api/requirements/{target['id']}/revive", json={"fact_id": fact["id"]}
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["requirement"]["status"] == "direct"
        assert payload["requirement"]["user_revived"] is True
        assert payload["requirement"]["quote"] == fact["raw_quote"]

        # 报告重算：基于 requirements 行，确定性更新（不重新调 LLM）
        after = client.get(f"/api/diagnoses/{diagnosis_id}").json()
        assert after["summary"]["must_gap"] == before["summary"]["must_gap"] - 1
        assert after["summary"]["must_direct"] == before["summary"]["must_direct"] + 1
        assert target["id"] not in after["gaps"]
        assert target["id"] not in {r["id"] for r in after["requirements"] if r["status"] == "gap"}

        # 重复捞回幂等
        resp = client.post(
            f"/api/requirements/{target['id']}/revive", json={"fact_id": fact["id"]}
        )
        assert resp.status_code == 200
        assert client.get(f"/api/diagnoses/{diagnosis_id}").json()["summary"] == after["summary"]


def test_revive_rejects_foreign_fact_and_missing_ids() -> None:
    with TestClient(app) as client:
        resume_id = _upload(client)
        _confirm_all(client, resume_id)
        diagnosis_id, _ = _run_diagnosis(client, resume_id)
        detail = client.get(f"/api/diagnoses/{diagnosis_id}").json()
        target = next(r for r in detail["requirements"] if r["status"] == "gap")

        # 不属于该诊断对应简历的事实条目 → 400
        other_id = _upload(client, "two_column.pdf")
        other_facts = client.get(f"/api/resumes/{other_id}/facts").json()["facts"]
        resp = client.post(
            f"/api/requirements/{target['id']}/revive", json={"fact_id": other_facts[0]["id"]}
        )
        assert resp.status_code == 400

        # 要求不存在 / 事实不存在 → 404
        resp = client.post(
            f"/api/requirements/nope/revive", json={"fact_id": other_facts[0]["id"]}
        )
        assert resp.status_code == 404
        resp = client.post(
            f"/api/requirements/{target['id']}/revive", json={"fact_id": "nope"}
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# mock 溯源按行（v3 迁移列，呼应 M1 审查 P3）
# ---------------------------------------------------------------------------

def test_diagnosis_mock_provenance_is_per_row() -> None:
    with TestClient(app) as client:
        resume_id = _upload(client)
        _confirm_all(client, resume_id)
        diagnosis_id, _ = _run_diagnosis(client, resume_id)

        # 切到 live：全局 mock=False，历史诊断仍如实记录为 mock 产物
        client.put("/api/settings", json={"llm_mode": "live"})
        assert client.get("/api/settings").json()["mock"] is False
        assert client.get(f"/api/diagnoses/{diagnosis_id}").json()["mock"] is True

        conn = db.connect()
        try:
            row = conn.execute(
                "SELECT mock FROM diagnoses WHERE id = ?", (diagnosis_id,)
            ).fetchone()
            assert row["mock"] == 1
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 固定演示快照（spec §12.8 fixtures/mock_diagnosis.json）
# ---------------------------------------------------------------------------

def test_mock_diagnosis_fixture_snapshot() -> None:
    with TestClient(app) as client:
        resume_id = _upload(client)
        _confirm_all(client, resume_id)
        diagnosis_id, _ = _run_diagnosis(client, resume_id)
        detail = client.get(f"/api/diagnoses/{diagnosis_id}").json()

    payload = {
        "source": "01_single_column.pdf + DEMO_JD（见 test_diagnose_api.py）",
        "note": "mock 模式固定诊断（规则拆解 + 规则举证产物）；质量不代表真实水平；不含随机 id",
        "summary": detail["summary"],
        "requirements": [
            {
                "priority": req["priority"],
                "text": req["text"],
                "status": req["status"],
                "quote": req["quote"],
                "nearest_note": req["nearest_note"],
            }
            for req in detail["requirements"]
        ],
    }
    path = FIXTURES_DIR / "mock_diagnosis.json"
    if write_or_compare_snapshot(path, payload):
        pytest.skip(f"固定诊断首次生成：{path.name}")

"""span 回验 100% 可回查 + 上传→解析→校对写回全链路（mock LLM，spec §10）。

集成验收：fixtures 中文简历（单栏/双栏/表格/量化）跑通，
每条事实的 span 都能在 canonical_text 上回查（svc §7 验收标准 1.1）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingest import mock_rules
from app.main import app
from app.platform import config
from app.platform.offsets import utf16_to_cp
from app.shared.normalize import find_quote
from conftest import (
    FILES_DIR,
    FIXTURES_DIR,
    canonical_of,
    fixture_names,
    load_fixture_text,
    write_or_compare_snapshot,
)

_PDF = "application/pdf"
_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def upload(client: TestClient, filename: str) -> dict:
    path = FILES_DIR / filename
    mime = _DOCX if filename.endswith(".docx") else _PDF
    resp = client.post("/api/resumes", files={"file": (filename, path.read_bytes(), mime)})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 规则抽取的 quote 必须全部可回查（纯逻辑层）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", fixture_names())
def test_rule_extraction_all_quotes_locatable(name: str) -> None:
    canonical = canonical_of(load_fixture_text(name))
    facts = mock_rules.extract_facts(canonical)
    assert facts, "规则抽取应至少产出一条"
    for item in facts:
        assert find_quote(canonical, item["quote"]) is not None, f"定位失败：{item['quote']}"


def test_mock_extraction_fixture_snapshot() -> None:
    """fixtures/mock_extraction.json 与规则抽取输出一致（mock 固定结果保护）。"""
    canonical = canonical_of(load_fixture_text("01_single_column.txt"))
    payload = {
        "source": "01_single_column.txt",
        "note": "mock 模式固定抽取结果（规则抽取产物）；质量不代表真实水平",
        "facts": mock_rules.extract_facts(canonical),
    }
    path = FIXTURES_DIR / "mock_extraction.json"
    if write_or_compare_snapshot(path, payload):
        pytest.skip(f"固定结果首次生成：{path.name}")


# ---------------------------------------------------------------------------
# 上传 → 解析 → 回验 → 校对写回 全链路
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename",
    ["single_column.pdf", "two_column.pdf", "with_table.pdf", "quantified.pdf", "sample.docx"],
)
def test_upload_parse_facts_all_verifiable(filename: str) -> None:
    with TestClient(app) as client:
        data = upload(client, filename)
        assert data["mock"] is True
        resume_id = data["resume_id"]

        detail = client.get(f"/api/resumes/{resume_id}").json()
        assert detail["status"] == "reviewing"
        assert detail["mock"] is True
        assert detail["normalize_version"] >= 1
        canonical = detail["canonical_text"]
        assert canonical.strip()

        facts = client.get(f"/api/resumes/{resume_id}/facts").json()["facts"]
        assert facts, "应至少抽取出一条事实"
        for fact in facts:
            assert fact["span_start"] >= 0, f"quote 回验失败：{fact['raw_quote']}"
            # API span 为 UTF-16 偏移，换算回码点后必须逐字命中
            start = utf16_to_cp(canonical, fact["span_start"])
            end = utf16_to_cp(canonical, fact["span_end"])
            assert canonical[start:end] == fact["raw_quote"]


def test_quantified_fixture_anomaly_flags() -> None:
    """04 号量化简历应触发：日期颠倒、日期重叠、百分比超界、金额量级。"""
    with TestClient(app) as client:
        resume_id = upload(client, "quantified.pdf")["resume_id"]
        facts = client.get(f"/api/resumes/{resume_id}/facts").json()["facts"]
        labels = {flag["rule"] for fact in facts for flag in fact["anomaly_flags"]}
        assert {"date_order", "date_overlap", "percent_bound", "amount_magnitude"} <= labels
        # 每条异象带人类可读说明（gate1 tooltip 直接用）
        for fact in facts:
            for flag in fact["anomaly_flags"]:
                assert flag["detail"].get("message")


def test_patch_fact_requires_attribution_confirmation() -> None:
    with TestClient(app) as client:
        resume_id = upload(client, "single_column.pdf")["resume_id"]
        facts = client.get(f"/api/resumes/{resume_id}/facts").json()["facts"]
        target = facts[0]

        # mock 抽取默认 attribution=unknown 未表态 → 不能直接完成校对
        resp = client.patch(f"/api/facts/{target['id']}", json={"confirmed": True})
        assert resp.status_code == 422
        assert "归因" in resp.json()["detail"]

        # 表态后可确认
        resp = client.patch(f"/api/facts/{target['id']}", json={"attribution": "individual"})
        assert resp.status_code == 200
        assert resp.json()["attribution_confirmed"] is True

        resp = client.patch(f"/api/facts/{target['id']}", json={"confirmed": True})
        assert resp.status_code == 200
        assert resp.json()["confirmed"] is True


def test_all_confirmed_advances_and_rolls_back_resume_status() -> None:
    with TestClient(app) as client:
        resume_id = upload(client, "single_column.pdf")["resume_id"]
        facts = client.get(f"/api/resumes/{resume_id}/facts").json()["facts"]

        for fact in facts:
            resp = client.patch(
                f"/api/facts/{fact['id']}",
                json={"attribution": "individual", "confirmed": True},
            )
            assert resp.status_code == 200, resp.text
        assert client.get(f"/api/resumes/{resume_id}").json()["status"] == "confirmed"

        # 取消一条确认 → 状态回退 reviewing（保守：未全部确认不算完成）
        resp = client.patch(f"/api/facts/{facts[0]['id']}", json={"confirmed": False})
        assert resp.status_code == 200
        assert client.get(f"/api/resumes/{resume_id}").json()["status"] == "reviewing"


def test_edited_payload_does_not_touch_canonical() -> None:
    with TestClient(app) as client:
        resume_id = upload(client, "with_table.pdf")["resume_id"]
        canonical_before = client.get(f"/api/resumes/{resume_id}").json()["canonical_text"]
        facts = client.get(f"/api/resumes/{resume_id}/facts").json()["facts"]

        payload = {"fields": {"school": "北京邮电大学（更正）"}}
        resp = client.patch(f"/api/facts/{facts[0]['id']}", json={"edited_payload": payload})
        assert resp.status_code == 200
        assert resp.json()["edited_payload"] == payload
        # 原文层不变（ard/0001）
        assert client.get(f"/api/resumes/{resume_id}").json()["canonical_text"] == canonical_before


def test_resolve_anomaly_flag() -> None:
    with TestClient(app) as client:
        resume_id = upload(client, "quantified.pdf")["resume_id"]
        facts = client.get(f"/api/resumes/{resume_id}/facts").json()["facts"]
        flag = next(flag for fact in facts for flag in fact["anomaly_flags"])

        resp = client.post(f"/api/anomaly-flags/{flag['id']}/resolve")
        assert resp.status_code == 200
        assert resp.json()["resolved"] is True

        facts = client.get(f"/api/resumes/{resume_id}/facts").json()["facts"]
        target = next(f for f in facts for fl in f["anomaly_flags"] if fl["id"] == flag["id"])
        resolved = next(fl for fl in target["anomaly_flags"] if fl["id"] == flag["id"])
        assert resolved["resolved"] is True


# ---------------------------------------------------------------------------
# 重解析逃生门 / 增量模式准入
# ---------------------------------------------------------------------------

def test_reparse_without_mineru_returns_400(monkeypatch) -> None:
    monkeypatch.setattr(config, "mineru_available", lambda: False)
    with TestClient(app) as client:
        resume_id = upload(client, "two_column.pdf")["resume_id"]
        resp = client.post(f"/api/resumes/{resume_id}/reparse?parser=mineru")
        assert resp.status_code == 400
        assert "MinerU" in resp.json()["detail"]


def test_reupload_same_file_creates_new_row_and_allows_marked_only() -> None:
    """重解析/再导入产生新 resume 行（不覆盖旧行）；第二个版本可用"只看标记"模式。"""
    with TestClient(app) as client:
        first_id = upload(client, "single_column.pdf")["resume_id"]
        assert client.get(f"/api/resumes/{first_id}").json()["allow_marked_only"] is False

        facts = client.get(f"/api/resumes/{first_id}/facts").json()["facts"]
        for fact in facts:
            client.patch(f"/api/facts/{fact['id']}", json={"attribution": "individual", "confirmed": True})

        second_id = upload(client, "single_column.pdf")["resume_id"]
        assert second_id != first_id
        detail = client.get(f"/api/resumes/{second_id}").json()
        assert detail["allow_marked_only"] is True
        # 旧行仍可访问（版本不被覆盖）
        assert client.get(f"/api/resumes/{first_id}").json()["status"] == "confirmed"


def test_upload_rejects_unsupported_format() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/resumes",
            files={"file": ("resume.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400


def test_resume_mock_flag_is_per_row_not_global() -> None:
    """mock 来源按行记录（parser='mock' 枚举）：切换全局模式不影响历史版本（审查 P3）。"""
    with TestClient(app) as client:
        resume_id = upload(client, "single_column.pdf")["resume_id"]
        detail = client.get(f"/api/resumes/{resume_id}").json()
        assert detail["mock"] is True
        assert detail["parser"] == "mock"

        # 切到 live（强制真实模型）：全局 mock=False，但该版本仍如实记录为 mock 解析产物
        client.put("/api/settings", json={"llm_mode": "live"})
        assert client.get("/api/settings").json()["mock"] is False
        detail = client.get(f"/api/resumes/{resume_id}").json()
        assert detail["mock"] is True
        assert detail["parser"] == "mock"


def test_mineru_subprocess_decodes_utf8(monkeypatch, tmp_path) -> None:
    """MinerU 子进程输出显式按 UTF-8 解码（不再依赖 Windows locale，审查 P3）。"""
    import subprocess

    from app.ingest import io as ingest_io

    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        outdir = Path(cmd[cmd.index("-o") + 1])
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "demo.md").write_text("## 中文标题\n\n正文", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="解析完成：中文日志", stderr="")

    monkeypatch.setattr(ingest_io.shutil, "which", lambda name: "mineru")
    monkeypatch.setattr(ingest_io.subprocess, "run", fake_run)

    pdf = FILES_DIR / "single_column.pdf"
    assert ingest_io.extract_with_mineru(pdf) == "## 中文标题\n\n正文"
    # 锁定修复：显式 UTF-8（locale 解码会对 UTF-8 中文抛 UnicodeDecodeError）
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"


def test_settings_mock_roundtrip() -> None:
    with TestClient(app) as client:
        data = client.get("/api/settings").json()
        assert data["mock"] is True  # 无 key → mock
        assert data["llm_api_key_set"] is False

        resp = client.put("/api/settings", json={"llm_mode": "mock", "llm_model": "deepseek-chat"})
        assert resp.status_code == 200
        assert resp.json()["llm_mode"] == "mock"
        assert resp.json()["mock"] is True


# ---------------------------------------------------------------------------
# 审查修复回归（P2 quote 歧义 / P3 mock 溯源）
# ---------------------------------------------------------------------------

def test_duplicate_quote_is_not_anchored_and_flagged() -> None:
    """quote 在原文出现多次 → span (-1,-1) 不锚定 + missing_field 歧义标记（审查 P2）。"""
    from app.fact_store import core as fact_store_core
    from app.fact_store import io as fact_store_io
    from app.platform import db

    from app.shared.normalize import normalize

    # canonical_text 必须已过 normalize（真实入库路径如此：全角"："→半角）
    canonical = normalize("技能：Python\n项目一：用 Python 做推荐\n项目二：用 Python 做搜索")
    conn = db.connect()
    try:
        with conn:
            fact_store_io.insert_resume(
                conn,
                resume_id="r-dup",
                filename="dup.txt",
                stored_path="dup.txt",
                parser="pymupdf4llm",
                normalize_version=1,
                status="reviewing",
            )
            fact_store_io.insert_canonical(conn, "r-dup", canonical)
            fact_ids = fact_store_core.store_extraction(
                conn,
                "r-dup",
                canonical,
                [
                    {
                        "section": "skill",
                        "quote": "Python",  # 出现 3 次
                        "fields": {"items": ["Python"]},
                        "date_interpretations": [],
                        "attribution": "unknown",
                        "entities": {"numbers": [], "orgs": [], "skills": ["Python"]},
                    },
                    {
                        "section": "project",
                        "quote": "项目一：用 Python 做推荐",  # 唯一
                        "fields": {"name": "项目一"},
                        "date_interpretations": [],
                        "attribution": "unknown",
                        "entities": {"numbers": [], "orgs": [], "skills": ["Python"]},
                    },
                ],
            )
    finally:
        conn.close()

    facts = fact_store_core.load_facts("r-dup")
    by_id = {fact.id: fact for fact in facts}
    dup = by_id[fact_ids[0]]
    # 歧义：不锚定 + 歧义标记
    assert (dup.span_start, dup.span_end) == (-1, -1)
    assert any(
        flag.rule == "missing_field" and "无法唯一定位" in flag.detail.get("message", "")
        for flag in dup.anomaly_flags
    )
    # 唯一匹配：正常锚定，无歧义标记
    uniq = by_id[fact_ids[1]]
    assert uniq.span_start >= 0
    assert not any("无法唯一定位" in flag.detail.get("message", "") for flag in uniq.anomaly_flags)


def test_mock_provenance_recorded_per_resume() -> None:
    """mock 溯源按行记录：mock 解析的 resume 此后如实标示（审查 P3）。"""
    with TestClient(app) as client:
        resume_id = upload(client, "single_column.pdf")["resume_id"]
        detail = client.get(f"/api/resumes/{resume_id}").json()
        assert detail["mock"] is True
        assert detail["parser"] == "mock"

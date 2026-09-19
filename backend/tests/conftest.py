"""测试公共夹具。

- 每个测试用独立的临时数据目录（``RESUME_AGENT_DATA_DIR``），互不污染；
- 提供 fixtures 文本加载与 canonical_text 计算（与 ingest 同一链路：
  sanitize → normalize）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = TESTS_DIR / "fixtures"
TEXTS_DIR = FIXTURES_DIR / "resumes_text"
FILES_DIR = FIXTURES_DIR / "files"
SNAPSHOTS_DIR = FIXTURES_DIR / "snapshots"

UPDATE_SNAPSHOTS = os.environ.get("UPDATE_SNAPSHOTS") == "1"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """隔离 SQLite 与上传目录。"""
    monkeypatch.setenv("RESUME_AGENT_DATA_DIR", str(tmp_path / "data"))
    from app.platform import db

    db.init_db()
    yield


def load_fixture_text(name: str) -> str:
    return (TEXTS_DIR / name).read_text(encoding="utf-8")


def fixture_names() -> list[str]:
    return sorted(path.name for path in TEXTS_DIR.glob("*.txt"))


def canonical_of(text: str) -> str:
    """完整前处理链路（与 ingest 一致）：sanitize 清洗 → normalize。"""
    from app.ingest import sanitize
    from app.shared.normalize import normalize

    cleaned, _ = sanitize.clean(text)
    return normalize(cleaned)


def write_or_compare_snapshot(path: Path, payload: dict) -> bool:
    """快照写入或对比。

    :returns: ``True`` = 本次为新写入（调用方应跳过断言）；``False`` = 已对比。
    """
    import json

    if UPDATE_SNAPSHOTS or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert payload == expected, (
        f"快照不一致：{path.name}\n"
        "canonical_text / 偏移发生变化——若属 normalize/sanitize 的意图改动，"
        "必须 NORMALIZE_VERSION +1 并全量重入库（ard/0001），"
        "然后以 UPDATE_SNAPSHOTS=1 重新生成快照"
    )
    return False


# ---------------------------------------------------------------------------
# M3 公共夹具：确认简历 / 诊断
# ---------------------------------------------------------------------------

def build_confirmed_resume(
    resume_text: str,
    *,
    attribution: str = "individual",
    attribution_confirmed: bool = True,
) -> str:
    """简历文本 → canonical → mock 规则事实源 → 全部 confirmed（确定性，不依赖 DB 外部状态）。

    归因默认 individual + 已确认：M3 改写/校验链路的测试需要可主导的出处
    （verb 层要求主导级动词配 individual 出处）；归因维度的矩阵测试在
    test_validate_layers.py 单独覆盖。
    """
    import uuid

    from app.fact_store import core as fact_store_core
    from app.fact_store import io as fact_store_io
    from app.ingest import mock_rules as ingest_mock_rules
    from app.platform import db
    from app.shared.normalize import NORMALIZE_VERSION

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
                filename=f"test_{resume_id}.txt",
                stored_path="test",
                parser="mock",
                normalize_version=NORMALIZE_VERSION,
                status="reviewing",
            )
            fact_store_io.insert_canonical(conn, resume_id, canonical)
            fact_ids = fact_store_core.store_extraction(conn, resume_id, canonical, facts)
            for fact_id in fact_ids:
                conn.execute(
                    "UPDATE facts SET confirmed = 1, attribution = ?, attribution_confirmed = ? WHERE id = ?",
                    (attribution, int(attribution_confirmed), fact_id),
                )
            fact_store_io.set_resume_status(conn, resume_id, "confirmed")
    finally:
        conn.close()
    return resume_id


def confirmed_resume_fact_ids(resume_id: str) -> list[str]:
    from app.fact_store import io as fact_store_io
    from app.platform import db

    conn = db.connect()
    try:
        return [
            row["id"]
            for row in fact_store_io.fetch_facts(conn, resume_id)
            if row["confirmed"]
        ]
    finally:
        conn.close()


def build_diagnosis(resume_id: str, jd_text: str) -> str:
    """跑完整诊断链路（mock/真实按当前设置），返回 diagnosis_id。"""
    from app.diagnose import core as diagnose_core

    events = list(diagnose_core.iter_diagnosis(resume_id, jd_text))
    assert events[-1]["type"] == "done", events[-1]
    return events[-1]["diagnosis_id"]


def import_live_settings_from_main_db() -> None:
    """VCR 录制用：把主库的 LLM 设置复制到隔离测试库（M2 同款做法）。

    测试的 isolated_data_dir 会新建空 settings（无 key → mock），而录制必须走
    用户真实配置；本地单体下主库（<项目根>/data/resume_agent.db）是唯一可靠来源。
    """
    import sqlite3

    main_db = Path(__file__).resolve().parents[2] / "data" / "resume_agent.db"
    if not main_db.exists():
        import pytest

        pytest.skip(f"未找到主库 {main_db}，无法读取真实 LLM 设置")
    from app.platform import config

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
        import pytest

        pytest.skip("主库未配置 llm_api_key，无法录制真实响应（请先在设置页配置）")
    config.update_settings(patch)


# ---------------------------------------------------------------------------
# 回归统计（标注集 / L2；spec §13.2、§16.5 趋势可观测）
# ---------------------------------------------------------------------------

ANNOTATED_STATS: list[str] = []
L2_STATS: list[str] = []


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ARG001
    if ANNOTATED_STATS:
        terminalreporter.write_sep("-", "诊断标注集回归统计（spec §13.2）")
        for line in ANNOTATED_STATS:
            terminalreporter.write_line(line)
        ANNOTATED_STATS.clear()
    if L2_STATS:
        terminalreporter.write_sep("-", "L2 标注集回归统计（spec §16.5）")
        for line in L2_STATS:
            terminalreporter.write_line(line)
        L2_STATS.clear()

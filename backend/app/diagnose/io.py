"""diagnose 的 SQLite 读写（连接由调用方管理）。"""

from __future__ import annotations

import json
import sqlite3

from ..fact_store.io import now_iso
from .schemas import FactView


# ---------------------------------------------------------------------------
# facts → FactView（举证/最近邻使用；仅 confirmed 条目）
# ---------------------------------------------------------------------------

def _flatten(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_flatten(item))
        return parts
    if isinstance(value, dict):
        parts = []
        for item in value.values():
            parts.extend(_flatten(item))
        return parts
    return [str(value)]


def _searchable_text(row: sqlite3.Row, payload: dict) -> str:
    """确定性检索文本：原文 + 结构化字段 + 实体（供关键词重叠排序）。"""
    parts: list[str] = [row["raw_quote"]]
    for value in (payload.get("fields") or {}).values():
        parts.extend(_flatten(value))
    entities = payload.get("entities") or {}
    parts.extend(_flatten(entities.get("skills")))
    parts.extend(_flatten(entities.get("orgs")))
    return " ".join(part for part in parts if part)


def _row_to_fact_view(row: sqlite3.Row) -> FactView:
    # 人工修正优先（edited_payload 不改原文层，ard/0001）
    payload = json.loads(row["edited_payload"] or row["payload"])
    return FactView(
        fact_id=row["id"],
        section=row["section"],
        raw_quote=row["raw_quote"],
        searchable_text=_searchable_text(row, payload),
        locatable=row["span_start"] >= 0,
    )


def fetch_confirmed_facts(conn: sqlite3.Connection, resume_id: str) -> list[FactView]:
    """该简历的全部已确认事实条目（诊断只消费 confirmed 数据，ard/0002）。"""
    rows = conn.execute(
        "SELECT * FROM facts WHERE resume_id = ? AND confirmed = 1 "
        "ORDER BY span_start, created_at",
        (resume_id,),
    ).fetchall()
    return [_row_to_fact_view(row) for row in rows]


# ---------------------------------------------------------------------------
# diagnoses
# ---------------------------------------------------------------------------

def insert_diagnosis(
    conn: sqlite3.Connection,
    *,
    diagnosis_id: str,
    resume_id: str,
    jd_text: str,
    report_json: str,
    mock: bool,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO diagnoses(id, resume_id, jd_text, report, mock, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (diagnosis_id, resume_id, jd_text, report_json, int(mock), created_at or now_iso()),
    )


def fetch_diagnosis(conn: sqlite3.Connection, diagnosis_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM diagnoses WHERE id = ?", (diagnosis_id,)).fetchone()


def fetch_diagnoses(conn: sqlite3.Connection, resume_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM diagnoses WHERE resume_id = ? ORDER BY created_at DESC, rowid DESC",
        (resume_id,),
    ).fetchall()


def update_report(conn: sqlite3.Connection, diagnosis_id: str, report_json: str) -> None:
    conn.execute("UPDATE diagnoses SET report = ? WHERE id = ?", (report_json, diagnosis_id))


# ---------------------------------------------------------------------------
# requirements
# ---------------------------------------------------------------------------

def insert_requirement(
    conn: sqlite3.Connection,
    *,
    requirement_id: str,
    diagnosis_id: str,
    req_index: int,
    priority: str,
    text: str,
    keywords_json: str,
    status: str,
    quote: str | None,
    fact_id: str | None,
    nearest_note: str | None,
    user_revived: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO requirements(id, diagnosis_id, req_index, priority, text, keywords, "
        "status, quote, fact_id, nearest_note, user_revived) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            requirement_id, diagnosis_id, req_index, priority, text, keywords_json,
            status, quote, fact_id, nearest_note, int(user_revived),
        ),
    )


def fetch_requirements(conn: sqlite3.Connection, diagnosis_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM requirements WHERE diagnosis_id = ? ORDER BY req_index",
        (diagnosis_id,),
    ).fetchall()


def fetch_requirement(conn: sqlite3.Connection, requirement_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM requirements WHERE id = ?", (requirement_id,)).fetchone()


def revive_requirement_row(
    conn: sqlite3.Connection, requirement_id: str, *, quote: str, fact_id: str
) -> None:
    """人工捞回：status → direct + user_revived=1（ard/0004 低成本确认点）。"""
    conn.execute(
        "UPDATE requirements SET status = 'direct', quote = ?, fact_id = ?, "
        "nearest_note = NULL, user_revived = 1 WHERE id = ?",
        (quote, fact_id, requirement_id),
    )

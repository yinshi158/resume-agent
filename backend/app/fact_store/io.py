"""fact_store 的 SQLite 基础读写（连接由调用方管理）。"""

from __future__ import annotations

import sqlite3
from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# resumes / canonical_texts
# ---------------------------------------------------------------------------

def insert_resume(
    conn: sqlite3.Connection,
    *,
    resume_id: str,
    filename: str,
    stored_path: str,
    parser: str,
    normalize_version: int,
    status: str,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO resumes(id, filename, stored_path, parser, normalize_version, status, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (resume_id, filename, stored_path, parser, normalize_version, status, created_at or now_iso()),
    )


def insert_canonical(conn: sqlite3.Connection, resume_id: str, content: str) -> None:
    conn.execute(
        "INSERT INTO canonical_texts(resume_id, content) VALUES(?, ?)",
        (resume_id, content),
    )


def fetch_resume(conn: sqlite3.Connection, resume_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()


def fetch_canonical(conn: sqlite3.Connection, resume_id: str) -> str | None:
    row = conn.execute(
        "SELECT content FROM canonical_texts WHERE resume_id = ?", (resume_id,)
    ).fetchone()
    return row["content"] if row else None


def fetch_resumes_by_filename(conn: sqlite3.Connection, filename: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM resumes WHERE filename = ? ORDER BY created_at DESC", (filename,)
    ).fetchall()


def fetch_resumes(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    """简历列表（新→旧）；可按状态过滤（M2 诊断入口选已 confirmed 简历）。"""
    if status:
        return conn.execute(
            "SELECT * FROM resumes WHERE status = ? ORDER BY created_at DESC, rowid DESC",
            (status,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM resumes ORDER BY created_at DESC, rowid DESC"
    ).fetchall()


def set_resume_status(conn: sqlite3.Connection, resume_id: str, status: str) -> None:
    conn.execute("UPDATE resumes SET status = ? WHERE id = ?", (status, resume_id))


# ---------------------------------------------------------------------------
# facts
# ---------------------------------------------------------------------------

def insert_fact(
    conn: sqlite3.Connection,
    *,
    fact_id: str,
    resume_id: str,
    section: str,
    raw_quote: str,
    span_start: int,
    span_end: int,
    payload_json: str,
    attribution: str,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO facts(id, resume_id, section, raw_quote, span_start, span_end, payload, "
        "attribution, attribution_confirmed, confirmed, edited_payload, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, 0, NULL, ?)",
        (fact_id, resume_id, section, raw_quote, span_start, span_end, payload_json,
         attribution, created_at or now_iso()),
    )


def fetch_facts(conn: sqlite3.Connection, resume_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM facts WHERE resume_id = ? ORDER BY span_start, created_at", (resume_id,)
    ).fetchall()


def fetch_fact(conn: sqlite3.Connection, fact_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()


def count_unconfirmed_facts(conn: sqlite3.Connection, resume_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM facts WHERE resume_id = ? AND confirmed = 0", (resume_id,)
    ).fetchone()
    return int(row["n"])


# ---------------------------------------------------------------------------
# anomaly_flags
# ---------------------------------------------------------------------------

def insert_flag(
    conn: sqlite3.Connection,
    *,
    flag_id: str,
    fact_id: str,
    rule: str,
    detail_json: str,
) -> None:
    conn.execute(
        "INSERT INTO anomaly_flags(id, fact_id, rule, detail, resolved) VALUES(?, ?, ?, ?, 0)",
        (flag_id, fact_id, rule, detail_json),
    )


def fetch_flags_for_facts(conn: sqlite3.Connection, fact_ids: list[str]) -> dict[str, list[sqlite3.Row]]:
    if not fact_ids:
        return {}
    placeholders = ",".join("?" for _ in fact_ids)
    rows = conn.execute(
        f"SELECT * FROM anomaly_flags WHERE fact_id IN ({placeholders}) ORDER BY rowid",
        fact_ids,
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {fid: [] for fid in fact_ids}
    for row in rows:
        grouped[row["fact_id"]].append(row)
    return grouped


def fetch_flag(conn: sqlite3.Connection, flag_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM anomaly_flags WHERE id = ?", (flag_id,)).fetchone()

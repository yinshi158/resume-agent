"""ledger 的 SQLite 读写：applications / outcomes（spec §19，只记录不分析）。

versions 表由 export 写入（export/io.py）；本模块负责投递记录与结果的
追加式读写。
"""

from __future__ import annotations

import sqlite3

from ..fact_store.io import now_iso


def insert_application(
    conn: sqlite3.Connection,
    *,
    application_id: str,
    version_id: str,
    company: str,
    position: str,
    channel: str | None,
    applied_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO applications(id, version_id, company, position, channel, applied_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (application_id, version_id, company, position, channel, applied_at or now_iso()),
    )


def insert_outcome(
    conn: sqlite3.Connection,
    *,
    outcome_id: str,
    application_id: str,
    stage: str,
    note: str | None,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO outcomes(id, application_id, stage, note, created_at) VALUES(?, ?, ?, ?, ?)",
        (outcome_id, application_id, stage, note, created_at or now_iso()),
    )


def fetch_applications(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM applications ORDER BY applied_at DESC, rowid DESC"
    ).fetchall()


def fetch_application(conn: sqlite3.Connection, application_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM applications WHERE id = ?", (application_id,)
    ).fetchone()


def fetch_outcomes(conn: sqlite3.Connection, application_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM outcomes WHERE application_id = ? ORDER BY created_at, rowid",
        (application_id,),
    ).fetchall()

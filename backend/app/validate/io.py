"""validate 的 SQLite 读写：sentence_validations 表（连接由调用方管理）。

判定按句按轮持久化（ard/0006）：gate_events 快照取自最新一轮，
不重新计算（确定性，与 §12.6 报告重算同款纪律）。
"""

from __future__ import annotations

import sqlite3

from ..fact_store.io import now_iso


def insert_validation(
    conn: sqlite3.Connection,
    *,
    validation_id: str,
    sentence_id: str,
    round: int,
    layer: str,
    verdict: str,
    detail_json: str,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO sentence_validations(id, sentence_id, round, layer, verdict, detail, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (validation_id, sentence_id, round, layer, verdict, detail_json, created_at or now_iso()),
    )


def fetch_validations(
    conn: sqlite3.Connection, sentence_id: str
) -> list[sqlite3.Row]:
    """该句全部轮次的判定（按轮次、写入顺序）。"""
    return conn.execute(
        "SELECT * FROM sentence_validations WHERE sentence_id = ? ORDER BY round, rowid",
        (sentence_id,),
    ).fetchall()


def fetch_latest_round(
    conn: sqlite3.Connection, sentence_id: str
) -> tuple[int | None, list[sqlite3.Row]]:
    """最新一轮的判定行（无判定时返回 (None, [])）。"""
    row = conn.execute(
        "SELECT MAX(round) AS r FROM sentence_validations WHERE sentence_id = ?",
        (sentence_id,),
    ).fetchone()
    if row is None or row["r"] is None:
        return None, []
    latest = int(row["r"])
    rows = conn.execute(
        "SELECT * FROM sentence_validations WHERE sentence_id = ? AND round = ? ORDER BY rowid",
        (sentence_id, latest),
    ).fetchall()
    return latest, rows

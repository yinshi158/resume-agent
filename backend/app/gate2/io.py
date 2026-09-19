"""gate2 的 SQLite 读写：gate_events 表（连接由调用方管理，spec §17.1）。"""

from __future__ import annotations

import sqlite3

from ..fact_store.io import now_iso


def insert_gate_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    sentence_id: str,
    action: str,
    before_text: str | None,
    after_text: str | None,
    validation_snapshot_json: str,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO gate_events(id, sentence_id, action, before_text, after_text, "
        "validation_snapshot, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (
            event_id,
            sentence_id,
            action,
            before_text,
            after_text,
            validation_snapshot_json,
            created_at or now_iso(),
        ),
    )


def fetch_gate_events(conn: sqlite3.Connection, sentence_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM gate_events WHERE sentence_id = ? ORDER BY created_at, rowid",
        (sentence_id,),
    ).fetchall()

"""gate1 写回的 SQL 操作（连接由 core 管理）。"""

from __future__ import annotations

import sqlite3


def update_fact_row(
    conn: sqlite3.Connection,
    fact_id: str,
    *,
    edited_payload_json: str | None,
    attribution: str,
    attribution_confirmed: int,
    confirmed: int,
) -> None:
    conn.execute(
        "UPDATE facts SET edited_payload = ?, attribution = ?, "
        "attribution_confirmed = ?, confirmed = ? WHERE id = ?",
        (edited_payload_json, attribution, attribution_confirmed, confirmed, fact_id),
    )


def set_flag_resolved(conn: sqlite3.Connection, flag_id: str, resolved: int) -> None:
    conn.execute("UPDATE anomaly_flags SET resolved = ? WHERE id = ?", (resolved, flag_id))


def count_unconfirmed(conn: sqlite3.Connection, resume_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM facts WHERE resume_id = ? AND confirmed = 0", (resume_id,)
    ).fetchone()
    return int(row["n"])

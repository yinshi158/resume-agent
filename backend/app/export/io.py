"""export 的 SQLite 读写：versions 表（一次成功导出 = 一行，spec §19）。

``path`` 存相对数据目录的路径（data/exports/xxx.pdf），跨机器可迁移。
"""

from __future__ import annotations

import sqlite3

from ..fact_store.io import now_iso


def insert_version(
    conn: sqlite3.Connection,
    *,
    version_id: str,
    rewrite_id: str,
    path: str,
    template: str = "ats-v1",
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO versions(id, rewrite_id, path, template, created_at) VALUES(?, ?, ?, ?, ?)",
        (version_id, rewrite_id, path, template, created_at or now_iso()),
    )


def fetch_version(conn: sqlite3.Connection, version_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM versions WHERE id = ?", (version_id,)).fetchone()

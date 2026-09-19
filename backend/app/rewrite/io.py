"""rewrite 的 SQLite 读写（连接由调用方管理）+ 校验层事实源视图组装。

- rewrites / rewrite_sentences 表读写（spec §15.5）；
- ``fetch_validation_facts``：facts 行 → ``validate.schemas.ValidationFact``
  （校验层只消费已 confirmed 条目，ard/0002；payload 取值规则
  ``edited_payload ?? payload``，C8）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..fact_store.io import now_iso
from ..validate.schemas import ValidationFact


# ---------------------------------------------------------------------------
# facts → ValidationFact（校验层视图）
# ---------------------------------------------------------------------------

def _flatten(value: Any) -> list[str]:
    """结构化字段文本化（与 diagnose.io._flatten 同构；依赖单向，不跨模块取私有）。"""
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
        parts: list[str] = []
        for item in value.values():
            parts.extend(_flatten(item))
        return parts
    return [str(value)]


def _numbers_of(payload: dict) -> list[tuple[float, str]]:
    numbers: list[tuple[float, str]] = []
    for item in (payload.get("entities") or {}).get("numbers") or []:
        if not isinstance(item, dict):
            continue
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        numbers.append((value, str(item.get("unit") or "")))
    return numbers


def _row_to_validation_fact(row: sqlite3.Row) -> ValidationFact:
    payload = json.loads(row["edited_payload"] or row["payload"])  # C8：人工修正优先
    parts: list[str] = [row["raw_quote"]]
    for value in (payload.get("fields") or {}).values():
        parts.extend(_flatten(value))
    entities = payload.get("entities") or {}
    parts.extend(_flatten(entities.get("skills")))
    parts.extend(_flatten(entities.get("orgs")))
    return ValidationFact(
        fact_id=row["id"],
        section=row["section"],
        raw_quote=row["raw_quote"],
        searchable_text=" ".join(part for part in parts if part),
        numbers=_numbers_of(payload),
        attribution=row["attribution"],
        attribution_confirmed=bool(row["attribution_confirmed"]),
        confirmed=bool(row["confirmed"]),
    )


def fetch_validation_facts(
    conn: sqlite3.Connection, resume_id: str
) -> list[ValidationFact]:
    """该简历全部已确认条目（校验层视图；改写只消费 confirmed 数据）。"""
    rows = conn.execute(
        "SELECT * FROM facts WHERE resume_id = ? AND confirmed = 1 ORDER BY span_start, created_at",
        (resume_id,),
    ).fetchall()
    return [_row_to_validation_fact(row) for row in rows]


def fetch_fact_payloads(conn: sqlite3.Connection, resume_id: str) -> dict[str, dict]:
    """id → 有效 payload（edited_payload ?? payload），供 prompt 附字段摘要。"""
    rows = conn.execute(
        "SELECT id, payload, edited_payload FROM facts WHERE resume_id = ? AND confirmed = 1",
        (resume_id,),
    ).fetchall()
    return {
        row["id"]: json.loads(row["edited_payload"] or row["payload"]) for row in rows
    }


# ---------------------------------------------------------------------------
# rewrites / rewrite_sentences
# ---------------------------------------------------------------------------

def insert_rewrite(
    conn: sqlite3.Connection,
    *,
    rewrite_id: str,
    diagnosis_id: str,
    status: str,
    rounds: int,
    target_req_ids_json: str,
    escalations_json: str,
    mock: bool,
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO rewrites(id, diagnosis_id, status, rounds, target_req_ids, escalations, mock, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (
            rewrite_id,
            diagnosis_id,
            status,
            rounds,
            target_req_ids_json,
            escalations_json,
            int(mock),
            created_at or now_iso(),
        ),
    )


def insert_sentence(
    conn: sqlite3.Connection,
    *,
    sentence_id: str,
    rewrite_id: str,
    section: str,
    seq: int,
    original_text: str,
    text: str,
    source_fact_ids_json: str,
    derived_json: str,
    verbs_json: str,
    requirement_ids_json: str,
    gate_status: str = "pending",
    created_at: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO rewrite_sentences(id, rewrite_id, section, seq, original_text, text, "
        "source_fact_ids, derived, verbs, requirement_ids, gate_status, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            sentence_id,
            rewrite_id,
            section,
            seq,
            original_text,
            text,
            source_fact_ids_json,
            derived_json,
            verbs_json,
            requirement_ids_json,
            gate_status,
            created_at or now_iso(),
        ),
    )


def fetch_rewrite(conn: sqlite3.Connection, rewrite_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM rewrites WHERE id = ?", (rewrite_id,)).fetchone()


def fetch_rewrites(conn: sqlite3.Connection, diagnosis_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM rewrites WHERE diagnosis_id = ? ORDER BY created_at DESC, rowid DESC",
        (diagnosis_id,),
    ).fetchall()


def fetch_sentences(conn: sqlite3.Connection, rewrite_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM rewrite_sentences WHERE rewrite_id = ? ORDER BY seq, rowid",
        (rewrite_id,),
    ).fetchall()


def fetch_sentence(conn: sqlite3.Connection, sentence_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM rewrite_sentences WHERE id = ?", (sentence_id,)
    ).fetchone()


def update_sentence_gate(
    conn: sqlite3.Connection,
    sentence_id: str,
    *,
    text: str,
    gate_status: str,
) -> None:
    conn.execute(
        "UPDATE rewrite_sentences SET text = ?, gate_status = ? WHERE id = ?",
        (text, gate_status, sentence_id),
    )


def update_rewrite_status(conn: sqlite3.Connection, rewrite_id: str, status: str) -> None:
    conn.execute("UPDATE rewrites SET status = ? WHERE id = ?", (status, rewrite_id))


def count_pending_sentences(conn: sqlite3.Connection, rewrite_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM rewrite_sentences WHERE rewrite_id = ? AND gate_status = 'pending'",
        (rewrite_id,),
    ).fetchone()
    return int(row["n"])

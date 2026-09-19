"""事实源存储与回验（spec §4）。

职责：把 ingest 的抽取结果落库——

1. **程序回验**：quote 经 normalize 后必须为 canonical_text 子串，
   回验得 span；定位不回的字段强制标 anomaly（missing_field 类），
   span 记为 ``(-1, -1)`` 交人工核对（宁可交人，不猜测）；
2. 组装 payload（fields + date_interpretations + entities）；
3. 计算 anomaly 规则并连同 facts 一次事务入库。

注意：本模块不 import ingest（依赖单向，ard/0008），抽取结果以
dict 传入（结构见 docs/spec.md §5，调用方已完成 pydantic 校验）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import Any, Iterable

from ..anomaly import core as anomaly_core
from ..anomaly.schemas import FactForRules
from ..platform import db, offsets
from ..shared.normalize import find_quote_matches
from . import io
from .schemas import AnomalyFlagOut, FactOut


def store_extraction(
    conn: sqlite3.Connection,
    resume_id: str,
    canonical: str,
    extracted_facts: Iterable[dict[str, Any]],
) -> list[str]:
    """回验 + 异象计算 + 入库，返回 fact_id 列表。

    连接/事务由调用方（ingest 流水线编排）管理，保证 facts 与 resume 行
    同事务提交——失败不留半成品行。
    """
    fact_rows: list[dict[str, Any]] = []
    rules_input: list[FactForRules] = []

    for item in extracted_facts:
        fact_id = uuid.uuid4().hex
        section = str(item.get("section") or "other")
        quote = str(item.get("quote") or "")

        # 程序回验：定位 quote。
        # 0 处匹配 → 回验失败；多处匹配 → 不静默取第一个（锚错实例会让
        # 高亮/证据指错位置）。两种情形都 span (-1,-1) 交人工，不猜测。
        matches = find_quote_matches(canonical, quote)
        match_count = len(matches)
        quote_verified = match_count > 0
        span_start, span_end = matches[0] if match_count == 1 else (-1, -1)

        payload = {
            "fields": item.get("fields") or {},
            "date_interpretations": item.get("date_interpretations") or [],
            "entities": item.get("entities") or {},
        }
        attribution = str(item.get("attribution") or "unknown")
        if attribution not in ("individual", "team", "mixed", "unknown"):
            attribution = "unknown"  # 保守默认（ard/0002）

        fact_rows.append(
            {
                "fact_id": fact_id,
                "resume_id": resume_id,
                "section": section,
                "raw_quote": quote,
                "span_start": span_start,
                "span_end": span_end,
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "attribution": attribution,
            }
        )
        rules_input.append(
            FactForRules(
                fact_id=fact_id,
                section=section,
                raw_quote=quote,
                payload=payload,
                quote_verified=quote_verified,
                quote_match_count=match_count,
            )
        )

    flags = anomaly_core.compute_flags(rules_input)

    for row in fact_rows:
        io.insert_fact(conn, **row)
    for flag in flags:
        io.insert_flag(
            conn,
            flag_id=uuid.uuid4().hex,
            fact_id=flag.fact_id,
            rule=flag.rule,
            detail_json=json.dumps(flag.detail, ensure_ascii=False),
        )
    return [row["fact_id"] for row in fact_rows]


# ---------------------------------------------------------------------------
# 读取（API 输出）
# ---------------------------------------------------------------------------

def _row_to_fact(row, flags: list, canonical: str) -> FactOut:
    return FactOut(
        id=row["id"],
        resume_id=row["resume_id"],
        section=row["section"],
        raw_quote=row["raw_quote"],
        # DB 存码点偏移，API 输出 UTF-16 偏移（前端直接 slice，spec §8）
        span_start=offsets.cp_to_utf16(canonical, row["span_start"]),
        span_end=offsets.cp_to_utf16(canonical, row["span_end"]),
        payload=json.loads(row["payload"]),
        attribution=row["attribution"],
        attribution_confirmed=bool(row["attribution_confirmed"]),
        confirmed=bool(row["confirmed"]),
        edited_payload=json.loads(row["edited_payload"]) if row["edited_payload"] else None,
        created_at=row["created_at"],
        anomaly_flags=[
            AnomalyFlagOut(
                id=flag["id"],
                rule=flag["rule"],
                detail=json.loads(flag["detail"]),
                resolved=bool(flag["resolved"]),
            )
            for flag in flags
        ],
    )


def load_facts(resume_id: str) -> list[FactOut]:
    """读取一份 resume 的全部事实条目（含异象标记）。"""
    conn = db.connect()
    try:
        resume = io.fetch_resume(conn, resume_id)
        if resume is None:
            raise KeyError(resume_id)
        canonical = io.fetch_canonical(conn, resume_id) or ""
        rows = io.fetch_facts(conn, resume_id)
        flags_map = io.fetch_flags_for_facts(conn, [row["id"] for row in rows])
        return [_row_to_fact(row, flags_map.get(row["id"], []), canonical) for row in rows]
    finally:
        conn.close()


def load_fact(fact_id: str) -> FactOut:
    """读取单条事实（含其 resume 的 canonical 用于偏移换算）。"""
    conn = db.connect()
    try:
        row = io.fetch_fact(conn, fact_id)
        if row is None:
            raise KeyError(fact_id)
        canonical = io.fetch_canonical(conn, row["resume_id"]) or ""
        flags_map = io.fetch_flags_for_facts(conn, [fact_id])
        return _row_to_fact(row, flags_map.get(fact_id, []), canonical)
    finally:
        conn.close()

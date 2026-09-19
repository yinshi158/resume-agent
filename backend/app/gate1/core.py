"""gate1 写回核心（spec §8 校对动作）。

写回规则（保守一侧，ard/0002）：

- ``edited_payload``：人工修正后的结构化字段——**不改原文层**（ard/0001）；
- ``attribution`` 变更时若未显式给出 ``attribution_confirmed``，
  确认态重置（individual 视为无需强制确认，自动确认）；
- ``confirmed=True`` 要求归因已确认（individual 或 attribution_confirmed），
  否则拒绝——attribution 未表态的条目不能完成校对；
- 一份 resume 的全部条目 confirmed 后，resume 状态 → ``confirmed``；
  若之后取消确认则回退 ``reviewing``。
"""

from __future__ import annotations

import json

from ..fact_store import core as fact_store_core
from ..fact_store import io as fact_store_io
from ..platform import db
from . import io
from .schemas import FactPatch


def patch_fact(fact_id: str, patch: FactPatch) -> fact_store_core.FactOut:
    conn = db.connect()
    try:
        row = fact_store_io.fetch_fact(conn, fact_id)
        if row is None:
            raise KeyError(fact_id)

        edited_payload_json = row["edited_payload"]
        attribution = row["attribution"]
        attribution_confirmed = int(row["attribution_confirmed"])
        confirmed = int(row["confirmed"])

        if patch.edited_payload is not None:
            edited_payload_json = json.dumps(patch.edited_payload, ensure_ascii=False)

        if patch.attribution is not None:
            attribution = patch.attribution
            if patch.attribution_confirmed is not None:
                attribution_confirmed = int(patch.attribution_confirmed)
            else:
                # attribution 变更 → 默认重置确认态；individual 无需强制确认
                attribution_confirmed = int(attribution == "individual")
        elif patch.attribution_confirmed is not None:
            attribution_confirmed = int(patch.attribution_confirmed)

        if patch.confirmed is not None:
            if patch.confirmed and not (attribution == "individual" or attribution_confirmed):
                raise PermissionError(
                    "归因未确认：请先确认该条目的团队/个人归因，再完成校对（ard/0002）"
                )
            confirmed = int(patch.confirmed)

        with conn:
            io.update_fact_row(
                conn,
                fact_id,
                edited_payload_json=edited_payload_json,
                attribution=attribution,
                attribution_confirmed=attribution_confirmed,
                confirmed=confirmed,
            )
            _sync_resume_status(conn, row["resume_id"])
    finally:
        conn.close()
    return fact_store_core.load_fact(fact_id)


def resolve_flag(flag_id: str) -> None:
    """标记异象已处理（resolved=1）。"""
    conn = db.connect()
    try:
        flag = fact_store_io.fetch_flag(conn, flag_id)
        if flag is None:
            raise KeyError(flag_id)
        with conn:
            io.set_flag_resolved(conn, flag_id, 1)
    finally:
        conn.close()


def _sync_resume_status(conn, resume_id: str) -> None:
    """全部条目 confirmed → resume=confirmed；否则若已 confirmed 则回退。"""
    pending = io.count_unconfirmed(conn, resume_id)
    row = conn.execute("SELECT status FROM resumes WHERE id = ?", (resume_id,)).fetchone()
    if row is None:
        return
    if pending == 0 and row["status"] != "confirmed":
        conn.execute("UPDATE resumes SET status = 'confirmed' WHERE id = ?", (resume_id,))
    elif pending > 0 and row["status"] == "confirmed":
        conn.execute("UPDATE resumes SET status = 'reviewing' WHERE id = ?", (resume_id,))

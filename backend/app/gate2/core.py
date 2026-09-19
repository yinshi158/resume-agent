"""gate2 动作与编辑重校验（spec §17，ard/0002 第二咽喉 + ard/0006 同步项）。

- 动作 × 当次判定快照配对落库（误报/漏报分析原料：用户驳回被标红句 =
  误报信号；用户手改未标红句 = 漏报信号）；快照取自 sentence_validations
  最新一轮，不重新计算（确定性，与 §12.6 报告重算同款纪律）；
- edit 动作落库后立即对该句重跑确定性三层（L0/verb/L1）；L2 重跑
  触发条件（R2 已决，满足任一）：① 句中数字 token 集合发生变化；
  ② 编辑后文本与原句 difflib 相似度 < 0.8；均不触发则沿用原 L2 判定
  入快照（数字是幻觉高发区，轻量措辞修改不改变蕴含关系）；
- 重校验结果写新一轮 sentence_validations。
"""

from __future__ import annotations

import difflib
import json
import sqlite3
import uuid

from ..diagnose import io as diagnose_io
from ..platform import db
from ..rewrite import io as rewrite_io
from ..validate import core as validate_core
from ..validate import io as validate_io
from ..validate.l0 import build_entity_vocab, extract_numbers
from ..validate.schemas import LayerVerdict, RewrittenSentence
from . import io

#: edit 触发 L2 重跑的最小相似度阈值（R2：< 0.8 才重跑）
L2_RERUN_SIMILARITY = 0.8

ACTIONS = ("confirm", "reject", "edit")


def _row_to_snapshot(validation_rows: list[sqlite3.Row]) -> list[dict]:
    snapshot: list[dict] = []
    for row in validation_rows:
        try:
            detail = json.loads(row["detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        snapshot.append(
            {"layer": row["layer"], "verdict": row["verdict"], "detail": detail}
        )
    return snapshot


def _number_token_set(text: str) -> set[tuple[float, str]]:
    return {(token.value, token.unit.casefold()) for token in extract_numbers(text)}


def should_rerun_l2(before_text: str, after_text: str) -> bool:
    """R2 触发条件：数字 token 集合变化，或相似度 < 0.8。"""
    if _number_token_set(before_text) != _number_token_set(after_text):
        return True
    ratio = difflib.SequenceMatcher(None, before_text, after_text).ratio()
    return ratio < L2_RERUN_SIMILARITY


def apply_gate_action(sentence_id: str, action: str, edit_text: str | None = None) -> dict:
    """执行 gate2 动作：落事件（含判定快照）→ 更新句状态 → edit 时重校验。"""
    if action not in ACTIONS:
        raise ValueError(f"未知动作：{action}")

    conn = db.connect()
    try:
        sentence = rewrite_io.fetch_sentence(conn, sentence_id)
        if sentence is None:
            raise KeyError(sentence_id)
        rewrite = rewrite_io.fetch_rewrite(conn, sentence["rewrite_id"])
        if rewrite is None:
            raise KeyError(sentence["rewrite_id"])
        diagnosis = diagnose_io.fetch_diagnosis(conn, rewrite["diagnosis_id"])
        if diagnosis is None:
            raise KeyError(rewrite["diagnosis_id"])

        latest_round, latest_rows = validate_io.fetch_latest_round(conn, sentence_id)
        snapshot = _row_to_snapshot(latest_rows)

        before_text: str | None = None
        after_text: str | None = None
        new_status = {"confirm": "confirmed", "reject": "rejected", "edit": "edited"}[action]
        new_text = sentence["text"]

        if action == "edit":
            edited = (edit_text or "").strip()
            if not edited:
                raise ValueError("编辑后的文本不能为空")
            before_text = sentence["text"]
            after_text = edited
            new_text = edited

        with conn:
            io.insert_gate_event(
                conn,
                event_id=uuid.uuid4().hex,
                sentence_id=sentence_id,
                action=action,
                before_text=before_text,
                after_text=after_text,
                validation_snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            )
            rewrite_io.update_sentence_gate(
                conn, sentence_id, text=new_text, gate_status=new_status
            )

            l2_rerun = False
            if action == "edit":
                l2_rerun = _revalidate_after_edit(
                    conn,
                    sentence_id=sentence_id,
                    sentence_row=sentence,
                    edited_text=edited,
                    diagnosis=diagnosis,
                    latest_round=latest_round,
                    latest_rows=latest_rows,
                )
    finally:
        conn.close()

    return {
        "sentence_id": sentence_id,
        "gate_status": new_status,
        "text": new_text,
        "l2_rerun": bool(action == "edit" and l2_rerun),
    }


def _revalidate_after_edit(
    conn: sqlite3.Connection,
    *,
    sentence_id: str,
    sentence_row: sqlite3.Row,
    edited_text: str,
    diagnosis: sqlite3.Row,
    latest_round: int | None,
    latest_rows: list[sqlite3.Row],
) -> bool:
    """编辑后重校验：确定性三层必跑；L2 按 R2 条件决定重跑或沿用。"""
    facts = rewrite_io.fetch_validation_facts(conn, diagnosis["resume_id"])
    facts_by_id = {fact.fact_id: fact for fact in facts}
    requirement_rows = diagnose_io.fetch_requirements(conn, diagnosis["id"])
    entity_vocab = build_entity_vocab(
        [json.loads(row["keywords"] or "[]") for row in requirement_rows]
    )

    edited_sentence = RewrittenSentence(
        section=sentence_row["section"],
        text=edited_text,
        source_fact_ids=json.loads(sentence_row["source_fact_ids"]),
        derived_numbers=json.loads(sentence_row["derived"] or "[]"),
        verbs=json.loads(sentence_row["verbs"] or "[]"),
        requirement_ids=json.loads(sentence_row["requirement_ids"] or "[]"),
    )

    verdicts: list[LayerVerdict] = validate_core.run_deterministic_layers(
        edited_sentence,
        all_facts=facts,
        facts_by_id=facts_by_id,
        entity_vocab=entity_vocab,
    )

    rerun = should_rerun_l2(sentence_row["text"], edited_text)
    if rerun:
        verdicts.append(validate_core.run_l2(edited_sentence, facts_by_id=facts_by_id))
    else:
        previous = next(
            (row for row in latest_rows if row["layer"] == "L2"), None
        )
        if previous is None:
            # 没有历史 L2 判定可沿用 → 保守起见重跑一次
            rerun = True
            verdicts.append(validate_core.run_l2(edited_sentence, facts_by_id=facts_by_id))
        else:
            try:
                detail = json.loads(previous["detail"] or "{}")
            except json.JSONDecodeError:
                detail = {}
            detail = {
                **detail,
                "reused": True,
                "message": (
                    f"未触发 L2 重跑（数字集合未变且相似度 ≥ {L2_RERUN_SIMILARITY}），"
                    f"沿用上一轮判定：{detail.get('message', '')}"
                ),
            }
            verdicts.append(
                LayerVerdict(layer="L2", verdict=previous["verdict"], detail=detail)
            )

    new_round = (latest_round + 1) if latest_round is not None else 0
    for verdict in verdicts:
        validate_io.insert_validation(
            conn,
            validation_id=uuid.uuid4().hex,
            sentence_id=sentence_id,
            round=new_round,
            layer=verdict.layer,
            verdict=verdict.verdict,
            detail_json=json.dumps(verdict.detail, ensure_ascii=False),
        )
    return rerun


# ---------------------------------------------------------------------------
# 导出咽喉（§17.2：全部句子处置完成才允许导出）
# ---------------------------------------------------------------------------

def assert_exportable(rewrite_id: str) -> None:
    """导出前置：无 pending 句子且句集非空。

    :raises KeyError: 改写不存在（API → 404）
    :raises PermissionError: 仍有 pending 句 / 无句子（API → 422）
    """
    conn = db.connect()
    try:
        rewrite = rewrite_io.fetch_rewrite(conn, rewrite_id)
        if rewrite is None:
            raise KeyError(rewrite_id)
        total = len(rewrite_io.fetch_sentences(conn, rewrite_id))
        pending = rewrite_io.count_pending_sentences(conn, rewrite_id)
    finally:
        conn.close()
    if total == 0:
        raise PermissionError("该改写没有可导出的句子（全部要求均为硬性缺口）")
    if pending:
        raise PermissionError(f"还有 {pending} 句未处置（确认/拒绝/编辑）——全部处置完成才可导出")

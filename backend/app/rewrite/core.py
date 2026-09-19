"""改写流水线编排（spec §15）与读取。

```
POST /api/rewrites {diagnosis_id}
→ 复核 diagnosis 存在且其 resume 仍为 confirmed（改写入口咽喉，ard/0002）
→ 计算目标清单 + 升级清单（§15.3 分母修正：纯 gap 不进分母）
→ writing round 0：LLM 改写，逐句 pydantic 校验，非法句跳过记 warning
→ validating round n：四层校验（validate.run_all_layers），判定按句按轮持久化
→ 可修复失败 → writing round n+1（≤2 轮，判定 detail 进 prompt）
→ 最终轮 L2 不蕴含（非调用故障）→ 升级回诊断报告（不重试）
→ done(rewrite_id) / error
```

存储纪律：全部轮次完成才入库（一次事务），失败/断流不留半成品行
（与 ingest/diagnose 同款）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from typing import Iterator

from pydantic import ValidationError

from ..diagnose import io as diagnose_io
from ..fact_store import io as fact_store_io
from ..platform import db, llm, offsets
from ..validate import core as validate_core
from ..validate import io as validate_io
from ..validate.l0 import build_entity_vocab
from ..validate.schemas import LayerVerdict, RewrittenSentence, ValidationFact
from . import io, mock_rules, prompts
from .schemas import RewriteEscalation, RewriteTarget

#: 可修复失败的重写轮上限（spec §15.3：≤2 轮）
MAX_RETRY_ROUNDS = 2


# ---------------------------------------------------------------------------
# 前置校验（API 层同步快速失败：404 / 422）
# ---------------------------------------------------------------------------

def assert_ready(diagnosis_id: str) -> None:
    """复核诊断存在且对应简历仍为 confirmed（改写入口咽喉）。

    :raises KeyError: 诊断/简历不存在（API → 404）
    :raises PermissionError: 事实源已退回校对状态（API → 422）
    """
    conn = db.connect()
    try:
        diagnosis = diagnose_io.fetch_diagnosis(conn, diagnosis_id)
        if diagnosis is None:
            raise KeyError(diagnosis_id)
        resume = fact_store_io.fetch_resume(conn, diagnosis["resume_id"])
        if resume is None:
            raise KeyError(diagnosis["resume_id"])
        if resume["status"] != "confirmed":
            raise PermissionError(
                "该简历的事实源已退回校对状态——改写与导出均以 confirmed 事实源为前提（ard/0002）"
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 目标清单与升级清单（§15.3 分母修正，ard/0004）
# ---------------------------------------------------------------------------

def compute_targets(
    requirement_rows: list[sqlite3.Row],
) -> tuple[list[RewriteTarget], list[RewriteEscalation]]:
    """目标清单 = direct / nearest / user_revived；纯 gap 剔除进升级清单。"""
    targets: list[RewriteTarget] = []
    escalations: list[RewriteEscalation] = []
    for row in requirement_rows:
        keywords = json.loads(row["keywords"] or "[]")
        if row["status"] in ("direct", "nearest") or row["user_revived"]:
            targets.append(
                RewriteTarget(
                    requirement_id=row["id"],
                    req_index=row["req_index"],
                    priority=row["priority"],
                    text=row["text"],
                    status=row["status"],
                    quote=row["quote"],
                    fact_id=row["fact_id"],
                    keywords=keywords if isinstance(keywords, list) else [],
                )
            )
        else:
            escalations.append(
                RewriteEscalation(
                    kind="requirement_gap",
                    requirement_id=row["id"],
                    message=(
                        f"该岗位要求「{row['text']}」，经历库中没有对应经历——"
                        f"建议补充经历或接受低覆盖"
                    ),
                    detail={
                        "priority": row["priority"],
                        "text": row["text"],
                        "nearest_note": row["nearest_note"],
                    },
                )
            )
    return targets, escalations


# ---------------------------------------------------------------------------
# 流水线（生成器：逐事件产出，api 层编码为 SSE）
# ---------------------------------------------------------------------------

@dataclass
class _SentenceState:
    """句子的内存态（判定按轮累积，最终一次事务入库）。"""

    sentence_id: str
    sentence: RewrittenSentence
    validations: dict[int, list[LayerVerdict]] = field(default_factory=dict)

    def final_verdicts(self) -> list[LayerVerdict]:
        if not self.validations:
            return []
        return self.validations[max(self.validations)]


def iter_rewrite(diagnosis_id: str) -> Iterator[dict]:
    """改写全链路：writing → validating rounds → escalated → done / error。"""
    try:
        # ---------- 载入（同步阶段） ----------
        conn = db.connect()
        try:
            diagnosis = diagnose_io.fetch_diagnosis(conn, diagnosis_id)
            if diagnosis is None:
                raise KeyError(f"诊断不存在：{diagnosis_id}")
            resume = fact_store_io.fetch_resume(conn, diagnosis["resume_id"])
            if resume is None:
                raise KeyError("简历不存在")
            if resume["status"] != "confirmed":
                raise PermissionError(
                    "该简历的事实源已退回校对状态——改写与导出均以 confirmed 事实源为前提（ard/0002）"
                )
            requirement_rows = diagnose_io.fetch_requirements(conn, diagnosis_id)
            facts = io.fetch_validation_facts(conn, diagnosis["resume_id"])
            payloads = io.fetch_fact_payloads(conn, diagnosis["resume_id"])
        finally:
            conn.close()
        if not facts:
            raise ValueError("该简历没有已确认的事实源条目，无法改写")
        facts_by_id = {fact.fact_id: fact for fact in facts}
        entity_vocab = build_entity_vocab(
            [json.loads(row["keywords"] or "[]") for row in requirement_rows]
        )

        targets, escalations = compute_targets(requirement_rows)
        warnings: list[str] = []
        is_mock = False
        rounds_used = 0
        states: list[_SentenceState] = []
        feedback_items: list[dict] = []

        if not targets:
            yield {
                "type": "step",
                "step": "writing",
                "round": 0,
                "message": "全部要求均为硬性缺口（无改写目标），不生成句子",
            }
        else:
            for round_index in range(MAX_RETRY_ROUNDS + 1):
                is_retry = round_index > 0
                if is_retry:
                    rounds_used = round_index
                    yield {
                        "type": "step",
                        "step": "writing",
                        "round": round_index,
                        "message": (
                            f"重写第 {round_index} 轮（≤{MAX_RETRY_ROUNDS}）："
                            f"修复 {len(feedback_items)} 句"
                        ),
                    }
                else:
                    yield {
                        "type": "step",
                        "step": "writing",
                        "round": 0,
                        "message": f"改写第 1 轮：为 {len(targets)} 条目标要求生成句集",
                    }

                messages = prompts.build_rewrite_messages(
                    targets,
                    facts,
                    feedback_items if is_retry else None,
                    payloads,
                )
                text, call_mock = llm.chat_json(
                    messages, mock_provider=mock_rules.build_rewrite_response
                )
                is_mock = is_mock or call_mock

                if is_retry:
                    revised, revision_warnings = _parse_sentences(text)
                    warnings.extend(revision_warnings)
                    to_validate = _apply_revisions(
                        states, feedback_items, revised, warnings
                    )
                    if not to_validate:
                        break  # 重写轮没有可用修复 → 保留原判定（最终轮即已有）
                else:
                    parsed, parse_warnings = _parse_sentences(text)
                    warnings.extend(parse_warnings)
                    states = [
                        _SentenceState(uuid.uuid4().hex, sentence)
                        for sentence in parsed
                    ]
                    if not states:
                        raise ValueError("改写未产出任何合法句子（全部被程序拒收）")
                    to_validate = states

                yield {
                    "type": "step",
                    "step": "validating",
                    "round": round_index,
                    "message": f"校验第 {round_index + 1} 轮（L0 / 归因 / L1 / L2）",
                }
                for state in to_validate:
                    verdicts = validate_core.run_all_layers(
                        state.sentence,
                        all_facts=facts,
                        facts_by_id=facts_by_id,
                        entity_vocab=entity_vocab,
                    )
                    state.validations[round_index] = verdicts
                    if any(v.detail.get("mock") for v in verdicts):
                        is_mock = True

                retry_states = [
                    state
                    for state in to_validate
                    if validate_core.repairable_failures(state.validations[round_index])
                ]
                feedback_items = [
                    _feedback_item(state, round_index) for state in retry_states
                ]
                if not retry_states or round_index >= MAX_RETRY_ROUNDS:
                    break

        # ---------- 升级清单：诊断缺口 + 最终轮 L2 不蕴含（不重试） ----------
        for state in states:
            for verdict in validate_core.l2_failures(state.final_verdicts()):
                escalations.append(
                    RewriteEscalation(
                        kind="l2_unentailed",
                        sentence_id=state.sentence_id,
                        message=(
                            f"改写句「{state.sentence.text[:40]}」声明出处无法支撑："
                            f"{verdict.detail.get('reason') or verdict.detail.get('message')}"
                            f"——已升级回诊断报告，不自动重试"
                        ),
                        detail={
                            "message": verdict.detail.get("message"),
                            "requirement_ids": state.sentence.requirement_ids,
                        },
                    )
                )

        status = "escalated" if escalations else "done"
        rewrite_id = _store(
            diagnosis_id, targets, states, escalations, is_mock, rounds_used, status
        )

        if escalations:
            yield {
                "type": "step",
                "step": "escalated",
                "items": [asdict(item) for item in escalations],
                "message": (
                    f"{len(escalations)} 项升级（硬性缺口 / 声明出处不蕴含），已附回报告"
                ),
            }
        yield {
            "type": "done",
            "rewrite_id": rewrite_id,
            "mock": is_mock,
            "rounds": rounds_used,
            "warnings": warnings,
            "escalations": len(escalations),
        }
    except Exception as exc:  # noqa: BLE001 —— SSE 流内错误统一转 error 事件
        yield {"type": "error", "message": str(exc)}


def _parse_sentences(text: str) -> tuple[list[RewrittenSentence], list[str]]:
    """LLM 输出 → 句集（逐句 pydantic 校验，非法句跳过记 warning）。"""
    data = llm.parse_json(text)
    raw_items = data.get("sentences")
    if not isinstance(raw_items, list):
        raise ValueError("改写输出的 sentences 字段必须是数组")
    sentences: list[RewrittenSentence] = []
    warnings: list[str] = []
    for index, item in enumerate(raw_items):
        try:
            sentences.append(RewrittenSentence.model_validate(item))
        except ValidationError as exc:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            warnings.append(
                f"第 {index + 1} 句被程序拒收（{location}：{first.get('msg')}）"
            )
    return sentences, warnings


def _apply_revisions(
    states: list[_SentenceState],
    feedback_items: list[dict],
    revised: list[RewrittenSentence],
    warnings: list[str],
) -> list[_SentenceState]:
    """把重写轮产出的修复句按反馈顺序回填到内存态。"""
    by_id = {state.sentence_id: state for state in states}
    to_validate: list[_SentenceState] = []
    for index, item in enumerate(feedback_items):
        if index >= len(revised):
            warnings.append(
                f"重写轮返回句数不足（{len(revised)}/{len(feedback_items)}），"
                f"未覆盖的句子保留原判定"
            )
            break
        state = by_id.get(item["sentence_id"])
        if state is None:
            continue
        state.sentence = revised[index]
        to_validate.append(state)
    if len(revised) > len(feedback_items):
        warnings.append(f"重写轮返回了多余的句子（{len(revised)} 句），已忽略")
    return to_validate


def _feedback_item(state: _SentenceState, round_index: int) -> dict:
    """失败句反馈（repairable 判定必列；L2 不蕴含作为附加提示）。"""
    verdicts = state.validations.get(round_index, [])
    issues = [
        f"{verdict.layer}：{verdict.detail.get('message', '')}"
        for verdict in verdicts
        if not verdict.passed
    ]
    return {
        "sentence_id": state.sentence_id,
        "text": state.sentence.text,
        "section": state.sentence.section,
        "fact_ids": state.sentence.source_fact_ids,
        "requirement_ids": state.sentence.requirement_ids,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# 存储（全部轮次完成 → 一次事务）
# ---------------------------------------------------------------------------

def _store(
    diagnosis_id: str,
    targets: list[RewriteTarget],
    states: list[_SentenceState],
    escalations: list[RewriteEscalation],
    is_mock: bool,
    rounds_used: int,
    status: str,
) -> str:
    rewrite_id = uuid.uuid4().hex
    conn = db.connect()
    try:
        with conn:
            io.insert_rewrite(
                conn,
                rewrite_id=rewrite_id,
                diagnosis_id=diagnosis_id,
                status=status,
                rounds=rounds_used,
                target_req_ids_json=json.dumps(
                    [t.requirement_id for t in targets], ensure_ascii=False
                ),
                escalations_json=json.dumps(
                    [asdict(item) for item in escalations], ensure_ascii=False
                ),
                mock=is_mock,
            )
            for seq, state in enumerate(states):
                sentence = state.sentence
                io.insert_sentence(
                    conn,
                    sentence_id=state.sentence_id,
                    rewrite_id=rewrite_id,
                    section=sentence.section,
                    seq=seq,
                    original_text=sentence.text,  # LLM 定稿文本（diff 基准，不再变）
                    text=sentence.text,
                    source_fact_ids_json=json.dumps(sentence.source_fact_ids, ensure_ascii=False),
                    derived_json=json.dumps(
                        [item.model_dump() for item in sentence.derived_numbers],
                        ensure_ascii=False,
                    ),
                    verbs_json=json.dumps(sentence.verbs, ensure_ascii=False),
                    requirement_ids_json=json.dumps(sentence.requirement_ids, ensure_ascii=False),
                )
                for round_index in sorted(state.validations):
                    for verdict in state.validations[round_index]:
                        validate_io.insert_validation(
                            conn,
                            validation_id=uuid.uuid4().hex,
                            sentence_id=state.sentence_id,
                            round=round_index,
                            layer=verdict.layer,
                            verdict=verdict.verdict,
                            detail_json=json.dumps(verdict.detail, ensure_ascii=False),
                        )
    finally:
        conn.close()
    return rewrite_id


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def _source_fact_payload(
    fact_row: sqlite3.Row | None, canonical: str
) -> dict:
    if fact_row is None:
        return {"id": None, "missing": True}
    return {
        "id": fact_row["id"],
        "section": fact_row["section"],
        "raw_quote": fact_row["raw_quote"],
        "attribution": fact_row["attribution"],
        "confirmed": bool(fact_row["confirmed"]),
        # UTF-16 偏移（前端直接 slice 高亮，spec §8）；-1 = 未定位
        "span_start": offsets.cp_to_utf16(canonical, fact_row["span_start"]),
        "span_end": offsets.cp_to_utf16(canonical, fact_row["span_end"]),
        "locatable": fact_row["span_start"] >= 0,
    }


def load_rewrite(rewrite_id: str) -> dict:
    """改写详情：句集 + 各层判定（按轮）+ 目标/升级清单 + 出处对照。"""
    conn = db.connect()
    try:
        rewrite = io.fetch_rewrite(conn, rewrite_id)
        if rewrite is None:
            raise KeyError(rewrite_id)
        diagnosis = diagnose_io.fetch_diagnosis(conn, rewrite["diagnosis_id"])
        if diagnosis is None:
            raise KeyError(rewrite["diagnosis_id"])
        sentence_rows = io.fetch_sentences(conn, rewrite_id)
        requirement_rows = diagnose_io.fetch_requirements(conn, rewrite["diagnosis_id"])
        canonical = fact_store_io.fetch_canonical(conn, diagnosis["resume_id"]) or ""
        fact_rows = fact_store_io.fetch_facts(conn, diagnosis["resume_id"])
        facts_by_id = {row["id"]: row for row in fact_rows}
        validations_by_sentence = {
            row["id"]: validate_io.fetch_validations(conn, row["id"])
            for row in sentence_rows
        }
    finally:
        conn.close()

    sentences_out: list[dict] = []
    for row in sentence_rows:
        rounds: dict[int, list[dict]] = {}
        for validation in validations_by_sentence.get(row["id"], []):
            try:
                detail = json.loads(validation["detail"] or "{}")
            except json.JSONDecodeError:
                detail = {}
            rounds.setdefault(validation["round"], []).append(
                {
                    "layer": validation["layer"],
                    "verdict": validation["verdict"],
                    "detail": detail,
                }
            )
        source_fact_ids = json.loads(row["source_fact_ids"])
        sentences_out.append(
            {
                "id": row["id"],
                "section": row["section"],
                "seq": row["seq"],
                "original_text": row["original_text"],
                "text": row["text"],
                "source_fact_ids": source_fact_ids,
                "derived": json.loads(row["derived"] or "[]"),
                "verbs": json.loads(row["verbs"] or "[]"),
                "requirement_ids": json.loads(row["requirement_ids"] or "[]"),
                "gate_status": row["gate_status"],
                "validations": [
                    {"round": round_index, "layers": layers}
                    for round_index, layers in sorted(rounds.items())
                ],
                "source_facts": [
                    {
                        **_source_fact_payload(facts_by_id.get(fid), canonical),
                        "id": fid,
                    }
                    for fid in source_fact_ids
                ],
            }
        )

    return {
        "id": rewrite["id"],
        "diagnosis_id": rewrite["diagnosis_id"],
        "resume_id": diagnosis["resume_id"],
        "status": rewrite["status"],
        "rounds": rewrite["rounds"],
        "created_at": rewrite["created_at"],
        "mock": bool(rewrite["mock"]),
        "target_requirement_ids": json.loads(rewrite["target_req_ids"] or "[]"),
        "escalations": json.loads(rewrite["escalations"] or "[]"),
        "requirements": [
            {
                "id": row["id"],
                "req_index": row["req_index"],
                "priority": row["priority"],
                "text": row["text"],
                # 关键词（含中英文别名）：导出的 ATS 关键词并写来源（§18.1）
                "keywords": json.loads(row["keywords"] or "[]"),
                "status": row["status"],
                "quote": row["quote"],
                "user_revived": bool(row["user_revived"]),
            }
            for row in requirement_rows
        ],
        "sentences": sentences_out,
    }


def list_rewrites(diagnosis_id: str) -> list[dict]:
    """某诊断的历史改写列表（新→旧）。"""
    conn = db.connect()
    try:
        if diagnose_io.fetch_diagnosis(conn, diagnosis_id) is None:
            raise KeyError(diagnosis_id)
        rows = io.fetch_rewrites(conn, diagnosis_id)
        counts = {
            count_row["rewrite_id"]: int(count_row["n"])
            for count_row in conn.execute(
                "SELECT rewrite_id, COUNT(*) AS n FROM rewrite_sentences GROUP BY rewrite_id"
            )
        }
    finally:
        conn.close()

    items: list[dict] = []
    for row in rows:
        try:
            escalations = json.loads(row["escalations"] or "[]")
        except json.JSONDecodeError:
            escalations = []
        items.append(
            {
                "id": row["id"],
                "diagnosis_id": row["diagnosis_id"],
                "status": row["status"],
                "rounds": row["rounds"],
                "created_at": row["created_at"],
                "mock": bool(row["mock"]),
                "escalations": len(escalations),
                "sentence_count": counts.get(row["id"], 0),
            }
        )
    return items

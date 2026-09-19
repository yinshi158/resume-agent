"""诊断流水线编排（spec §12.3）与报告读取/人工捞回。

```
POST /api/diagnoses {resume_id, jd_text}
→ 校验 resume 状态 == confirmed（未校对完不允许诊断，咽喉顺序 ard/0002）
→ sanitize(jd_text) → LLM 拆要求清单（逐条校验，非法跳过记 warning）
→ 逐条举证：LLM 候选生成 → 程序验证（evidence.resolve_requirement）
→ 汇总报告（report.build_report）→ diagnoses + requirements 一次事务入库
```

- SSE 进度经 ``iter_diagnosis`` 以事件字典流式上报（api 层负责编码）；
- 全部 LLM 调用与计算完成后才入库：失败/断流不留半成品诊断行
  （与 ingest 失败回滚同款；重试产生新行，与重解析语义一致）；
- 报告读取/重算只依据 requirements 行，不重新调 LLM（spec §12.6，
  revive 后汇总确定性更新）。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from typing import Iterator

from pydantic import ValidationError

from ..fact_store import io as fact_store_io
from ..platform import db, llm, offsets
from ..shared.normalize import find_quote_matches
from . import io, jd, mock_rules, prompts
from .evidence import fallback_candidates, resolve_requirement
from .report import build_report
from .schemas import (
    DiagnosisReport,
    EvidenceCandidate,
    FactView,
    RequirementItem,
    VerifiedRequirement,
)


# ---------------------------------------------------------------------------
# 前置校验（API 层同步快速失败：404 / 422）
# ---------------------------------------------------------------------------

def assert_ready(resume_id: str) -> None:
    """校验简历存在且已完成事实源校对（诊断的咽喉前提，ard/0002）。

    :raises KeyError: 简历不存在（API → 404）
    :raises PermissionError: 未完成校对（API → 422）
    """
    conn = db.connect()
    try:
        resume = fact_store_io.fetch_resume(conn, resume_id)
    finally:
        conn.close()
    if resume is None:
        raise KeyError(resume_id)
    if resume["status"] != "confirmed":
        raise PermissionError(
            "该简历尚未完成事实源校对（gate1）——全部条目确认后才可进行 JD 诊断（ard/0002）"
        )


# ---------------------------------------------------------------------------
# 流水线（生成器：逐事件产出，api 层编码为 SSE）
# ---------------------------------------------------------------------------

def iter_diagnosis(resume_id: str, jd_text: str) -> Iterator[dict]:
    """诊断全链路：parsing → evidencing n/N → reporting → done / error。"""
    try:
        conn = db.connect()
        try:
            resume = fact_store_io.fetch_resume(conn, resume_id)
            if resume is None:
                raise KeyError(f"简历不存在：{resume_id}")
            if resume["status"] != "confirmed":
                raise PermissionError(
                    "该简历尚未完成事实源校对（gate1）——全部条目确认后才可进行 JD 诊断（ard/0002）"
                )
            canonical = fact_store_io.fetch_canonical(conn, resume_id) or ""
            fact_views = io.fetch_confirmed_facts(conn, resume_id)
        finally:
            conn.close()
        if not fact_views:
            raise ValueError("该简历没有已确认的事实源条目，无法进行诊断")

        yield {"type": "step", "step": "parsing", "message": "正在清洗 JD 并拆解要求清单"}
        cleaned, requirements, is_mock, warnings = jd.parse_requirements(jd_text)
        total = len(requirements)

        candidates: list[EvidenceCandidate | None] = []
        for index, req in enumerate(requirements):
            yield {
                "type": "step",
                "step": "evidencing",
                "index": index,
                "total": total,
                "message": f"逐条举证 {index + 1}/{total}：{req.text[:40]}",
            }
            candidate, call_mock = _generate_candidate(cleaned, index, req, fact_views)
            is_mock = is_mock or call_mock
            candidates.append(candidate)

        yield {"type": "step", "step": "reporting", "message": "正在汇总诊断报告"}
        verified = [
            resolve_requirement(canonical, req, index, candidates[index], fact_views)
            for index, req in enumerate(requirements)
        ]
        report = build_report(verified)
        diagnosis_id = _store(cleaned, resume_id, verified, report, is_mock, warnings)
        yield {
            "type": "done",
            "diagnosis_id": diagnosis_id,
            "mock": is_mock,
            "warnings": warnings,
        }
    except Exception as exc:  # noqa: BLE001 —— SSE 流内错误统一转 error 事件
        yield {"type": "error", "message": str(exc)}


def _generate_candidate(
    jd_text: str, req_index: int, req: RequirementItem, facts: list[FactView]
) -> tuple[EvidenceCandidate | None, bool]:
    """单条要求的 LLM 举证候选；生成/校验失败按"无候选"处理（保守 → nearest/gap）。"""
    messages = prompts.build_evidence_messages(
        jd_text, req_index, req, [prompts.format_fact_line(fact) for fact in facts]
    )
    text, is_mock = llm.chat_json(
        messages, mock_provider=mock_rules.build_evidence_response
    )
    try:
        return EvidenceCandidate.model_validate(llm.parse_json(text)), is_mock
    except (ValueError, ValidationError):
        return None, is_mock


def _store(
    jd_text: str,
    resume_id: str,
    verified: list[VerifiedRequirement],
    report: DiagnosisReport,
    is_mock: bool,
    warnings: list[str],
) -> str:
    """diagnoses + requirements 一次事务入库（失败不留半成品行）。"""
    diagnosis_id = uuid.uuid4().hex
    report_payload = {**asdict(report), "warnings": warnings}
    conn = db.connect()
    try:
        with conn:
            io.insert_diagnosis(
                conn,
                diagnosis_id=diagnosis_id,
                resume_id=resume_id,
                jd_text=jd_text,
                report_json=json.dumps(report_payload, ensure_ascii=False),
                mock=is_mock,
            )
            for req in verified:
                io.insert_requirement(
                    conn,
                    requirement_id=uuid.uuid4().hex,
                    diagnosis_id=diagnosis_id,
                    req_index=req.req_index,
                    priority=req.priority,
                    text=req.text,
                    keywords_json=json.dumps(req.keywords, ensure_ascii=False),
                    status=req.status,
                    quote=req.quote,
                    fact_id=req.fact_id,
                    nearest_note=req.nearest_note,
                    user_revived=req.user_revived,
                )
    finally:
        conn.close()
    return diagnosis_id


# ---------------------------------------------------------------------------
# 读取（报告 + 要求清单）与列表
# ---------------------------------------------------------------------------

def _row_to_verified(row: sqlite3.Row) -> VerifiedRequirement:
    return VerifiedRequirement(
        req_index=row["req_index"],
        priority=row["priority"],
        text=row["text"],
        keywords=json.loads(row["keywords"]),
        status=row["status"],
        quote=row["quote"],
        fact_id=row["fact_id"],
        nearest_note=row["nearest_note"],
        user_revived=bool(row["user_revived"]),
    )


def _rebuild_report(
    diagnosis_row: sqlite3.Row, requirement_rows: list[sqlite3.Row]
) -> tuple[DiagnosisReport, list[str]]:
    """基于 requirements 行确定性重算报告（spec §12.6，不重新调 LLM）。

    warnings 是诊断生成时的元信息（拆解跳过条目等），从 report 快照保留。
    """
    report = build_report([_row_to_verified(row) for row in requirement_rows])
    try:
        warnings = json.loads(diagnosis_row["report"] or "{}").get("warnings", [])
    except json.JSONDecodeError:
        warnings = []
    return report, warnings if isinstance(warnings, list) else []


def _quote_span(canonical: str, quote: str | None) -> dict | None:
    """证据 quote 的唯一匹配区间（UTF-16 偏移，前端直接 slice 高亮）。"""
    if not quote:
        return None
    matches = find_quote_matches(canonical, quote)
    if len(matches) != 1:
        return None  # 0 处或多处：不给高亮坐标（不猜测）
    start, end = matches[0]
    return {
        "start": offsets.cp_to_utf16(canonical, start),
        "end": offsets.cp_to_utf16(canonical, end),
    }


def _requirement_payload(
    requirement_id: str, req: VerifiedRequirement, canonical: str
) -> dict:
    return {
        "id": requirement_id,
        "req_index": req.req_index,
        "priority": req.priority,
        "text": req.text,
        "keywords": req.keywords,
        "status": req.status,
        "quote": req.quote,
        "quote_span": _quote_span(canonical, req.quote) if req.status == "direct" else None,
        "fact_id": req.fact_id,
        "nearest_note": req.nearest_note,
        "user_revived": req.user_revived,
    }


def load_diagnosis(diagnosis_id: str) -> dict:
    """诊断详情：summary + 要求清单（must 前、缺口前）+ 硬性缺口 id 清单。

    gap 条目附带 ``candidate_facts``（降级候选）：读取时确定性计算、不落库；
    措辞上只作"可考虑的候选"，**不声称"最接近"**——把 ard/0004 的低成本
    人工确认入口保持在用户眼前。
    """
    conn = db.connect()
    try:
        diagnosis = io.fetch_diagnosis(conn, diagnosis_id)
        if diagnosis is None:
            raise KeyError(diagnosis_id)
        rows = io.fetch_requirements(conn, diagnosis_id)
        canonical = fact_store_io.fetch_canonical(conn, diagnosis["resume_id"]) or ""
        fact_views = io.fetch_confirmed_facts(conn, diagnosis["resume_id"])
    finally:
        conn.close()

    report, warnings = _rebuild_report(diagnosis, rows)
    id_by_index = {row["req_index"]: row["id"] for row in rows}
    requirements_out: list[dict] = []
    for req in report.requirements:
        payload = _requirement_payload(id_by_index[req.req_index], req, canonical)
        if req.status == "gap":
            payload["candidate_facts"] = [
                {"id": fact.fact_id, "section": fact.section, "raw_quote": fact.raw_quote}
                for fact in fallback_candidates(fact_views)
            ]
        requirements_out.append(payload)
    return {
        "id": diagnosis["id"],
        "resume_id": diagnosis["resume_id"],
        "jd_text": diagnosis["jd_text"],
        "created_at": diagnosis["created_at"],
        "mock": bool(diagnosis["mock"]),
        "summary": asdict(report.summary),
        "requirements": requirements_out,
        "gaps": [id_by_index[req.req_index] for req in report.gaps],
        "warnings": warnings,
    }


def list_diagnoses(resume_id: str) -> list[dict]:
    """某简历的历史诊断列表（新→旧）。"""
    conn = db.connect()
    try:
        if fact_store_io.fetch_resume(conn, resume_id) is None:
            raise KeyError(resume_id)
        rows = io.fetch_diagnoses(conn, resume_id)
    finally:
        conn.close()

    items: list[dict] = []
    for row in rows:
        try:
            report = json.loads(row["report"] or "{}")
        except json.JSONDecodeError:
            report = {}
        items.append(
            {
                "id": row["id"],
                "resume_id": row["resume_id"],
                "created_at": row["created_at"],
                "mock": bool(row["mock"]),
                "summary": report.get("summary"),
            }
        )
    return items


# ---------------------------------------------------------------------------
# 人工捞回（ard/0004 低成本确认点，spec §12.6）
# ---------------------------------------------------------------------------

def revive_requirement(requirement_id: str, fact_id: str) -> dict:
    """把一条 nearest/gap 捞回 direct（用户确认"最接近的 X 其实相关"）。

    **证据强度语义（P3 审查决议，勿与 pipeline direct 混同）**：

    - revive 的证据是 *fact_id 锚定*——用户选定条目、人工判定，仅要求
      quote 可定位（≥1 处），标记 ``user_revived=1``，UI 显示「人工确认」；
    - pipeline 的 direct 是 *quote 唯一定位*——程序验证逐字且唯一。

    保留宽松门槛的理由：多处匹配的条目在校对页只能"确认"、
    无法消除歧义（raw_quote 不可改），严格门槛会把它永久堵死，
    违背 ard/0004 的低成本捞回精神。

    其余约束：fact 必须属于同一诊断对应的简历；更新后基于 requirements
    行确定性重算报告（不重新调 LLM）。
    """
    conn = db.connect()
    try:
        requirement = io.fetch_requirement(conn, requirement_id)
        if requirement is None:
            raise KeyError(requirement_id)
        diagnosis = io.fetch_diagnosis(conn, requirement["diagnosis_id"])
        fact = fact_store_io.fetch_fact(conn, fact_id)
        if fact is None:
            raise KeyError(fact_id)
        if diagnosis is None or fact["resume_id"] != diagnosis["resume_id"]:
            raise ValueError("该事实条目不属于此诊断对应的简历，不能作为捞回证据")
        canonical = fact_store_io.fetch_canonical(conn, diagnosis["resume_id"]) or ""
        if not find_quote_matches(canonical, fact["raw_quote"]):
            raise ValueError(
                "该条目的原文锚点未定位（quote 回验失败），不能作为证据；请先在校对页处理该条目"
            )

        with conn:
            io.revive_requirement_row(
                conn, requirement_id, quote=fact["raw_quote"], fact_id=fact_id
            )
            rows = io.fetch_requirements(conn, diagnosis["id"])
            report, warnings = _rebuild_report(diagnosis, rows)
            report_payload = {**asdict(report), "warnings": warnings}
            io.update_report(
                conn, diagnosis["id"], json.dumps(report_payload, ensure_ascii=False)
            )

        updated = io.fetch_requirement(conn, requirement_id)
        return {
            "requirement": _requirement_payload(
                requirement_id, _row_to_verified(updated), canonical
            ),
            "summary": asdict(report.summary),
        }
    finally:
        conn.close()

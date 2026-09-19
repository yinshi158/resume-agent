"""diagnose HTTP 接口（spec §12.7）。

- ``POST /api/diagnoses``：新建诊断，SSE 流（step / done / error）；
  前置校验（400/404/422）在返回流之前同步完成——未 confirmed 的简历
  直接 422，不进 SSE（spec §13.4）；
- ``GET /api/diagnoses/{id}``：报告 + 要求清单；
- ``GET /api/resumes/{id}/diagnoses``：历史诊断列表；
- ``POST /api/requirements/{id}/revive``：人工捞回（body: fact_id）。

SSE 事件统一为 ``data: {JSON}``，JSON ``type`` 字段区分：
``step``（parsing / evidencing n/N / reporting）、``done``、``error``。
"""

from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import core

router = APIRouter(prefix="/api", tags=["diagnose"])


class DiagnoseRequest(BaseModel):
    resume_id: str
    jd_text: str


class ReviveRequest(BaseModel):
    fact_id: str


def _sse(event: dict) -> str:
    """统一 data JSON 行（前端只解析 data 行，容错简单）。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/diagnoses")
def create_diagnosis(payload: DiagnoseRequest) -> StreamingResponse:
    """新建诊断（SSE 流式进度）。

    前置校验同步返回普通 JSON 错误（前端按 HTTP 状态处理）；
    流内错误（LLM 失败/JD 拆解失败等）转 ``error`` 事件。
    """
    if not payload.jd_text.strip():
        raise HTTPException(status_code=400, detail="JD 内容为空，请粘贴职位描述后再诊断")
    try:
        core.assert_ready(payload.resume_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="简历不存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def event_stream() -> Iterator[str]:
        for event in core.iter_diagnosis(payload.resume_id, payload.jd_text):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 代理环境不缓冲
        },
    )


@router.get("/diagnoses/{diagnosis_id}")
def get_diagnosis(diagnosis_id: str) -> dict:
    try:
        return core.load_diagnosis(diagnosis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="诊断记录不存在") from exc


@router.get("/resumes/{resume_id}/diagnoses")
def list_resume_diagnoses(resume_id: str) -> dict:
    try:
        return {"diagnoses": core.list_diagnoses(resume_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="简历不存在") from exc


@router.post("/requirements/{requirement_id}/revive")
def revive_requirement(requirement_id: str, payload: ReviveRequest) -> dict:
    """人工捞回：用户确认"最接近的 X 其实相关"（ard/0004）。"""
    try:
        return core.revive_requirement(requirement_id, payload.fact_id)
    except KeyError as exc:
        missing = exc.args[0] if exc.args else ""
        detail = "诊断要求不存在" if missing == requirement_id else "事实条目不存在"
        raise HTTPException(status_code=404, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

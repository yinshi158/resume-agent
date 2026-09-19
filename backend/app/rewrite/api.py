"""rewrite HTTP 接口（spec §20）。

- ``POST /api/rewrites``：body: diagnosis_id；SSE 流
  （step writing / validating round n / escalated + done / error）；
  前置校验（404/422）在返回流之前同步完成——resume 已退回校对状态的
  直接 422，不进 SSE（改写入口咽喉，ard/0002）；
- ``GET /api/rewrites/{id}``：句集 + 各层判定 + 目标/升级清单 + mock 标记；
- ``GET /api/diagnoses/{id}/rewrites``：历史改写列表。

SSE 事件统一为 ``data: {JSON}``，JSON ``type`` 字段区分。
"""

from __future__ import annotations

import json
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import core

router = APIRouter(prefix="/api", tags=["rewrite"])


class RewriteRequest(BaseModel):
    diagnosis_id: str


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/rewrites")
def create_rewrite(payload: RewriteRequest) -> StreamingResponse:
    """新建改写（SSE 流式进度）。"""
    try:
        core.assert_ready(payload.diagnosis_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="诊断记录不存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def event_stream() -> Iterator[str]:
        for event in core.iter_rewrite(payload.diagnosis_id):
            yield _sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 代理环境不缓冲
        },
    )


@router.get("/rewrites/{rewrite_id}")
def get_rewrite(rewrite_id: str) -> dict:
    try:
        return core.load_rewrite(rewrite_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="改写记录不存在") from exc


@router.get("/diagnoses/{diagnosis_id}/rewrites")
def list_diagnosis_rewrites(diagnosis_id: str) -> dict:
    try:
        return {"rewrites": core.list_rewrites(diagnosis_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="诊断记录不存在") from exc

"""export HTTP 接口（spec §18/§20）。

- ``POST /api/exports``：body: rewrite_id；有 pending 句 → 422（第二咽喉，
  ard/0002）；SSE：rendering → done(version_id) / error；
- ``GET /api/exports/{id}/file``：下载 PDF。

注意：body 不再携带 gate_events（§9 变更标注）——确认事件已按句落库，
服务端按 rewrite_id 复核 pending 状态。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..gate2 import core as gate2_core
from ..platform import config, db
from ..rewrite import core as rewrite_core
from ..rewrite import io as rewrite_io
from . import ats, io, render

router = APIRouter(prefix="/api", tags=["export"])

TEMPLATE = "ats-v1"


class ExportRequest(BaseModel):
    rewrite_id: str


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/exports")
def create_export(payload: ExportRequest) -> StreamingResponse:
    """导出 PDF（SSE 流式进度；前置校验同步 404/422）。"""
    if not config.playwright_available():
        raise HTTPException(
            status_code=422,
            detail=(
                "未检测到 Playwright 浏览器（导出唯一渲染路径，R4）。"
                "请执行：pip install playwright && python -m playwright install chromium"
            ),
        )
    try:
        gate2_core.assert_exportable(payload.rewrite_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="改写记录不存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def event_stream() -> Iterator[str]:
        try:
            yield _sse({"type": "step", "step": "rendering", "message": "正在渲染 ATS 模板"})
            rewrite_payload = rewrite_core.load_rewrite(payload.rewrite_id)
            sentences = render.build_export_sentences(rewrite_payload)
            problems = ats.self_check(sentences)
            if problems:
                yield _sse(
                    {
                        "type": "error",
                        "message": "ATS 自检未通过，未生成文件：" + "；".join(problems),
                    }
                )
                return

            html = render.build_html(sentences)
            version_id = uuid.uuid4().hex
            relative_path = f"exports/{version_id}.pdf"
            out_path: Path = db.data_dir() / relative_path
            render.render_pdf(html, out_path)

            conn = db.connect()
            try:
                with conn:
                    io.insert_version(
                        conn,
                        version_id=version_id,
                        rewrite_id=payload.rewrite_id,
                        path=relative_path,
                        template=TEMPLATE,
                    )
                    rewrite_io.update_rewrite_status(conn, payload.rewrite_id, "exported")
            finally:
                conn.close()

            yield _sse(
                {
                    "type": "done",
                    "version_id": version_id,
                    "template": TEMPLATE,
                    "file_url": f"/api/exports/{version_id}/file",
                    "sentence_count": len(sentences),
                }
            )
        except Exception as exc:  # noqa: BLE001 —— SSE 流内错误统一转 error 事件
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/exports/{version_id}/file")
def download_export(version_id: str) -> FileResponse:
    conn = db.connect()
    try:
        version = io.fetch_version(conn, version_id)
    finally:
        conn.close()
    if version is None:
        raise HTTPException(status_code=404, detail="导出记录不存在")
    path = db.data_dir() / version["path"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="导出文件已不存在（数据目录可能被清理）")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"resume_{version_id[:8]}.pdf",
    )

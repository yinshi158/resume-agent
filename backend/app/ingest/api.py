"""ingest HTTP 接口（spec §7）。

- ``POST /api/resumes``：上传（multipart），触发解析，返回 resume_id；
- ``GET  /api/resumes/{id}``：状态 + canonical_text + parser + mock 标记；
- ``POST /api/resumes/{id}/reparse?parser=mineru``：重解析逃生门（新 resume 行）。
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..fact_store import io as fact_store_io
from ..platform import config, db
from . import core

router = APIRouter(prefix="/api", tags=["ingest"])

_ALLOWED_SUFFIXES = (".pdf", ".docx", ".doc")


@router.post("/resumes")
def upload_resume(file: UploadFile = File(...)) -> dict:
    """上传简历并同步完成解析（M1 单次 ≤1 分钟，任务式响应结构）。"""
    filename = file.filename or "upload"
    if not filename.lower().endswith(_ALLOWED_SUFFIXES):
        raise HTTPException(status_code=400, detail="仅支持 PDF / DOCX 格式的简历文件")
    data = file.file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        resume_id, is_mock, warnings = core.process_upload(filename, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "resume_id": resume_id,
        "status": "reviewing",
        "mock": is_mock,
        "warnings": warnings,
    }


@router.get("/resumes")
def list_resumes(status: str | None = None) -> dict:
    """简历列表（M2 诊断入口：选已 confirmed 简历 → 粘贴 JD）。"""
    conn = db.connect()
    try:
        rows = fact_store_io.fetch_resumes(conn, status=status)
    finally:
        conn.close()
    return {
        "resumes": [
            {
                "id": row["id"],
                "filename": row["filename"],
                "parser": row["parser"],
                "status": row["status"],
                "created_at": row["created_at"],
                "mock": row["parser"] == "mock",
            }
            for row in rows
        ]
    }


@router.get("/resumes/{resume_id}")
def get_resume(resume_id: str) -> dict:
    """状态 + canonical_text + parser + mock 标记。

    ``mock`` 为该版本解析时的真实抽取来源（按行记录）——真实模式解析的
    简历切换回 mock 模式后仍显示真实来源，反之亦然。
    """
    conn = db.connect()
    try:
        row = fact_store_io.fetch_resume(conn, resume_id)
        if row is None:
            raise HTTPException(status_code=404, detail="简历不存在")
        canonical = fact_store_io.fetch_canonical(conn, resume_id) or ""
        # 首次导入（无同文件名已确认版本）时必须全量过一遍；之后可只看标记
        siblings = fact_store_io.fetch_resumes_by_filename(conn, row["filename"])
        allow_marked_only = any(
            other["id"] != resume_id and other["status"] == "confirmed" for other in siblings
        )
    finally:
        conn.close()
    return {
        "id": row["id"],
        "filename": row["filename"],
        "parser": row["parser"],
        "normalize_version": row["normalize_version"],
        "status": row["status"],
        "created_at": row["created_at"],
        "canonical_text": canonical,
        # mock 溯源按行（审查 P3）：以解析时记录的 parser 为准，
        # 与当前全局模式无关——真实解析的简历不因后来切到 mock 模式而被误标
        "mock": row["parser"] == "mock",
        "allow_marked_only": allow_marked_only,
        "mineru_available": config.mineru_available(),
    }


@router.post("/resumes/{resume_id}/reparse")
def reparse_resume(resume_id: str, parser: str = "mineru") -> dict:
    """重解析逃生门：阅读序混乱时切换 MinerU 重新解析（产生新 resume 行）。"""
    if parser != "mineru":
        raise HTTPException(status_code=400, detail="重解析仅支持 parser=mineru")
    if not config.mineru_available():
        raise HTTPException(
            status_code=400,
            detail="未检测到 MinerU（M1 可选依赖）：请先安装 MinerU 后重试，或在设置页查看说明",
        )
    try:
        new_id = core.reparse(resume_id, parser=parser)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="简历不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"resume_id": new_id, "parser": parser}

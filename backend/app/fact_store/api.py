"""fact_store HTTP 接口（spec §7）。

- ``GET /api/resumes/{id}/facts``：事实源列表（含 span、attribution、
  anomaly_flags；span 为 UTF-16 偏移，前端直接 slice 高亮）。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from . import core

router = APIRouter(prefix="/api", tags=["fact_store"])


@router.get("/resumes/{resume_id}/facts")
def get_facts(resume_id: str) -> dict:
    try:
        facts = core.load_facts(resume_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="简历不存在") from exc
    return {
        "resume_id": resume_id,
        "facts": [fact.model_dump() for fact in facts],
    }

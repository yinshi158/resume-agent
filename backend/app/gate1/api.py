"""gate1 HTTP 接口（spec §7）。

- ``PATCH /api/facts/{id}``：人工写回（edited_payload / attribution / confirmed）；
- ``POST  /api/anomaly-flags/{id}/resolve``：标记异象已处理。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..fact_store.schemas import FactOut
from . import core
from .schemas import FactPatch

router = APIRouter(prefix="/api", tags=["gate1"])


@router.patch("/facts/{fact_id}", response_model=FactOut)
def patch_fact(fact_id: str, patch: FactPatch) -> FactOut:
    """人工写回单条事实（原文层不变，ard/0001）。"""
    try:
        return core.patch_fact(fact_id, patch)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="事实条目不存在") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/anomaly-flags/{flag_id}/resolve")
def resolve_flag(flag_id: str) -> dict:
    try:
        core.resolve_flag(flag_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="异象标记不存在") from exc
    return {"id": flag_id, "resolved": True}

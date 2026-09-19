"""gate2 HTTP 接口（spec §20）。

``POST /api/sentences/{id}/gate``：动作 confirm/reject/edit（+ edit 文本）
→ 写 gate_events（含当次判定快照）→ 更新句状态 → edit 时重校验（§17.3）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import core

router = APIRouter(prefix="/api", tags=["gate2"])


class GateAction(BaseModel):
    action: Literal["confirm", "reject", "edit"]
    text: str | None = None  # edit 时的编辑后文本


@router.post("/sentences/{sentence_id}/gate")
def gate_sentence(sentence_id: str, payload: GateAction) -> dict:
    try:
        return core.apply_gate_action(sentence_id, payload.action, payload.text)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="改写句不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

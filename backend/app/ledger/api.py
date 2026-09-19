"""ledger HTTP 接口（spec §20，v1 只记录不分析）。

- ``POST /api/applications``：记录投递（version_id + company/position/channel/applied_at）；
- ``POST /api/applications/{id}/outcomes``：追加投递结果（stage + note）；
- ``GET /api/applications``：投递列表。
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..export import io as export_io
from ..platform import db
from . import io

router = APIRouter(prefix="/api", tags=["ledger"])

Stage = Literal["viewed", "written_test", "interview", "offer", "rejected"]


class ApplicationCreate(BaseModel):
    version_id: str
    company: str = Field(min_length=1)
    position: str = Field(min_length=1)
    channel: str | None = None
    applied_at: str | None = None


class OutcomeCreate(BaseModel):
    stage: Stage
    note: str | None = None


@router.post("/applications")
def create_application(payload: ApplicationCreate) -> dict:
    conn = db.connect()
    try:
        if export_io.fetch_version(conn, payload.version_id) is None:
            raise HTTPException(status_code=404, detail="简历版本不存在（先导出再记录投递）")
        application_id = uuid.uuid4().hex
        with conn:
            io.insert_application(
                conn,
                application_id=application_id,
                version_id=payload.version_id,
                company=payload.company.strip(),
                position=payload.position.strip(),
                channel=(payload.channel or "").strip() or None,
                applied_at=payload.applied_at,
            )
        row = io.fetch_application(conn, application_id)
    finally:
        conn.close()
    return {"application": dict(row)}


@router.post("/applications/{application_id}/outcomes")
def create_outcome(application_id: str, payload: OutcomeCreate) -> dict:
    conn = db.connect()
    try:
        if io.fetch_application(conn, application_id) is None:
            raise HTTPException(status_code=404, detail="投递记录不存在")
        outcome_id = uuid.uuid4().hex
        with conn:
            io.insert_outcome(
                conn,
                outcome_id=outcome_id,
                application_id=application_id,
                stage=payload.stage,
                note=(payload.note or "").strip() or None,
            )
        rows = io.fetch_outcomes(conn, application_id)
    finally:
        conn.close()
    return {"outcomes": [dict(row) for row in rows]}


@router.get("/applications")
def list_applications() -> dict:
    conn = db.connect()
    try:
        rows = io.fetch_applications(conn)
        items = [
            {
                **dict(row),
                "outcomes": [dict(outcome) for outcome in io.fetch_outcomes(conn, row["id"])],
            }
            for row in rows
        ]
    finally:
        conn.close()
    return {"applications": items}

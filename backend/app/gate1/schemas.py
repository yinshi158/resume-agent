"""gate1 写回请求模型（spec §7 PATCH /api/facts/{id}）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Attribution = Literal["individual", "team", "mixed", "unknown"]


class FactPatch(BaseModel):
    """人工写回：edited_payload / attribution(+confirmed) / confirmed。

    未提供的字段不变（``None`` = 不修改）。
    """

    edited_payload: dict | None = None
    attribution: Attribution | None = None
    attribution_confirmed: bool | None = None
    confirmed: bool | None = None

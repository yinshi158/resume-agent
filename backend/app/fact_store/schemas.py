"""事实源 API Schema（对外输出）。

注意：``span_start`` / ``span_end`` 为 **UTF-16 码元偏移**——前端 JS
可直接对 canonical_text 做 slice 高亮（platform/offsets.py 统一转换）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnomalyFlagOut(BaseModel):
    id: str
    rule: str                  # date_order|date_overlap|date_ambiguous|percent_bound|amount_magnitude|missing_field
    detail: dict = Field(default_factory=dict)  # 人类可读 message + 机器可读参数
    resolved: bool = False


class FactOut(BaseModel):
    id: str
    resume_id: str
    section: str
    raw_quote: str
    span_start: int            # UTF-16 码元偏移；-1 = 回验失败未定位
    span_end: int
    payload: dict = Field(default_factory=dict)
    attribution: str           # individual|team|mixed|unknown
    attribution_confirmed: bool
    confirmed: bool
    edited_payload: dict | None = None
    created_at: str
    anomaly_flags: list[AnomalyFlagOut] = Field(default_factory=list)

    @property
    def effective_payload(self) -> dict:
        """人工修正优先（edited_payload 不改原文层，ard/0001）。"""
        return self.edited_payload if self.edited_payload is not None else self.payload

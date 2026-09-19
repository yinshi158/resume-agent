"""诊断报告汇总（纯函数，确定性；spec §12.5）。

- worth_applying：存在 must 缺口时不直接判否——**全部** must 均为缺口
  才提示"岗位可能不值得投"（ard/0004 的决策价值，避免过度报警）；
- gaps 清单是 M3 改写器的分母修正依据（硬性缺口不进改写目标）；
- 报告重算（如 revive 后）必须走本函数，不重新调 LLM（spec §12.6）。
"""

from __future__ import annotations

from .schemas import DiagnosisReport, ReportSummary, VerifiedRequirement

# 报告呈现顺序：must 在前；组内按 缺口 → 最接近 → 有证据（用户最该先看的在前）
_STATUS_ORDER = {"gap": 0, "nearest": 1, "direct": 2}


def build_report(requirements: list[VerifiedRequirement]) -> DiagnosisReport:
    must = [r for r in requirements if r.priority == "must"]
    preferred = [r for r in requirements if r.priority == "preferred"]

    must_gap = [r for r in must if r.status == "gap"]
    must_nearest = [r for r in must if r.status == "nearest"]
    worth_applying = not (must and len(must_gap) == len(must))

    notice = None
    if not worth_applying:
        notice = (
            f"该岗位 {len(must)} 条硬性要求在事实源中均无对应经历，"
            "这个岗位可能不值得投——建议补充经历或更换目标（ard/0004）"
        )

    ordered = sorted(
        requirements,
        key=lambda r: (
            0 if r.priority == "must" else 1,
            _STATUS_ORDER[r.status],
            r.req_index,
        ),
    )
    return DiagnosisReport(
        summary=ReportSummary(
            must_total=len(must),
            must_direct=sum(1 for r in must if r.status == "direct"),
            must_nearest=len(must_nearest),
            must_gap=len(must_gap),
            preferred_total=len(preferred),
            preferred_direct=sum(1 for r in preferred if r.status == "direct"),
            worth_applying=worth_applying,
            notice=notice,
        ),
        requirements=ordered,
        gaps=[r for r in requirements if r.status == "gap" and not r.user_revived],
    )

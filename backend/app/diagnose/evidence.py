"""举证验证与最近邻（纯函数，确定性；spec §12.3）。

分流规则（保守，ard/0004）：

- quote 在 canonical_text **唯一**定位 → direct；
- 0 处或**多处**匹配 → 视为无证据（多处歧义不猜测，M1 审查 P2 复用
  同一机制），转最近邻；
- 最近邻：在 confirmed 事实源中按关键词重叠**确定性**排序取 top1
  （无 LLM），有候选 → nearest，无 → gap。

LLM 的 fact_id 声明只做最近邻的提示，锚定永远以程序验证为准。
"""

from __future__ import annotations

from ..shared.normalize import find_quote_matches
from .schemas import EvidenceCandidate, FactView, RequirementItem, VerifiedRequirement


def verify_quote(canonical: str, quote: str | None) -> bool:
    """quote 是否为可直接采信的证据：逐字 + 唯一定位。"""
    if not quote or not quote.strip():
        return False
    return len(find_quote_matches(canonical, quote)) == 1


def _keyword_hits(text: str, keywords: list[str]) -> int:
    """关键词命中数（去重；ASCII 大小写不敏感，中文直接包含）。"""
    lowered = text.lower()
    hits = 0
    seen: set[str] = set()
    for kw in keywords:
        kw_norm = kw.strip().lower()
        if kw_norm and kw_norm not in seen and kw_norm in lowered:
            hits += 1
            seen.add(kw_norm)
    return hits


def rank_nearest(facts: list[FactView], keywords: list[str]) -> FactView | None:
    """最近邻排序：关键词重叠数降序，fact_id 升序兜底（完全确定性）。"""
    scored = [
        (fact, _keyword_hits(fact.searchable_text, keywords))
        for fact in facts
    ]
    scored = [(fact, hits) for fact, hits in scored if hits > 0]
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[1], item[0].fact_id))
    return scored[0][0]


# 降级候选的 section 权重：经历类优先（gap 行兜底捞回列表）
_SECTION_RANK = {"work": 0, "project": 1}


def fallback_candidates(facts: list[FactView], limit: int = 3) -> list[FactView]:
    """零关键词命中时的降级候选（gap 行兜底捞回列表）。

    只在措辞上作"可考虑的候选"呈现，**不声称"最接近"**——语义相关性
    不是词法命中能判定的（M3 的 L2 职责）；候选存在的意义是把
    ard/0004 的低成本人工确认入口保持在用户眼前。规则（完全确定性）：

    - 只取**入库时唯一可定位**的条目（保证捞回可用：revive 会复验 quote
      可定位，候选若不可定位会让用户点了报错）；
    - 经历类 section（work / project）优先，其余随后；
    - 同组保持 facts 的文档顺序（稳定排序）。
    """
    locatable = [fact for fact in facts if fact.locatable]
    ranked = sorted(locatable, key=lambda fact: _SECTION_RANK.get(fact.section, 2))
    return ranked[:limit]


def resolve_requirement(
    canonical: str,
    req: RequirementItem,
    req_index: int,
    candidate: EvidenceCandidate | None,
    facts: list[FactView],
) -> VerifiedRequirement:
    """单条要求的验证分流：direct / nearest / gap。"""
    # 1) 直接证据：quote 程序验证（唯一定位才采信）
    if candidate is not None and verify_quote(canonical, candidate.quote):
        # fact_id 声明仅参考；若声明存在则带上，缺失/不合法时按 span 定位
        # 到的条目为准（此处取声明或 None——M3 可按 span 反查条目）
        fact_id = candidate.fact_id if candidate.fact_id in {f.fact_id for f in facts} else None
        return VerifiedRequirement(
            req_index=req_index,
            priority=req.priority,
            text=req.text,
            keywords=req.keywords,
            status="direct",
            quote=candidate.quote,
            fact_id=fact_id,
        )

    # 2) 最近邻：优先模型声明的 nearest_fact_id（仍须关键词非零命中），
    #    否则确定性排序取 top1
    declared: FactView | None = None
    if candidate is not None and candidate.nearest_fact_id:
        declared = next(
            (f for f in facts if f.fact_id == candidate.nearest_fact_id), None
        )
        if declared is not None and _keyword_hits(declared.searchable_text, req.keywords) == 0:
            declared = None  # 声明不靠谱，回退确定性排序
    nearest = declared or rank_nearest(facts, req.keywords)
    if nearest is not None:
        note = f"无直接证据，最接近的经历：{nearest.raw_quote[:60]}"
        return VerifiedRequirement(
            req_index=req_index,
            priority=req.priority,
            text=req.text,
            keywords=req.keywords,
            status="nearest",
            fact_id=nearest.fact_id,
            nearest_note=note,
        )

    # 3) 缺口：**不声称**验证过相关性——我们只验证了"没有关键词重叠"。
    #    过度断言会切断"低成本人机确认点"（ard/0004）：文案必须提示
    #    可手动挑选，而不是让用户相信系统判过相关性后直接放弃。
    return VerifiedRequirement(
        req_index=req_index,
        priority=req.priority,
        text=req.text,
        keywords=req.keywords,
        status="gap",
        nearest_note="未找到关键词重叠的经历，可从下方选择相关条目捞回",
    )

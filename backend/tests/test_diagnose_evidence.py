"""diagnose 证据验证与报告汇总单测（spec §13.4 单测层）。

覆盖分流矩阵：quote 0 处 / 1 处 / 多处匹配 → gap / direct / 转最近邻；
最近邻排序确定性；报告 worth_applying 与分母修正（gaps 清单）。
"""

from __future__ import annotations

from app.diagnose.evidence import (
    fallback_candidates,
    rank_nearest,
    resolve_requirement,
    verify_quote,
)
from app.diagnose.report import build_report
from app.diagnose.schemas import (
    EvidenceCandidate,
    FactView,
    RequirementItem,
    VerifiedRequirement,
)
from app.shared.normalize import normalize

_CANONICAL = normalize(
    "技能：Python、SQL\n"
    "项目一：用 Python 做推荐系统重构，DAU 提升 30%\n"
    "项目二：用 Python 做搜索\n"
    "甲公司 | 后端工程师 | 负责订单系统"
)

_FACTS = [
    FactView(fact_id="f-rec", section="project", raw_quote="项目一：用 Python 做推荐系统重构，DAU 提升 30%",
             searchable_text="项目一：用 Python 做推荐系统重构，DAU 提升 30% name=项目一"),
    FactView(fact_id="f-search", section="project", raw_quote="项目二：用 Python 做搜索",
             searchable_text="项目二：用 Python 做搜索 name=项目二"),
    FactView(fact_id="f-work", section="work", raw_quote="甲公司 | 后端工程师 | 负责订单系统",
             searchable_text="甲公司 后端工程师 负责订单系统"),
]


def _req(text: str = "3 年以上推荐系统经验", priority: str = "must",
         keywords: list[str] | None = None) -> RequirementItem:
    return RequirementItem(priority=priority, text=text,
                           keywords=keywords or ["推荐系统", "recommender"])


# ---------------------------------------------------------------------------
# verify_quote：0 / 1 / N 处分流
# ---------------------------------------------------------------------------

def test_verify_quote_unique_match() -> None:
    assert verify_quote(_CANONICAL, "推荐系统重构") is True


def test_verify_quote_zero_match() -> None:
    assert verify_quote(_CANONICAL, "管理团队 20 人") is False
    assert verify_quote(_CANONICAL, None) is False
    assert verify_quote(_CANONICAL, "  ") is False


def test_verify_quote_multiple_matches_rejected() -> None:
    """多处匹配不采信（审查 P2：歧义证据会让举证指错位置）。"""
    assert verify_quote(_CANONICAL, "Python") is False  # 出现 3 次


# ---------------------------------------------------------------------------
# resolve_requirement 分流
# ---------------------------------------------------------------------------

def test_resolve_direct_with_verified_quote() -> None:
    candidate = EvidenceCandidate(req_index=0, quote="推荐系统重构", fact_id="f-rec")
    result = resolve_requirement(_CANONICAL, _req(), 0, candidate, _FACTS)
    assert result.status == "direct"
    assert result.quote == "推荐系统重构"
    assert result.fact_id == "f-rec"


def test_resolve_direct_drops_invalid_fact_id_claim() -> None:
    """fact_id 声明不被信任：不在事实源里的声明 id 置空，证据仍成立。"""
    candidate = EvidenceCandidate(req_index=0, quote="推荐系统重构", fact_id="f-不存在")
    result = resolve_requirement(_CANONICAL, _req(), 0, candidate, _FACTS)
    assert result.status == "direct"
    assert result.fact_id is None


def test_resolve_multi_match_falls_to_nearest() -> None:
    """quote 多处匹配 → 不当证据，转最近邻而非硬配（保守）。"""
    candidate = EvidenceCandidate(req_index=0, quote="Python")
    result = resolve_requirement(_CANONICAL, _req(), 0, candidate, _FACTS)
    assert result.status == "nearest"
    assert result.fact_id == "f-rec"  # 命中"推荐系统"关键词最多
    assert "最接近" in (result.nearest_note or "")


def test_resolve_declared_nearest_with_zero_hits_falls_back() -> None:
    """模型声明的最近邻零关键词命中 → 回退确定性排序（声明不被信任）。"""
    candidate = EvidenceCandidate(req_index=0, quote=None, nearest_fact_id="f-work")
    result = resolve_requirement(_CANONICAL, _req(), 0, candidate, _FACTS)
    assert result.status == "nearest"
    assert result.fact_id == "f-rec"


def test_resolve_gap_when_no_keyword_overlap() -> None:
    """gap 措辞只声明"未找到关键词重叠"，不声称验证过相关性（不可过度断言）。"""
    req = _req(text="5 年管理经验", keywords=["团队管理", "领导力"])
    result = resolve_requirement(_CANONICAL, req, 0, None, _FACTS)
    assert result.status == "gap"
    assert result.fact_id is None
    assert "未找到关键词重叠" in (result.nearest_note or "")
    assert "捞回" in (result.nearest_note or "")  # 人工确认入口必须可见（ard/0004）


# ---------------------------------------------------------------------------
# rank_nearest 确定性
# ---------------------------------------------------------------------------

def test_rank_nearest_deterministic_tie_break() -> None:
    """重叠数相同时按 fact_id 升序——完全确定，无随机性。"""
    keywords = ["Python"]
    first = rank_nearest(_FACTS, keywords)
    assert first is not None and first.fact_id == "f-rec"  # f-rec < f-search
    assert rank_nearest(_FACTS, keywords).fact_id == first.fact_id


def test_rank_nearest_zero_hits_returns_none() -> None:
    assert rank_nearest(_FACTS, ["量子计算"]) is None


# ---------------------------------------------------------------------------
# fallback_candidates（gap 降级候选：经历类优先 + 只取可定位条目）
# ---------------------------------------------------------------------------

def test_fallback_candidates_work_project_first() -> None:
    facts = [
        FactView(fact_id="f-skill", section="skill", raw_quote="技能行", searchable_text="技能行"),
        FactView(fact_id="f-work", section="work", raw_quote="工作行", searchable_text="工作行"),
        FactView(fact_id="f-proj", section="project", raw_quote="项目行", searchable_text="项目行"),
    ]
    picked = fallback_candidates(facts)
    # 经历类优先（work → project → 其他），同组保持文档顺序
    assert [f.fact_id for f in picked] == ["f-work", "f-proj", "f-skill"]


def test_fallback_candidates_excludes_unlocatable_and_limits() -> None:
    facts = [
        FactView(fact_id="f-a", section="work", raw_quote="A", searchable_text="A"),
        FactView(fact_id="f-b", section="work", raw_quote="B", searchable_text="B", locatable=False),
        FactView(fact_id="f-c", section="project", raw_quote="C", searchable_text="C"),
        FactView(fact_id="f-d", section="summary", raw_quote="D", searchable_text="D"),
        FactView(fact_id="f-e", section="work", raw_quote="E", searchable_text="E"),
    ]
    picked = fallback_candidates(facts, limit=3)
    ids = [f.fact_id for f in picked]
    # 不可定位条目不进候选（保证捞回可用：revive 会复验 quote 可定位）
    assert "f-b" not in ids
    assert ids == ["f-a", "f-e", "f-c"]  # work 文档序 → project；summary 被 limit 截掉


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------

def _vreq(index: int, priority: str, status: str) -> VerifiedRequirement:
    return VerifiedRequirement(
        req_index=index, priority=priority, text=f"要求{index}",
        keywords=[], status=status,
    )


def test_report_counts_and_ordering() -> None:
    reqs = [
        _vreq(0, "preferred", "direct"),
        _vreq(1, "must", "direct"),
        _vreq(2, "must", "gap"),
        _vreq(3, "must", "nearest"),
    ]
    report = build_report(reqs)
    s = report.summary
    assert (s.must_total, s.must_direct, s.must_nearest, s.must_gap) == (3, 1, 1, 1)
    assert s.preferred_total == 1 and s.preferred_direct == 1
    assert s.worth_applying is True and s.notice is None
    # 呈现顺序：must 在前，组内 gap → nearest → direct
    assert [(r.priority, r.status) for r in report.requirements] == [
        ("must", "gap"), ("must", "nearest"), ("must", "direct"), ("preferred", "direct"),
    ]
    # 分母修正：gaps 清单只含未捞回的缺口
    assert [r.req_index for r in report.gaps] == [2]


def test_report_all_must_gap_not_worth_applying() -> None:
    report = build_report([_vreq(0, "must", "gap"), _vreq(1, "must", "gap")])
    assert report.summary.worth_applying is False
    assert "可能不值得投" in (report.summary.notice or "")


def test_report_revived_gap_excluded_from_gaps() -> None:
    revived = VerifiedRequirement(
        req_index=0, priority="must", text="要求0", keywords=[],
        status="direct", user_revived=True,
    )
    report = build_report([revived])
    assert report.gaps == []

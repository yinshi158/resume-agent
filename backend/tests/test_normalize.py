"""normalize 属性测试与偏移稳定性快照（spec §3.3，M1 必交付）。

- 幂等性：hypothesis 随机中文/英文/空白混合串；
- 不变量：输出不含 U+3000、``\\r``、连续空格；字符集内 ``len`` 不增长；
- 偏移稳定性快照：fixtures 中文简历的 canonical_text 全文与抽样 span，
  normalize/sanitize 任何改动触发快照失败（= 必须 NORMALIZE_VERSION +1
  并全量重入库，ard/0001）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.shared.normalize import NORMALIZE_VERSION, find_quote, normalize, verify_span
from conftest import SNAPSHOTS_DIR, canonical_of, fixture_names, load_fixture_text, write_or_compare_snapshot

# 中文/英文/空白混合字母表（含全角空格、制表符、CRLF、零宽字符）
_ALPHABET = "中文测试简历工程师有限公司北京市年月至今 \t\u3000\r\nabcXYZ019%.-|、（）%\u200b"

_SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@_SETTINGS
@given(st.text(alphabet=_ALPHABET, max_size=200))
def test_normalize_is_idempotent(text: str) -> None:
    once = normalize(text)
    assert normalize(once) == once


@_SETTINGS
@given(st.text(alphabet=_ALPHABET, max_size=200))
def test_normalize_invariants(text: str) -> None:
    out = normalize(text)
    assert "\u3000" not in out          # 全角空格已折叠
    assert "\r" not in out              # 换行已统一
    assert "\t" not in out              # 制表符折叠为空格
    assert "  " not in out              # 无连续空格
    assert len(out) <= len(text)        # 该字符集内 NFKC 不增长（模块 docstring 有说明）


def test_normalize_whitespace_contract() -> None:
    """空白折叠的固定行为（实现契约，改动即快照失败）。"""
    assert normalize("a\r\nb\rc") == "a\nb\nc"
    assert normalize("a    b\t\tc") == "a b c"
    assert normalize("a \u3000 b") == "a b"
    assert normalize("line   \nnext") == "line\nnext"
    assert normalize("  indented") == " indented"  # 行首空白折叠为单个空格（不删除）
    assert normalize("") == ""
    assert normalize("\r\n") == "\n"


def test_find_quote_handles_fullwidth_and_whitespace() -> None:
    """全半角/空白差异不误报（P1 已知问题的验收）。"""
    canonical = normalize("负责推荐系统重构，ＤＡＵ 从　70万 提升到 91万")
    quote = "负责推荐系统重构，DAU 从 70万 提升到 91万"
    span = find_quote(canonical, quote)
    assert span is not None
    assert canonical[span[0] : span[1]] == normalize(quote)


def test_find_quote_tolerates_extra_surrounding_whitespace() -> None:
    canonical = normalize("负责推荐系统重构")
    assert find_quote(canonical, "\u3000负责推荐系统重构\u3000") == (0, len(canonical))


def test_find_quote_is_case_sensitive() -> None:
    """大小写折叠不是 normalize 的职责（spec §3.1）。"""
    canonical = normalize("Hello World")
    assert find_quote(canonical, "hello world") is None


def test_find_quote_not_found_returns_none() -> None:
    canonical = normalize("负责推荐系统重构")
    assert find_quote(canonical, "负责推荐算法") is None
    assert find_quote(canonical, "") is None
    assert find_quote(canonical, "   ") is None


def test_find_quote_matches_multiple() -> None:
    """重复文本必须报告全部匹配——调用方据匹配数判定歧义（审查 P2）。"""
    from app.shared.normalize import find_quote_matches

    content = normalize("技能：Python、SQL\n项目一：用 Python 做推荐\n项目二：用 Python 做搜索")
    matches = find_quote_matches(content, "Python")
    assert len(matches) == 3
    assert all(content[s:e] == "Python" for s, e in matches)
    # 唯一匹配与零匹配的边界
    assert len(find_quote_matches(content, "推荐")) == 1
    assert find_quote_matches(content, "不存在的词") == []
    # find_quote 保持"首个匹配"语义不变（向后兼容）
    assert find_quote(content, "Python") == matches[0]


def test_verify_span_positive_and_negative() -> None:
    canonical = normalize("负责推荐系统重构，DAU 从 70 万提升到 91 万")
    span = find_quote(canonical, "推荐系统重构")
    assert span == (2, 8)
    assert verify_span(canonical, span, "推荐系统重构")
    assert not verify_span(canonical, span, "推荐系统")   # 前缀不算
    assert not verify_span(canonical, span, "推荐系统重构项目")


def test_verify_span_rejects_out_of_range() -> None:
    canonical = "abc"
    assert not verify_span(canonical, (1, 10), "bc")
    assert not verify_span(canonical, (2, 1), "b")
    assert not verify_span(canonical, (-1, -1), "")


# ---------------------------------------------------------------------------
# 偏移稳定性快照
# ---------------------------------------------------------------------------

_SAMPLE_WINDOWS = [(0, 80), (200, 280), (400, 480)]
_ANCHOR_KEYWORDS = ("DAU", "工作经历", "Python")


def _snapshot_payload(name: str) -> dict:
    canonical = canonical_of(load_fixture_text(name))
    samples = []
    for start, end in _SAMPLE_WINDOWS:
        s, e = min(start, len(canonical)), min(end, len(canonical))
        samples.append({"start": s, "end": e, "text": canonical[s:e]})
    anchors = {}
    for keyword in _ANCHOR_KEYWORDS:
        span = find_quote(canonical, keyword)
        if span is not None:
            anchors[keyword] = {"span": list(span), "verified": verify_span(canonical, span, keyword)}
    return {
        "normalize_version": NORMALIZE_VERSION,
        "canonical": canonical,
        "samples": samples,
        "anchors": anchors,
    }


@pytest.mark.parametrize("name", fixture_names())
def test_canonical_snapshot(name: str) -> None:
    payload = _snapshot_payload(name)
    path = Path(SNAPSHOTS_DIR) / f"{Path(name).stem}.json"
    if write_or_compare_snapshot(path, payload):
        pytest.skip(f"快照首次生成：{path.name}（再次运行将对比）")

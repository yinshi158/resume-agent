"""全系统唯一归一化实现（ard/0001，spec §3）。

``canonical_text`` 是所有 span / quote / 高亮联动的唯一锚定载体，
入库即不可变。本模块是它唯一的生成与校验实现：入库与 quote 校验共用，
任何改动 = ``NORMALIZE_VERSION`` +1 = 全量重入库。

归一化规则（机械、无损、无歧义，顺序固定）：

1. Unicode NFKC（覆盖全角→半角、兼容字符折叠）；
2. 空白折叠：
   - 换行统一：``\\r\\n`` / ``\\r`` → ``\\n``；
   - 行内连续空白字符（含 NFKC 前的全角空格 U+3000、制表符等）→ 单个空格；
   - 行尾空白删除；行首空白折叠为单个空格（不删除——规则只授权行尾删除）。

**不做**：日期归一化（抽取层职责，ard/0001）、大小写折叠、标点替换、
任何删除非空白字符的操作。

已知不变量（tests/test_normalize.py 覆盖）：

- 幂等性：``normalize(normalize(x)) == normalize(x)``；
- 输出不含 U+3000、``\\r``、连续空格；
- 对"中文/英文/空白混合"字符集，``len(normalize(x)) <= len(x)``
  （NFKC 对个别兼容字符可能归一化为更长的等价串，如 ``½`` → ``1⁄2``，
  该情形不在不变量断言字符集内——见测试说明）。
"""

from __future__ import annotations

import re
import unicodedata

#: 规则或顺序的任何变更 = 版本 +1 = 全量重入库（ard/0001）
NORMALIZE_VERSION = 1

# 行内连续空白（不含换行；\s 覆盖普通空格、\t、全角空格 U+3000 等）
_INLINE_WS_RE = re.compile(r"[^\S\n]+")


def _normalize_line(line: str) -> str:
    """单行处理：行内连续空白 → 单个空格；行尾空白删除。"""
    return _INLINE_WS_RE.sub(" ", line).rstrip(" ")


def normalize(text: str) -> str:
    """机械归一化（幂等；先归一化再算偏移）。"""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", text)
    # 换行统一在 NFKC 之后（NFKC 不改变 \r\n）
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_normalize_line(line) for line in s.split("\n"))


def verify_span(content: str, span: tuple[int, int], quote: str) -> bool:
    """验证 ``span`` 指向 ``content`` 的片段等于归一化后的 ``quote``。

    ``content`` 必须是 canonical_text（已归一化），``quote`` 是待验证的
    原始引用——先过同一个 normalize 再比较，全半角/空白差异不误报（P1）。
    """
    start, end = span
    if start < 0 or end < start or end > len(content):
        return False
    return content[start:end] == normalize(quote)


def find_quote_matches(content: str, quote: str) -> list[tuple[int, int]]:
    """在 ``content`` 中定位归一化后的 ``quote`` 的**全部**匹配区间。

    找不到返回空列表（调用方须按保守一侧处理：标异常交人工，不猜测）。
    多处匹配时调用方不得静默取第一个——重复文本（如技能词出现在多个
    段落）锚定到错误实例会让高亮/证据指错位置（审查 P2）。
    """
    if not quote or not quote.strip():
        return []
    q = normalize(quote)
    if not q:
        return []
    matches = _find_all(content, q)
    if matches:
        return matches
    # LLM 常在 quote 首尾携带多余空白：折叠后行首可能残留一个空格，
    # 去掉行首空格再试一次（仅首次失败后启用，不改变首次成功的结果）
    q2 = q.strip(" ")
    if q2 and q2 != q:
        return _find_all(content, q2)
    return []


def _find_all(content: str, needle: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    idx = content.find(needle)
    while idx >= 0:
        out.append((idx, idx + len(needle)))
        idx = content.find(needle, idx + len(needle))
    return out


def find_quote(content: str, quote: str) -> tuple[int, int] | None:
    """在 ``content`` 中定位归一化后的 ``quote``，返回首个匹配 ``(start, end)``。

    找不到返回 ``None``。注意：多处匹配时本函数返回**第一个**——
    入库锚定等需要唯一定位的场景请用 ``find_quote_matches`` 自行判定。
    """
    matches = find_quote_matches(content, quote)
    return matches[0] if matches else None

"""ATS 规则自检（导出前确定性检查，spec §18.1）。

规则库 v1 只收确定项：单栏、标准章节名、无表格文本框、倒序时间、
关键词中英文并写。本模块负责**渲染前**对句集跑确定性规则：
超长行、表格/制表字符、非标准 section 名、空句；fail 即 error 不出文件。
"""

from __future__ import annotations

#: 单句行超过该长度视为 ATS 解析风险（截断/换行错乱）
MAX_LINE_CHARS = 200

#: 表格/制表字符（v1 模板单栏无表格，正文出现即违规）
_TABLE_MARKS = ("|", "｜", "\t", "┃", "│")

#: 标准章节名集合（section 枚举，spec §2）
ALLOWED_SECTIONS = frozenset(
    {"summary", "work", "project", "education", "skill", "other"}
)


def self_check(sentences: list[dict]) -> list[str]:
    """返回问题清单（空 = 可导出）。sentences: [{id, section, text}]。"""
    problems: list[str] = []
    for sentence in sentences:
        text = (sentence.get("text") or "").strip()
        label = (sentence.get("id") or "?")[:8]
        if not text:
            problems.append(f"句子 {label} 为空")
            continue
        if len(text) > MAX_LINE_CHARS:
            problems.append(
                f"句子 {label} 超长（{len(text)} > {MAX_LINE_CHARS} 字符），ATS 解析易截断"
            )
        for mark in _TABLE_MARKS:
            if mark in text:
                problems.append(f"句子 {label} 含表格/制表字符「{mark}」")
        if sentence.get("section") not in ALLOWED_SECTIONS:
            problems.append(
                f"句子 {label} 使用了非标准章节名：{sentence.get('section')!r}"
            )
    return problems

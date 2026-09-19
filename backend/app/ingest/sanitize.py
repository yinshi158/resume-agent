"""文本层清洗（防注入，spec §4）。

- 剔除零宽字符（U+200B–U+200D、U+FEFF）；
- 与背景同色的文本层（PDF 提取时按颜色过滤，见 ingest/io.py）；
- 明显注入句式**只记录不执行**（LLM 输入侧另有 system prompt 护栏）。

清洗是字符级的机械操作，先于 normalize；清洗结果进入锚定层。
"""

from __future__ import annotations

import re

# 零宽字符（spec 指定集合）：零宽空格/非连接符/连接符/零宽不换行空格(BOM)
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")

# 明显注入句式（中英文）；命中只记录、不删改内容
_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"忽略(以上|上面|前面|之前|先前)?(所有)?(的)?(指令|指示|要求|提示|内容)"), "忽略指令"),
    (re.compile(r"无视(以上|上面|前面|之前|先前)?(所有)?(的)?(指令|指示|要求|提示)"), "无视指令"),
    (re.compile(r"ignore\s+(all\s+)?(the\s+)?(previous|above|prior|foregoing)\s+(instruction|prompt|rule)", re.IGNORECASE), "ignore instructions"),
    (re.compile(r"disregard\s+(all\s+)?(the\s+)?(previous|above|prior)", re.IGNORECASE), "disregard previous"),
    (re.compile(r"forget\s+(all\s+)?(your\s+)?(previous\s+)?(instruction|prompt|rule)", re.IGNORECASE), "forget instructions"),
    (re.compile(r"你现在是|你现在扮演|从现在开始你是"), "角色扮演指令"),
    (re.compile(r"(system|assistant)\s*[:：]\s*", re.IGNORECASE), "伪造对话角色"),
    (re.compile(r"<\|(im_start|im_end|system|endoftext)\|>", re.IGNORECASE), "特殊标记注入"),
    (re.compile(r"\[/?(INST|SYS)\]", re.IGNORECASE), "特殊标记注入"),
]


def clean(text: str) -> tuple[str, list[str]]:
    """清洗文本层。

    :returns: ``(清洗后文本, 告警列表)``；告警列表供 API 响应与日志展示，
        不改变用户内容（注入句式只记录不执行）。
    """
    warnings: list[str] = []
    if not text:
        return "", warnings

    cleaned = _ZERO_WIDTH_RE.sub("", text)
    removed = len(text) - len(cleaned)
    if removed:
        warnings.append(f"已剔除 {removed} 个零宽字符（隐形文本层）")

    for pattern, label in _INJECTION_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            snippet = match.group(0)[:40]
            warnings.append(
                f"检测到疑似提示注入（{label}）：\u201c{snippet}\u201d——已按简历内容处理，未执行"
            )
    return cleaned, warnings

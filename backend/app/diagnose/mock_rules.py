"""mock 模式的诊断规则（无 key 全链路可跑通，ard/0007）。

- 拆解：确定性规则从 JD 分行/分句提取要求（must/preferred 启发式 +
  关键词词表命中）；**质量不代表真实 LLM 水平**，界面与响应头须标示 mock；
- 举证：按关键词重叠确定性排序取 top1 条目的原文作为 quote 候选——
  候选仍要过 evidence.py 的程序验证（mock 不承诺通过，验证机制照常）；
- 本模块只用 prompts 的标记解析与自有词表，不反向依赖 ingest 内部实现。
"""

from __future__ import annotations

import json
import re
from typing import Sequence

from . import prompts

# ---------------------------------------------------------------------------
# 关键词词表（互联网技术岗：推荐算法 / 后端 / 数据方向）
# ---------------------------------------------------------------------------

_KEYWORD_VOCAB: tuple[str, ...] = (
    # 算法/推荐
    "推荐系统", "推荐算法", "召回", "排序", "CTR", "深度学习", "机器学习",
    "TensorFlow", "PyTorch", "特征工程", "A/B 测试", "自然语言处理", "NLP",
    "计算机视觉", "大模型", "LLM",
    # 后端
    "Python", "Java", "Go", "Golang", "C++", "Node.js", "Spring", "后端开发",
    "微服务", "高并发", "分布式系统", "分布式", "MySQL", "PostgreSQL",
    "Redis", "Kafka", "消息队列", "Docker", "Kubernetes", "K8s", "Linux",
    "服务治理", "架构设计", "系统设计", "性能优化",
    # 数据
    "SQL", "Spark", "Flink", "Hadoop", "Hive", "数据仓库", "ETL",
    "数据分析", "数据挖掘", "数据建模", "离线计算", "实时计算",
    # 运营/增长
    "用户增长", "活动运营", "内容运营", "数据驱动", "会员体系", "渠道投放",
    "转化率", "GMV", "ROI", "留存", "拉新",
    # 通用/软素质
    "团队协作", "沟通能力", "项目管理", "带团队", "管理经验", "英语",
    "本科", "硕士", "计算机",
)

# 加分项标记（命中 → preferred，否则 must）
_PREFERRED_RE = re.compile(r"优先|加分|更佳|更好|nice to have|plus|preferred", re.IGNORECASE)

# 行首 bullet / 编号前缀
_BULLET_RE = re.compile(r"^\s*(?:[-*•·●○]|\(?\d+[.、)]|[（(]\d+[)）])\s*")

# 句级切分（中文分号/句号；英文句点后跟空白）
_SENTENCE_RE = re.compile(r"[；;。]|(?<=[^\d])\.(?=\s|$)")
_COMMA_RE = re.compile(r"[，,]")

# 纯标题行（不是要求本身）
_TITLE_WORDS = frozenset(
    {
        "岗位职责", "工作职责", "职位描述", "任职要求", "岗位要求", "任职资格",
        "加分项", "优先条件", "职位要求", "我们提供", "你将获得", "薪资福利",
    }
)

# 明确非要求行（公司介绍/投递方式等）
_NARRATIVE_HINTS = (
    "公司简介", "团队介绍", "福利待遇", "薪资范围", "简历投递", "投递方式",
    "联系方式", "邮箱投递", "扫码", "工作地点", "上班时间",
)

_MIN_CLAUSE_LEN = 4

_ASCII_KW_RE = r"(?<![a-z0-9+#]){}(?![a-z0-9+#])"


def _keyword_hit(text: str, keyword: str) -> bool:
    """关键词命中判定：ASCII 词做边界匹配，中文词直接包含。"""
    if not keyword:
        return False
    if keyword.isascii():
        return re.search(_ASCII_KW_RE.format(re.escape(keyword)), text, re.IGNORECASE) is not None
    return keyword in text


def _keywords_of(text: str) -> list[str]:
    """从一条要求文本中提取词表命中的关键词（去重、限 6 个）。"""
    found: list[str] = []
    for word in _KEYWORD_VOCAB:
        if word not in found and _keyword_hit(text, word):
            found.append(word)
    return found[:6]


# ---------------------------------------------------------------------------
# JD 拆解
# ---------------------------------------------------------------------------

def _split_clauses(line: str) -> list[str]:
    clauses: list[str] = []
    for sentence in _SENTENCE_RE.split(line):
        clauses.extend(_COMMA_RE.split(sentence))
    return clauses


def split_requirements(jd_text: str) -> list[dict]:
    """确定性拆解：返回 [{priority, text, keywords}]（纯函数，便于测试）。"""
    items: list[dict] = []
    for raw_line in jd_text.split("\n"):
        line = _BULLET_RE.sub("", raw_line.strip())
        if not line:
            continue
        if any(hint in line for hint in _NARRATIVE_HINTS):
            continue
        for clause in _split_clauses(line):
            # 只剪首尾的空白/装饰符/冒号；括号保留（如"（TensorFlow 或 PyTorch）"是内容）
            clause = clause.strip(" \t-—*·•●○:：")
            if len(clause) < _MIN_CLAUSE_LEN or clause in _TITLE_WORDS:
                continue
            priority = "preferred" if _PREFERRED_RE.search(clause) else "must"
            items.append(
                {"priority": priority, "text": clause, "keywords": _keywords_of(clause)}
            )
    return items


def build_requirements_response(messages: Sequence[dict]) -> str:
    """mock provider：拆解 JD 要求清单（与真实 LLM 输出同结构）。"""
    jd_text = prompts.extract_jd(messages)
    return json.dumps({"requirements": split_requirements(jd_text)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 举证候选
# ---------------------------------------------------------------------------

_FACT_LINE_RE = re.compile(
    r"^- \[(?P<fid>[0-9a-fA-F]+)\] section=(?P<section>\S+) \| (?P<quote>.+)$"
)


def _pick_best_fact(keywords: list[str], lines: list[str]) -> tuple[str, str] | None:
    """按关键词重叠数确定性取 top1；同分保留行序最前（稳定，不依赖随机 id）。

    零命中返回 None（保守：不硬配）。
    """
    best: tuple[str, str] | None = None
    best_hits = 0
    for line in lines:
        match = _FACT_LINE_RE.match(line)
        if match is None:
            continue
        hits = sum(1 for kw in keywords if _keyword_hit(line, kw))
        if hits > best_hits:
            best = (match.group("fid"), match.group("quote").strip())
            best_hits = hits
    return best


def build_evidence_response(messages: Sequence[dict]) -> str:
    """mock provider：为单条要求给出举证候选（quote 逐字取自条目原文）。"""
    req_index, _text, keywords = prompts.extract_requirement(messages)
    lines = prompts.extract_fact_lines(messages)
    picked = _pick_best_fact(keywords, lines)
    if picked is not None:
        fid, quote = picked
        payload = {
            "req_index": req_index,
            "quote": quote,
            "fact_id": fid,
            "nearest_fact_id": None,
        }
    else:
        payload = {
            "req_index": req_index,
            "quote": None,
            "fact_id": None,
            "nearest_fact_id": None,
        }
    return json.dumps(payload, ensure_ascii=False)

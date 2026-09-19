"""mock 模式的规则抽取器（无 key 时全链路可跑通，ard/0007）。

- 确定性规则：quote 全部逐字来自 canonical_text，span 回验必过；
- 优先级：fixtures 固定结果（``tests/fixtures/mock_extraction.json``，
  能对当前文本全部回验通过时使用）→ 否则现场规则抽取；
- 质量声明：规则抽取对中文简历的解析质量**不代表真实水平**，
  界面与响应头必须标示 mock（演示不得冒充，ard/0007）。

日期归一化规则（与 prompts.py 的格式约定一致）：

- 两位年份按 2000–2049 解释（求职简历场景）；
- ``YYYY-MM 至 YYYY-MM`` / ``YYYY-MM 至今`` / 单点 ``YYYY-MM``；
- 月份非法 → ``normalized=None`` + ``ambiguous=True`` + candidates。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Sequence

from . import prompts

_FIXTURES_FILE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "mock_extraction.json"

# ---------------------------------------------------------------------------
# 章节识别
# ---------------------------------------------------------------------------

_SECTION_TITLES: list[tuple[str, tuple[str, ...]]] = [
    ("work", ("工作经历", "工作经验", "实习经历", "实习经验", "职业经历", "工作履历")),
    ("project", ("项目经历", "项目经验")),
    ("education", ("教育背景", "教育经历", "教育信息")),
    ("skill", ("专业技能", "技能清单", "技能特长", "技术栈", "技能")),
    ("summary", ("个人简介", "自我介绍", "自我评价", "个人总结", "个人概述", "简介", "概述")),
    ("other", ("获奖经历", "荣誉奖项", "获奖情况", "获奖", "荣誉", "证书", "资格证书", "兴趣爱好", "其他")),
]

_MAX_TITLE_LEN = 14


def _match_section_title(line: str) -> str | None:
    """识别 section 标题行（如 "二、工作经历"、"技能"）。"""
    stripped = line.strip().strip("：:").strip()
    if not stripped or len(stripped) > _MAX_TITLE_LEN:
        return None
    for section, keywords in _SECTION_TITLES:
        for kw in keywords:
            if stripped == kw:
                return section
    # 允许带编号/装饰前缀，如 "一、工作经历"、"1. 项目经验"
    for section, keywords in _SECTION_TITLES:
        for kw in keywords:
            if stripped.endswith(kw) and len(stripped) - len(kw) <= 4:
                return section
    return None


# ---------------------------------------------------------------------------
# 日期提取与归一化
# ---------------------------------------------------------------------------

_DASH = r"[-‑–—~～至到]"
_RANGE_MONTH_RE = re.compile(
    rf"(?P<y1>\d{{2,4}})\s*[./年]\s*(?P<m1>\d{{1,2}})\s*月?"
    rf"\s*(?:{_DASH}|--+)\s*"
    rf"(?:(?P<y2>\d{{2,4}})\s*[./年]\s*(?P<m2>\d{{1,2}})\s*月?|(?P<ongoing>至今|现在|今|present|now))",
    re.IGNORECASE,
)
_RANGE_YEAR_RE = re.compile(rf"(?P<y1>\d{{4}})\s*(?:{_DASH}|--+)\s*(?P<y2>\d{{4}})(?!\s*[./年]\s*\d)")
_SINGLE_YM_RE = re.compile(r"(?P<y>\d{2,4})\s*[./年]\s*(?P<m>\d{1,2})\s*月?")


def _norm_year(raw: str) -> int:
    n = int(raw)
    return 2000 + n if len(raw) <= 2 else n


def _norm_ym(year_raw: str, month_raw: str) -> tuple[str | None, bool, list[str]]:
    """元年月归一化，返回 (normalized, ambiguous, candidates)。"""
    year = _norm_year(year_raw)
    month = int(month_raw)
    if 1 <= month <= 12:
        return f"{year}-{month:02d}", False, []
    return None, True, [f"{year} 年 {month} 月：月份非法"]


def extract_dates(text: str) -> tuple[list[dict], list[tuple[int, int]]]:
    """提取日期解释，返回 (date_interpretations, 已消费的 span 列表)。"""
    interpretations: list[dict] = []
    consumed: list[tuple[int, int]] = []

    for match in _RANGE_MONTH_RE.finditer(text):
        start_cp, start_ambiguous, candidates = _norm_ym(match.group("y1"), match.group("m1"))
        candidates = list(candidates)
        if match.group("ongoing"):
            end_cp, end_ambiguous = "至今", False
        else:
            end_cp, end_ambiguous, end_candidates = _norm_ym(match.group("y2"), match.group("m2"))
            candidates.extend(end_candidates)
        is_ambiguous = start_ambiguous or end_ambiguous
        normalized = None if is_ambiguous else f"{start_cp} 至 {end_cp}"
        interpretations.append(
            {
                "raw": match.group(0),
                "normalized": normalized,
                "ambiguous": is_ambiguous,
                "candidates": candidates,
            }
        )
        consumed.append(match.span())

    covered = [(s, e) for s, e in consumed]

    def _overlaps(span: tuple[int, int]) -> bool:
        return any(s < span[1] and span[0] < e for s, e in covered)

    for match in _RANGE_YEAR_RE.finditer(text):
        if _overlaps(match.span()):
            continue
        y1, y2 = _norm_year(match.group("y1")), _norm_year(match.group("y2"))
        interpretations.append(
            {
                "raw": match.group(0),
                "normalized": f"{y1} 至 {y2}",
                "ambiguous": False,
                "candidates": [],
            }
        )
        consumed.append(match.span())
        covered.append(match.span())

    for match in _SINGLE_YM_RE.finditer(text):
        if _overlaps(match.span()):
            continue
        normalized, ambiguous, candidates = _norm_ym(match.group("y"), match.group("m"))
        interpretations.append(
            {
                "raw": match.group(0),
                "normalized": normalized,
                "ambiguous": ambiguous,
                "candidates": candidates,
            }
        )
        consumed.append(match.span())
        covered.append(match.span())

    interpretations.sort(key=lambda item: item["raw"])
    # 按出现顺序返回 consumed（用于从正文中剔除日期串）
    consumed.sort(key=lambda span: span[0])
    return interpretations, consumed


# ---------------------------------------------------------------------------
# 结构化字段与实体
# ---------------------------------------------------------------------------

_SPLIT_RE = re.compile(r"[|｜\t]|\s{2,}")


def _parse_fields(section: str, body: str) -> dict:
    """从去掉日期后的正文解析 section 结构化字段（保守、确定性）。"""
    fields: dict = {}
    body = body.strip(" -—|")
    if not body:
        return fields

    if section == "work":
        parts = [p.strip(" -—|") for p in _SPLIT_RE.split(body)]
        parts = [p for p in parts if p]
        if parts:
            fields["organization"] = parts[0]
        if len(parts) > 1:
            fields["title"] = parts[1]
        if len(parts) > 2:
            fields["description"] = " ".join(parts[2:])
    elif section == "project":
        parts = [p.strip(" -—|") for p in _SPLIT_RE.split(body)]
        parts = [p for p in parts if p]
        if parts:
            fields["name"] = parts[0]
        if len(parts) > 1:
            fields["role"] = parts[1]
        if len(parts) > 2:
            fields["description"] = " ".join(parts[2:])
    elif section == "education":
        parts = [p.strip(" -—|") for p in _SPLIT_RE.split(body)]
        parts = [p for p in parts if p]
        if parts:
            fields["school"] = parts[0]
        if len(parts) > 1:
            fields["degree"] = parts[1]
    elif section == "skill":
        items = [p.strip() for p in re.split(r"[、,，;；/]", body) if p.strip()]
        if items:
            fields["items"] = items
    else:
        fields["text"] = body
    return fields


# 技能词表（规则抽取用；命中即入 entities.skills）
_SKILL_VOCAB: tuple[str, ...] = (
    "Python", "Java", "JavaScript", "TypeScript", "Go", "C++", "C#", "SQL",
    "MySQL", "PostgreSQL", "Redis", "MongoDB", "Elasticsearch", "Linux",
    "Docker", "Kubernetes", "K8s", "Git", "Spark", "Hadoop", "Flink", "Kafka",
    "Hive", "TensorFlow", "PyTorch", "React", "Vue", "Node.js", "Spring",
    "Django", "FastAPI", "Flask", "Nginx",
    "机器学习", "深度学习", "推荐系统", "自然语言处理", "NLP", "计算机视觉",
    "数据分析", "数据挖掘", "分布式系统", "微服务", "高并发", "搜索引擎优化", "SEO",
    "项目管理", "用户增长", "产品设计", "算法", "数据结构",
)


def _extract_skills(text: str) -> list[str]:
    found: list[str] = []
    for skill in _SKILL_VOCAB:
        pattern = rf"(?i)(?<![a-z0-9+#]){re.escape(skill)}(?![a-z0-9+#])"
        if re.search(pattern, text) and skill not in found:
            found.append(skill)
    return found


_SCALE = {"万": 10_000, "亿": 100_000_000}
_NUMBER_RE = re.compile(
    r"(?P<num>\d+(?:\.\d+)?)\s*(?P<scale>[万亿])?\s*"
    r"(?P<unit>%|DAU|MAU|GMV|PV|UV|ROI|人|次|个|家|名|元|美元|美金|台|件|小时|分钟|天|个月|万|亿)?",
    re.IGNORECASE,
)


def _extract_numbers(text: str) -> list[dict]:
    """数字 → {value, unit}（"70万" → 700000；"30%" → 30, unit=%）。"""
    numbers: list[dict] = []
    for match in _NUMBER_RE.finditer(text):
        raw_num = float(match.group("num"))
        scale = match.group("scale")
        unit = match.group("unit") or ""
        value = raw_num * _SCALE.get(scale, 1)
        if not unit and scale:
            unit = scale
        if not unit and raw_num != int(raw_num):
            continue  # 无单位的小数（如版本号）不入实体
        numbers.append({"value": value, "unit": unit})
    return numbers


def _extract_orgs(fields: dict, text: str) -> list[str]:
    orgs: list[str] = []
    for key in ("organization", "school", "name"):
        value = fields.get(key)
        if value:
            orgs.append(str(value))
    if not orgs:
        for match in re.finditer(r"[\u4e00-\u9fa5]{2,12}(?:公司|集团|科技|银行|大学|学院|研究院)", text):
            orgs.append(match.group(0))
    return orgs


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

_MIN_QUOTE_LEN = 4


def extract_facts(canonical: str) -> list[dict]:
    """规则抽取事实条目（quote 逐字取自 canonical，回验必过）。"""
    facts: list[dict] = []
    current_section = "other"

    for line in canonical.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        title = _match_section_title(stripped)
        if title:
            current_section = title
            continue
        if len(stripped) < _MIN_QUOTE_LEN:
            continue

        dates, consumed = extract_dates(stripped)
        body = stripped
        for start, end in reversed(consumed):
            body = body[:start] + " " + body[end:]
        fields = _parse_fields(current_section, body)
        if dates:
            fields["period"] = dates[0]["raw"]
        entities = {
            "numbers": _extract_numbers(stripped),
            "orgs": _extract_orgs(fields, stripped),
            "skills": _extract_skills(stripped),
        }
        facts.append(
            {
                "section": current_section,
                "quote": stripped,
                "fields": fields,
                "date_interpretations": dates,
                "attribution": "unknown",  # 保守默认（ard/0002）
                "entities": entities,
            }
        )
    return facts


def _load_fixture_facts() -> list[dict] | None:
    """读取 fixtures 固定抽取结果（存在且可加载时）。"""
    try:
        data = json.loads(_FIXTURES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    facts = data.get("facts")
    return facts if isinstance(facts, list) and facts else None


def build_mock_response(messages: Sequence[dict]) -> str:
    """mock provider 入口：返回固定结果（可回验时）或现场规则抽取。"""
    canonical = prompts.extract_original(messages)
    fixture_facts = _load_fixture_facts()
    if fixture_facts is not None:
        from ..shared.normalize import find_quote  # 局部导入，避免顶层循环

        if all(find_quote(canonical, str(f.get("quote", ""))) for f in fixture_facts):
            return json.dumps({"facts": fixture_facts}, ensure_ascii=False)
    return json.dumps({"facts": extract_facts(canonical)}, ensure_ascii=False)

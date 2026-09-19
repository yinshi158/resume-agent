"""ATS 模板渲染（spec §18.1：单栏、标准章节名、无表格文本框；Playwright 出 PDF）。

导出内容 = rewrite 下 confirmed + edited 句（rejected 剔除），按 section
分组（组内保持稿内顺序）；诊断关键词以中英文并写形式入 skill 区
（ATS 规则 v1 只收确定项）。
"""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path

SECTION_TITLES = {
    "summary": "个人简介",
    "work": "工作经历",
    "project": "项目经历",
    "education": "教育背景",
    "skill": "专业技能",
    "other": "其他",
}

#: 简历章节的标准顺序（summary 综述 → 经历 → 教育/技能）
SECTION_ORDER = ["summary", "work", "project", "education", "skill", "other"]

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z]")


def _split_keywords(keywords: list[str]) -> tuple[list[str], list[str]]:
    cjk: list[str] = []
    ascii_words: list[str] = []
    for keyword in keywords:
        keyword = str(keyword).strip()
        if not keyword:
            continue
        if _CJK_RE.search(keyword):
            cjk.append(keyword)
        elif _ASCII_WORD_RE.search(keyword):
            ascii_words.append(keyword)
    return cjk, ascii_words


def bilingual_keywords(requirements: list[dict]) -> list[str]:
    """诊断关键词 → 中英文并写条目（如"推荐系统（recommender）"）。

    每条要求的关键词里同时存在中文与英文别名时并写；只有单侧时原样收录。
    去重、保持要求顺序（完全确定性）。
    """
    entries: list[str] = []
    seen: set[str] = set()

    def add(entry: str) -> None:
        if entry and entry not in seen:
            seen.add(entry)
            entries.append(entry)

    for requirement in requirements:
        cjk, ascii_words = _split_keywords(requirement.get("keywords") or [])
        if cjk and ascii_words:
            add(f"{cjk[0]}（{'/'.join(ascii_words[:2])}）")
        elif cjk:
            add(cjk[0])
        elif ascii_words:
            add(ascii_words[0])
    return entries


def build_export_sentences(rewrite_payload: dict) -> list[dict]:
    """gate2 处置结果 → 导出行集（rejected 剔除；按 section 分组排序）。"""
    sentences = [
        sentence
        for sentence in rewrite_payload["sentences"]
        if sentence["gate_status"] in ("confirmed", "edited")
    ]
    sentences.sort(key=lambda item: (SECTION_ORDER.index(item["section"]), item["seq"]))

    out = [
        {"id": sentence["id"], "section": sentence["section"], "text": sentence["text"]}
        for sentence in sentences
    ]

    keywords = bilingual_keywords(rewrite_payload.get("requirements") or [])
    if keywords:
        out.append(
            {
                "id": "keywords",
                "section": "skill",
                "text": "核心关键词：" + "、".join(keywords),
            }
        )
        out.sort(key=lambda item: (SECTION_ORDER.index(item["section"]), item["id"] != "keywords"))
    return out


def build_html(sentences: list[dict], *, title: str = "简历") -> str:
    """ATS 模板 HTML（单栏、无表格文本框、标准章节名）。"""
    grouped: dict[str, list[str]] = {section: [] for section in SECTION_ORDER}
    for sentence in sentences:
        grouped.setdefault(sentence["section"], []).append(sentence["text"])

    blocks: list[str] = []
    for section in SECTION_ORDER:
        texts = grouped.get(section) or []
        if not texts:
            continue
        items = "".join(f"<p>{html_lib.escape(text)}</p>" for text in texts)
        blocks.append(
            f'<section><h2>{SECTION_TITLES[section]}</h2>{items}</section>'
        )
    body = "\n".join(blocks)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{html_lib.escape(title)}</title>
<style>
  /* ATS 友好：单栏、无表格、无浮动；字体用系统常规字体 */
  @page {{ margin: 16mm 14mm; }}
  body {{ font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
         font-size: 10.5pt; line-height: 1.6; color: #111; margin: 0; }}
  h2 {{ font-size: 12.5pt; margin: 14px 0 6px; padding-bottom: 2px;
        border-bottom: 1px solid #999; }}
  p {{ margin: 3px 0; word-break: break-word; }}
  section {{ margin-bottom: 6px; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_pdf(html: str, out_path: Path) -> None:
    """Playwright（Chromium）渲染 PDF。

    Playwright 为默认依赖（R4 已决）；浏览器二进制缺失时给出安装指引。
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - 依赖缺失路径由可用性检查前置拦截
        raise RuntimeError(
            "未安装 Playwright（R4：导出唯一渲染路径）。请执行："
            "pip install playwright && python -m playwright install chromium"
        ) from exc

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                page.set_content(html, wait_until="load")
                page.pdf(
                    path=str(out_path),
                    format="A4",
                    print_background=True,
                )
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 —— 统一转业务可读错误
        raise RuntimeError(
            f"PDF 渲染失败：{exc}（若为浏览器缺失，请执行：python -m playwright install chromium）"
        ) from exc

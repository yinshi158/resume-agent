"""生成二进制 fixtures（PDF/DOCX）与 mock 固定抽取结果。

开发脚本，手动运行一次（产物落在 fixtures/ 下，供测试与验收使用）：

    cd backend
    python tests/fixtures/make_fixtures.py

产物：

- ``files/single_column.pdf``：单栏 PDF（01 文本）
- ``files/two_column.pdf``：双栏 PDF（02 文本按奇偶行分列，模拟阅读序交错）
- ``files/with_table.pdf``：带表格 PDF（03 文本 + 真实表格线）
- ``files/sample.docx``：DOCX（01 文本）
- ``mock_extraction.json``：01 文本的规则抽取固定结果（mock 模式优先返回）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent
TEXTS_DIR = FIXTURES_DIR / "resumes_text"
FILES_DIR = FIXTURES_DIR / "files"

# backend/ 加入 sys.path，便于导入 app 包
sys.path.insert(0, str(FIXTURES_DIR.parents[1]))

_CJK_FONT = "china-s"  # PyMuPDF 内置简体中文字体


def _read(name: str) -> str:
    return (TEXTS_DIR / name).read_text(encoding="utf-8")


def make_single_column_pdf() -> None:
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 545, 800), _read("01_single_column.txt"),
        fontname=_CJK_FONT, fontsize=10.5,
    )
    doc.save(FILES_DIR / "single_column.pdf")
    doc.close()


def make_two_column_pdf() -> None:
    """双栏：文本按奇偶行分列，提取阅读序必然交错（验收"乱序有逃生门"）。"""
    import pymupdf as fitz

    lines = _read("02_two_column.txt").splitlines()
    left = "\n".join(lines[0::2])
    right = "\n".join(lines[1::2])
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(40, 50, 290, 800), left, fontname=_CJK_FONT, fontsize=10)
    page.insert_textbox(fitz.Rect(310, 50, 555, 800), right, fontname=_CJK_FONT, fontsize=10)
    doc.save(FILES_DIR / "two_column.pdf")
    doc.close()


def make_with_table_pdf() -> None:
    import pymupdf as fitz

    lines = _read("03_with_table.txt").splitlines()
    header = "\n".join(lines[:6])          # 姓名 / 联系方式 / 个人信息两行
    body = "\n".join(lines[6:])

    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 40, 545, 180), header, fontname=_CJK_FONT, fontsize=10.5)

    # 真实表格：4 列 × 3 行（表头 + 2 行数据）
    xs = [50, 150, 250, 350, 545]
    top, row_h = 190, 24
    rows = [
        ["姓名", "王五", "学历", "本科"],
        ["城市", "北京", "电话", "138-0000-2222"],
    ]
    for r in range(len(rows) + 1):
        y = top + r * row_h
        page.draw_line(fitz.Point(xs[0], y), fitz.Point(xs[-1], y))
    for x in xs:
        page.draw_line(fitz.Point(x, top), fitz.Point(x, top + len(rows) * row_h))
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_textbox(
                fitz.Rect(xs[c] + 4, top + r * row_h + 4, xs[c + 1] - 4, top + (r + 1) * row_h - 2),
                cell, fontname=_CJK_FONT, fontsize=10,
            )

    page.insert_textbox(
        fitz.Rect(50, top + (len(rows) + 1) * row_h + 20, 545, 800),
        body, fontname=_CJK_FONT, fontsize=10.5,
    )
    doc.save(FILES_DIR / "with_table.pdf")
    doc.close()


def make_quantified_pdf() -> None:
    """04 文本 PDF：异象规则验收用（日期颠倒/重叠、百分比、金额量级）。"""
    import pymupdf as fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(
        fitz.Rect(50, 50, 545, 800), _read("04_quantified.txt"),
        fontname=_CJK_FONT, fontsize=10.5,
    )
    doc.save(FILES_DIR / "quantified.pdf")
    doc.close()


def make_docx() -> None:
    from docx import Document

    document = Document()
    for line in _read("01_single_column.txt").splitlines():
        document.add_paragraph(line)
    document.save(FILES_DIR / "sample.docx")


def make_mock_extraction() -> None:
    """固化 01 文本的规则抽取结果（mock 模式优先返回，spec §4）。"""
    from app.ingest import mock_rules, sanitize
    from app.shared.normalize import normalize

    cleaned, _ = sanitize.clean(_read("01_single_column.txt"))
    canonical = normalize(cleaned)
    facts = mock_rules.extract_facts(canonical)
    payload = {
        "source": "01_single_column.txt",
        "note": "mock 模式固定抽取结果（规则抽取产物）；质量不代表真实水平",
        "facts": facts,
    }
    (FIXTURES_DIR / "mock_extraction.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    make_single_column_pdf()
    make_two_column_pdf()
    make_with_table_pdf()
    make_quantified_pdf()
    make_docx()
    make_mock_extraction()
    print("fixtures 生成完成：")
    for path in sorted(FILES_DIR.iterdir()):
        print(f"  files/{path.name} ({path.stat().st_size} bytes)")
    print("  mock_extraction.json")


if __name__ == "__main__":
    main()

"""PDF/DOCX 读取、文件存储（spec §4）。

提取策略：

- ``.pdf``：PyMuPDF 排序模式（阅读序以抽取器为准，ard/0001）提取纯文本，
  提取时过滤与背景同色（近白）的不可见文本层；``pymupdf4llm`` 另产
  Markdown 结构视图（仅喂 LLM，不承载锚点）。
- ``.docx``：``markitdown`` 转 Markdown；去除 Markdown 语法符号后的纯文本
  作为 canonical_text 来源。
- ``mineru``：重解析逃生门（可选依赖，未安装时由 API 层置灰提示）。

canonical_text 的锚定来源（纯文本）与 LLM 输入视图（Markdown）在此分离。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..platform import db

# 近白阈值：白底白字是最常见的 PDF 隐形注入手法
_WHITE_THRESHOLD = 245


@dataclass
class ExtractResult:
    """一次文本提取的结果。"""

    text: str                # canonical_text 来源（纯文本）
    markdown_view: str       # LLM 输入视图（Markdown，仅辅助理解结构）
    parser: str              # 'pymupdf4llm' | 'markitdown' | 'mineru'
    invisible_filtered: int = 0  # 被过滤的不可见文本层片段数


def save_upload(resume_id: str, filename: str, data: bytes) -> Path:
    """保存上传原件到 ``data/uploads/``，返回磁盘路径。"""
    safe_name = _safe_filename(filename)
    directory = db.upload_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{resume_id}_{safe_name}"
    path.write_bytes(data)
    return path


def _safe_filename(filename: str) -> str:
    """取文件名部分并做保守净化（防路径穿越）。"""
    name = Path(filename or "upload").name
    name = re.sub(r"[\\/:*?\"<>|\r\n\t]", "_", name).strip() or "upload"
    return name[:120]


def extract(path: Path, *, parser: str | None = None) -> ExtractResult:
    """按扩展名/指定 parser 提取文本。"""
    if parser == "mineru":
        markdown = extract_with_mineru(path)
        return ExtractResult(text=strip_markdown_markup(markdown), markdown_view=markdown, parser="mineru")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in (".docx", ".doc"):
        return _extract_docx(path)
    raise ValueError("仅支持 PDF / DOCX 格式的简历文件")


def _extract_pdf(path: Path) -> ExtractResult:
    """PyMuPDF 排序模式提取纯文本（过滤近白不可见文本），pymupdf4llm 产结构视图。"""
    import pymupdf as fitz  # 新版包名（fitz 为兼容别名）

    lines: list[str] = []
    invisible = 0
    doc = fitz.open(path)
    try:
        for page in doc:
            page_dict = page.get_text("dict", sort=True)
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # 只处理文本块
                    continue
                for line in block.get("lines", []):
                    parts: list[str] = []
                    for span in line.get("spans", []):
                        if _is_invisible_span(span):
                            invisible += 1
                            continue
                        parts.append(span.get("text", ""))
                    lines.append("".join(parts))
                lines.append("")  # 块（段落）之间留一个空行
    finally:
        doc.close()

    text = "\n".join(lines)
    markdown = ""
    try:
        import pymupdf4llm

        markdown = pymupdf4llm.to_markdown(str(path))
    except Exception:  # noqa: BLE001 —— 结构视图仅辅助，失败退化纯文本
        markdown = ""
    return ExtractResult(
        text=text,
        markdown_view=markdown or text,
        parser="pymupdf4llm",
        invisible_filtered=invisible,
    )


def _is_invisible_span(span: dict) -> bool:
    """判定近白（与白色背景同色）的不可见文本层片段。"""
    text = span.get("text", "")
    if not text.strip():
        return False
    color = int(span.get("color", 0))
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    return r >= _WHITE_THRESHOLD and g >= _WHITE_THRESHOLD and b >= _WHITE_THRESHOLD


def _extract_docx(path: Path) -> ExtractResult:
    """markitdown 转 Markdown；去符号纯文本作为 canonical 来源。"""
    from markitdown import MarkItDown

    converter = MarkItDown()
    result = converter.convert(str(path))
    markdown = getattr(result, "text_content", "") or ""
    if not markdown.strip():
        raise ValueError("DOCX 未提取到文本内容")
    return ExtractResult(
        text=strip_markdown_markup(markdown),
        markdown_view=markdown,
        parser="markitdown",
    )


_TABLE_SEP_RE = re.compile(r"^\s*\|(?:\s*:?-{2,}:?\s*\|)+\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_BOLD_RE = re.compile(r"\*{2,}")
_CODE_TICK_RE = re.compile(r"`+")


def strip_markdown_markup(markdown: str) -> str:
    """去除 Markdown 语法符号，得到确定性纯文本（canonical 来源）。

    只处理确定的装饰性符号：标题 ``#``、加粗 ``**``、行内代码反引号、
    表格分隔行；其余内容原样保留（含 ``|`` 表格内容与列表符号）。
    """
    out: list[str] = []
    for line in markdown.splitlines():
        if _TABLE_SEP_RE.match(line):
            continue
        line = _HEADING_RE.sub("", line)
        line = _BOLD_RE.sub("", line)
        line = _CODE_TICK_RE.sub("", line)
        out.append(line)
    return "\n".join(out)


def extract_with_mineru(path: Path) -> str:
    """调用 MinerU（可选依赖）重解析 PDF，返回 Markdown。

    未安装时抛 ``RuntimeError``（API 层提示用户）。
    """
    exe = shutil.which("mineru") or shutil.which("magic-pdf")
    if not exe:
        raise RuntimeError("MinerU 未安装：重解析逃生门需要先安装 MinerU（M1 可选依赖）")
    outdir = tempfile.mkdtemp(prefix="mineru_")
    try:
        proc = subprocess.run(
            [exe, "-p", str(path), "-o", outdir],
            capture_output=True,
            text=True,
            # Windows 默认按本地代码页（GBK）解码子进程输出，MinerU 输出
            # 为 UTF-8，不指定会在读取线程抛 UnicodeDecodeError（审查 P3）
            encoding="utf-8",
            errors="replace",
            timeout=1800,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[-500:]
            raise RuntimeError(f"MinerU 解析失败（退出码 {proc.returncode}）：{detail}")
        mds = sorted(Path(outdir).rglob("*.md"))
        if not mds:
            raise RuntimeError("MinerU 未产出 Markdown 文件")
        return mds[0].read_text(encoding="utf-8")
    finally:
        shutil.rmtree(outdir, ignore_errors=True)


def new_resume_id() -> str:
    return uuid.uuid4().hex

"""解析流水线编排（spec §4）。

```
上传文件 → io 保存原文件 → 文本提取(pymupdf4llm/markitdown)
→ sanitize 文本层清洗 → normalize → canonical_text 入库
→ LLM 抽取（或 mock 规则抽取）→ 程序回验 quote
→ anomaly 规则计算 → facts + anomaly_flags 入库 → 状态 reviewing
```

M1 同步处理（单次 ≤1 分钟）；API 返回任务式结构，为 M2 的 SSE 预留
演进空间。任一环节失败 → 回滚（不留半成品行）并删除已保存文件。
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..fact_store import core as fact_store_core
from ..fact_store import io as fact_store_io
from ..platform import db
from ..shared.normalize import NORMALIZE_VERSION, normalize
from . import extract, io, sanitize

logger = logging.getLogger(__name__)


def process_upload(filename: str, data: bytes, *, parser: str | None = None) -> tuple[str, bool, list[str]]:
    """完整解析流水线。

    :returns: ``(resume_id, is_mock, warnings)``；``is_mock=True`` 表示
        本次抽取来自 mock 规则（界面须标示，ard/0007）。
    :raises ValueError: 输入不可解析（格式不支持、空文本等）
    :raises RuntimeError: LLM/解析器调用失败
    """
    resume_id = io.new_resume_id()
    stored = io.save_upload(resume_id, filename, data)
    try:
        extracted = io.extract(stored, parser=parser)

        cleaned, warnings = sanitize.clean(extracted.text)
        if extracted.invisible_filtered:
            warnings.append(
                f"已过滤 {extracted.invisible_filtered} 处与背景同色的不可见文本层（防注入）"
            )

        canonical = normalize(cleaned)
        if not canonical.strip():
            raise ValueError("未能从文件中提取到有效文本（可能是扫描件，M1 暂不支持 OCR）")

        facts, is_mock, extract_warnings = extract.run_extraction(canonical, extracted.markdown_view)
        warnings.extend(extract_warnings)
        if not facts:
            warnings.append("未抽取到任何事实条目，可在校对页手动核对或切换 MinerU 重解析")

        conn = db.connect()
        try:
            with conn:
                # 状态语义：解析+抽取完成即进入人工校对（reviewing）；
                # 'parsed' 中间态在 M2 SSE 异步化时启用
                fact_store_io.insert_resume(
                    conn,
                    resume_id=resume_id,
                    filename=filename,
                    stored_path=str(stored),
                    # mock 溯源按行记录（审查 P3）：mock 抽取的简历此后无论
                    # 全局模式如何切换，都应如实标示为 mock 解析产物
                    parser="mock" if is_mock else extracted.parser,
                    normalize_version=NORMALIZE_VERSION,
                    status="reviewing",
                )
                fact_store_io.insert_canonical(conn, resume_id, canonical)
                fact_store_core.store_extraction(
                    conn, resume_id, canonical, [fact.model_dump() for fact in facts]
                )
        finally:
            conn.close()
        return resume_id, is_mock, warnings
    except Exception:
        # 失败回滚：不留半成品数据行与孤儿文件
        _cleanup(resume_id, stored)
        raise


def reparse(resume_id: str, *, parser: str) -> str:
    """重解析逃生门：用原上传文件按指定 parser 重新解析。

    产生**新 resume 行**（不覆盖旧行，spec §2）；旧行及其事实源保持不变，
    便于对照与回退。
    """
    conn = db.connect()
    try:
        row = fact_store_io.fetch_resume(conn, resume_id)
    finally:
        conn.close()
    if row is None:
        raise KeyError(resume_id)

    stored = Path(row["stored_path"])
    if not stored.exists():
        raise ValueError("原上传文件已不存在，无法重解析")
    return process_upload(row["filename"], stored.read_bytes(), parser=parser)[0]


def _cleanup(resume_id: str, stored) -> None:
    """删除失败残留（数据行 + 文件）。"""
    try:
        conn = db.connect()
        try:
            with conn:
                fact_ids = [
                    r["id"]
                    for r in conn.execute("SELECT id FROM facts WHERE resume_id = ?", (resume_id,))
                ]
                for fact_id in fact_ids:
                    conn.execute("DELETE FROM anomaly_flags WHERE fact_id = ?", (fact_id,))
                conn.execute("DELETE FROM facts WHERE resume_id = ?", (resume_id,))
                conn.execute("DELETE FROM canonical_texts WHERE resume_id = ?", (resume_id,))
                conn.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 —— 清理失败仅记日志，不掩盖原始错误
        logger.exception("失败清理未完成：resume_id=%s", resume_id)
    try:
        io.Path(stored).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception("失败文件未删除：%s", stored)

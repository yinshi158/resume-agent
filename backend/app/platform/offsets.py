"""字符偏移语义转换（API 边界唯一转换点）。

约定：

- 后端内部（DB 存储、anomaly 计算、回验）统一使用 Python 字符偏移
  （Unicode 码点语义，``str`` 下标）；
- HTTP API 对外输出的 ``span_start`` / ``span_end`` 统一转换为 UTF-16
  码元偏移——前端 JS 字符串下标即 UTF-16 码元，可对 canonical_text
  直接 ``slice`` 高亮，无需任何坐标换算（spec §8）。

注意：中文基本区字符（含常见汉字）在两种语义下偏移一致；差异只出现在
补充平面字符（emoji、罕用汉字等，1 个码点 = 2 个 UTF-16 码元）。
"""

from __future__ import annotations


def cp_to_utf16(text: str, offset: int) -> int:
    """把码点偏移转为 UTF-16 码元偏移；负数（-1 = 回验失败）原样返回。"""
    if offset < 0:
        return -1
    if offset > len(text):
        offset = len(text)
    return len(text[:offset].encode("utf-16-le")) // 2


def utf16_to_cp(text: str, offset: int) -> int:
    """把 UTF-16 码元偏移转为码点偏移（入站请求用，M1 暂无使用方）。"""
    if offset < 0:
        return -1
    if offset > len(text) * 2:
        return len(text)
    # 累加方式转换，避免对整段文本反复编码
    cp = 0
    u16 = 0
    for ch in text:
        if u16 >= offset:
            break
        u16 += len(ch.encode("utf-16-le")) // 2
        cp += 1
    return cp

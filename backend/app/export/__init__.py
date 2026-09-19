"""export：ATS 单模板 PDF 导出（spec §18）。

v1 只做 ATS 单模板（单栏、标准章节名、无表格文本框、倒序时间、
关键词中英文并写）；渲染唯一路径 = Playwright（R4 已决）。
v2 登记：网申版 / 直聘版双模板（不做，仅登记）。
"""

from . import api, ats, io, render

__all__ = ["api", "ats", "io", "render"]

"""ledger：版本/投递/结果三表（spec §19，ard/0006：只记录不分析）。

v1 不做任何分析视图；表结构先行——错过就从第一天开始丢数据。
"""

from . import api, io

__all__ = ["api", "io"]

"""verb_lexicon：归因动词词表（持续维护资产，spec §16.3 / R3）。

主导级 / 参与级两级中文动词词表，版本化迭代（LEXICON_VERSION）：
- 主导级动词要求句子全部出处的 attribution == individual 且已 gate1 确认；
- 参与级动词不主张个人主导，任何归因可用；
- 词表命中以程序扫描为准（模型自报 verbs 字段仅参考）。
"""

from .loader import LEXICON_VERSION, dominant_verbs, participatory_verbs

__all__ = ["LEXICON_VERSION", "dominant_verbs", "participatory_verbs"]

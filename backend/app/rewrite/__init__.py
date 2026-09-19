"""rewrite：声明出处的改写（spec §15）。

消费 diagnoses/requirements（目标清单剔除硬性缺口，ard/0004 分母修正），
产出逐句带锚点的改写句集；编排四层校验（validate）与失败分流
（可修复 ≤2 轮重写；L2 不蕴含升级回报告）。

依赖方向：fact_store / diagnose → rewrite → validate（rewrite 调用
validate，ard/0008 单向）。
"""

from . import api, core, io, mock_rules, prompts, schemas

__all__ = ["api", "core", "io", "mock_rules", "prompts", "schemas"]

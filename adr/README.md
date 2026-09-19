# ARD（Architecture Decision Records）架构决策记录

本目录记录项目中**不可逆或逆转代价极高**的决策。每条决策一个文件。

规则：

- 决策一旦写入即为定稿，推翻它必须新建一份 ARD 写明取代关系，不删旧文件。
- 改代码前发现实现与某份 ARD 冲突，先停下来改 ARD（经评审），再改代码。
- 可逆的实现细节不进本目录，进 [docs/resume-agent-architecture.md](../docs/resume-agent-architecture.md)。

## 索引

| 编号                                              | 决策                                                   | 不可逆性               |
| ----------------------------------------------- | ---------------------------------------------------- | ------------------ |
| [0001](0001-canonical-text-anchor-layer.md)     | canonical_text 为唯一锚定层，只做机械归一化                        | 数据入库后偏移永久绑定        |
| [0002](0002-two-human-gates.md)                 | 双人工节点，事实源校对为第一咽喉                                     | 决定全链路数据流形态         |
| [0003](0003-evidence-based-validation.md)       | 校验用举证式（quote 程序验证），不用 embedding 打分                   | 决定 prompt 体系与测试集形态 |
| [0004](0004-failure-triage.md)                  | 校验失败分流，硬性缺口剔除分母，诊断/校验共享要求清单                          | 决定诊断产物 schema      |
| [0005](0005-layered-hallucination-detection.md) | 幻觉检测三层分工 + attribution 保守默认 + formula 白名单求值          | 决定事实源与改写输出 schema  |
| [0006](0006-event-logging.md)                   | gate_events（含判定快照）+ 版本/投递/结果三表，v1 只记录不分析             | 没记录的数据事后无法补        |
| [0007](0007-local-monolith-stack.md)            | 本地单体：FastAPI + React/Vite + SQLite + LiteLLM，mock 模式 | 重写成本高，牵连部署形态       |
| [0008](0008-code-structure.md)                  | 代码顶层按业务模块划分，模块内部按技术职责分层                              | 目录结构一旦长开难以搬移       |



# 0008. 代码顶层按业务模块划分，模块内部按技术职责分层

状态：已定稿（用户指令）
日期：2026-09-16

## 背景

用户明确指令：代码顶层按业务模块划分，模块内部再按技术职责分层。
对应架构文档模块表：ingest / fact_store / anomaly / gate1 / diagnose /
rewrite / validate / gate2 / export / ledger 等。

## 决定

1. 顶层包按**业务模块**命名与划分，与架构文档模块表一一对应，
   不按技术类型（controllers/ services/ models/）横切。
2. 模块**内部**再按技术职责分层：如 api（HTTP 接口）/ core（领域
   逻辑）/ io（存储、外部调用）/ schemas（pydantic 模型）。
3. 跨模块共享物只允许两类：normalize（0001 指定的唯一实现）与
   基础设施（配置、DB 连接、LiteLLM 客户端），放在 shared/ 或
   platform/ 下；业务规则不得下沉到 shared。
4. 模块间依赖单向：ingest → fact_store → gate1 → diagnose → rewrite
   → validate → gate2 → export，diagnose 产物是 rewrite/validate 的
   共享契约（见 0004）；禁止反向依赖。

## 后果

- 正面：业务边界即代码边界，新里程碑对应新模块目录，不搅动旧代码；
  架构文档模块表可直接当目录地图用。
- 负面：跨模块复用需要纪律，稍有不慎业务规则会泄漏到 shared。
- 不可逆性：目录结构一旦长开，import 散布全库，搬移成本高。

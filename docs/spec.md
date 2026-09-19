# 技术规格（Spec）

版本：v1.3（对应架构 v0.4 定稿 / PRD v1.0；M3 实现收口 2026-09-18）
日期：2026-09-16（M2 详设 2026-09-16 补写；M3 详设 2026-09-18 补写；
M3 风险项 R1–R6 拍板 2026-09-18，见 §24.1；M3 实现与验收 2026-09-18，
见文末 §25 与 `已知问题.md`）
颗粒度：**M1 写到可直接编码**；M2/M3 只写接口契约，开工前补详细设计
（均已补：M2 见 §12–13，M3 见 §14–25）。
关联：[PRD](prd.md)、[架构设计](resume-agent-architecture.md)、[ard/](../ard/README.md)

## 1. 工程结构（ard/0008）

```
111/
├── ard/  docs/  已知问题.md
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py                  # FastAPI 装配，只做路由挂载
│   │   ├── platform/                # 基础设施（非业务规则）
│   │   │   ├── config.py            # 设置读写（LLM 端点/key、模式）
│   │   │   ├── db.py                # SQLite 连接与迁移
│   │   │   └── llm.py               # LiteLLM 客户端 + mock 切换
│   │   ├── shared/
│   │   │   └── normalize.py         # 全系统唯一归一化实现（ard/0001）
│   │   ├── ingest/                  # 业务模块：解析入库
│   │   │   ├── api.py               # HTTP 接口
│   │   │   ├── core.py              # 解析流水线编排
│   │   │   ├── extract.py           # LLM 抽取 + span 回验
│   │   │   ├── sanitize.py          # 文本层清洗（防注入/隐形文本）
│   │   │   ├── io.py                # PDF/DOCX 读取、文件存储
│   │   │   └── schemas.py
│   │   ├── fact_store/              # 事实源存储与版本
│   │   │   ├── api.py  core.py  io.py  schemas.py
│   │   ├── anomaly/                 # 异象信号（纯函数）
│   │   │   ├── core.py  rules.py  schemas.py
│   │   └── gate1/                   # 校对动作的写回
│   │       ├── api.py  core.py  io.py  schemas.py
│   └── tests/
│       ├── test_normalize.py        # 属性测试（见 §3.3）
│       ├── test_anomaly.py
│       ├── test_ingest_span.py
│       └── fixtures/                # 中文简历样本（脱敏）
├── frontend/
│   ├── package.json  vite.config.ts
│   └── src/
│       ├── pages/  UploadPage.tsx  ReviewPage.tsx  SettingsPage.tsx
│       ├── components/
│       ├── api/client.ts
│       └── main.tsx  App.tsx
└── data/                            # 运行时生成：resume_agent.db、上传文件
```

依赖方向：`ingest → fact_store → anomaly → gate1`，单向；
`shared/normalize` 与 `platform/*` 可被任何模块用，反向禁止。

## 2. 数据模型（M1 部分，SQLite）

```sql
-- 一份简历的一次解析（重解析产生新行，不覆盖）
CREATE TABLE resumes (
  id            TEXT PRIMARY KEY,        -- uuid
  filename      TEXT NOT NULL,
  parser        TEXT NOT NULL,           -- 'pymupdf4llm' | 'markitdown' | 'mineru' | 'mock'
                                         -- mock = 该次解析的抽取来源为 mock（按行记录）；
                                         -- 接口的 mock 标记以此列为准，不返回全局设置
  normalize_version INTEGER NOT NULL,    -- 见 §3
  status        TEXT NOT NULL,           -- 'parsed' | 'reviewing' | 'confirmed'
  created_at    TEXT NOT NULL
);

-- 规范原文层：入库即不可变（ard/0001）
CREATE TABLE canonical_texts (
  resume_id     TEXT PRIMARY KEY REFERENCES resumes(id),
  content       TEXT NOT NULL            -- 机械归一化后的纯文本
);

-- 事实源条目
CREATE TABLE facts (
  id            TEXT PRIMARY KEY,
  resume_id     TEXT NOT NULL REFERENCES resumes(id),
  section       TEXT NOT NULL,           -- summary|work|project|education|skill|other
  raw_quote     TEXT NOT NULL,           -- canonical_text 逐字片段
  span_start    INTEGER NOT NULL,
  span_end      INTEGER NOT NULL,
  payload       TEXT NOT NULL,           -- JSON：结构化字段（含日期归一化结果、entities）
  attribution   TEXT NOT NULL DEFAULT 'unknown',  -- individual|team|mixed|unknown
  attribution_confirmed INTEGER NOT NULL DEFAULT 0,
  confirmed     INTEGER NOT NULL DEFAULT 0,       -- 人工是否已校对
  edited_payload TEXT,                            -- 人工修正后的 payload（不改原文层）
  created_at    TEXT NOT NULL
);

-- 异象标记（一次计算，随事实源版本失效）
CREATE TABLE anomaly_flags (
  id            TEXT PRIMARY KEY,
  fact_id       TEXT NOT NULL REFERENCES facts(id),
  rule          TEXT NOT NULL,           -- date_order|date_overlap|date_ambiguous|
                                         -- percent_bound|amount_magnitude|missing_field
  detail        TEXT NOT NULL,           -- JSON，人类可读说明 + 机器可读参数
  resolved      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

M3 表（gate_events / versions / applications / outcomes）按 ard/0006
在 M3 建，此处不预建。

> **变更标注（2026-09-18，M3）**：上述表结构已定稿——gate_events 见
> §17.1、ledger 三表见 §19；另有 rewrites / rewrite_sentences（§15.5）
> 与 sentence_validations（§16.6）。SCHEMA_VERSION 3 → 4 迁移只追加
> 新表，不改 M1/M2 历史表（沿用 §12.2 纪律）。

## 3. normalize 规格（shared/normalize.py，ard/0001）

### 3.1 规则（机械、无损、无歧义，顺序固定）

1. Unicode NFKC（覆盖全角→半角、兼容字符折叠）；
2. 空白折叠：连续空白字符（含全角空格 U+3000）→ 单个空格；行尾空白
   删除；`\r\n`/`\r` → `\n`；
3. 不做：日期归一化（抽取层职责）、大小写折叠、标点替换、任何删除
   非空白字符的操作。

`NORMALIZE_VERSION = 1`：规则或顺序的任何变更 = 版本 +1 = 全量重入库。

### 3.2 接口

```python
NORMALIZE_VERSION: int
def normalize(text: str) -> str
def verify_span(content: str, span: tuple[int, int], quote: str) -> bool
def find_quote(content: str, quote: str) -> tuple[int, int] | None  # M2 用
```

### 3.3 属性测试（tests/test_normalize.py，M1 必交付）

- 幂等性：`normalize(normalize(x)) == normalize(x)`（hypothesis 随机
  中文/英文/空白混合串）；
- 偏移稳定性快照：fixtures 里 3–5 份典型中文简历（单栏/双栏/带表格），
  canonical_text 全文与抽样式 span 偏移做快照测试，normalize 任何改动
  触发快照失败；
- 不变量：输出不含 U+3000、`\r`、连续空格；`len(normalize(x)) <= len(x)`。

## 4. ingest 流水线（M1 核心）

```
上传文件 → io 保存原文件 → 文本提取(pymupdf4llm/markitdown)
→ sanitize 文本层清洗 → normalize → canonical_text 入库
→ LLM 抽取(schema 见 §5，要求每字段带 quote)
→ 程序回验：quote 经 normalize 后必须为 canonical_text 子串，
  回验得 span；定位不回的字段强制标 anomaly(missing_field 类)
→ anomaly 规则计算 → facts + anomaly_flags 入库 → 状态 reviewing
```

- **清洗规则**（sanitize）：剔除零宽字符（U+200B–U+200D、U+FEFF）、
  与背景同色的文本层（PDF 提取时按颜色过滤）、明显注入句式只记录
  不执行（LLM 输入侧另有 system prompt 护栏）。
- **阅读序**：pymupdf 排序模式提取；`parser='mineru'` 为重解析入口
  保留（M1 可实现为"未安装 MinerU 时按钮置灰并提示"）。
- **mock 模式**：`platform/llm.py` 无 key 时返回 fixtures 中的固定
  抽取结果；响应头与界面均标示 mock。
- 同步处理即可（M1 单次 ≤1 分钟），但 API 返回任务式结构，为 M2 的
  SSE 预留演进空间。

## 5. 抽取 Schema（LLM 输出，pydantic）

```python
class ExtractedFact(BaseModel):
    section: Literal["summary","work","project","education","skill","other"]
    quote: str                        # 原文逐字（回验用，唯一锚点）
    fields: dict                      # 按 section 的结构化字段
    date_interpretations: list[DateInterpretation]  # 日期归一化在这层
    attribution: Literal["individual","team","mixed","unknown"]
    entities: Entities                # numbers/orgs/skills 初抽取，anomaly 复用

class DateInterpretation(BaseModel):
    raw: str                          # "19.3-21.6"
    normalized: str | None            # "2019-03 至 2021-06"；歧义时为 None
    ambiguous: bool
    candidates: list[str]             # 歧义候选解释，gate1 展示
```

## 6. anomaly 规则（anomaly/rules.py，纯函数）

| rule             | 判定                            | 输入                   |
| ---------------- | ----------------------------- | -------------------- |
| date_order       | 同段经历结束日期早于开始日期                | date_interpretations |
| date_overlap     | 多段 work 时间区间重叠 >3 个月          | 全表 facts             |
| date_ambiguous   | `ambiguous=True` 或无法归一化       | DateInterpretation   |
| percent_bound    | payload 中百分比 >1000% 或负增长率语义矛盾 | entities.numbers     |
| amount_magnitude | 同 resume 金额类数字量级差 >10^4       | entities.numbers 聚合  |
| missing_field    | schema 必填字段缺失 / quote 回验失败    | 抽取结果                 |

规则输出 `detail` 必须含人类可读一句话说明（gate1 直接展示）。
每条规则配 test_anomaly.py 单测：正例、反例、边界。

## 7. M1 API

| 方法      | 路径                                      | 说明                                                        |
| ------- | --------------------------------------- | --------------------------------------------------------- |
| POST    | /api/resumes                            | 上传（multipart），触发解析，返回 resume_id                           |
| GET     | /api/resumes/{id}                       | 状态 + canonical_text + parser + mock 标记                    |
| GET     | /api/resumes/{id}/facts                 | 事实源列表（含 span、attribution、anomaly_flags）                   |
| PATCH   | /api/facts/{id}                         | 人工写回：edited_payload / attribution(+confirmed) / confirmed |
| POST    | /api/anomaly-flags/{id}/resolve         | 标记已处理                                                     |
| POST    | /api/resumes/{id}/reparse?parser=mineru | 重解析逃生门（新 resume 行）                                        |
| GET/PUT | /api/settings                           | LLM 端点、key、模式                                             |

## 8. M1 前端

**UploadPage**：拖拽上传、解析进度、mock 徽标。
**ReviewPage**（核心）：

- 左栏 `CanonicalTextView`：渲染 canonical_text；`selectedFactId` 或
  hover 时按 span 高亮（span → 字符偏移直接 slice，无任何坐标换算）；
- 右栏 `FactTable`：按 section 分组；每行 `AnomalyBadge`（detail 一句话
  tooltip）、`AttributionControl`（四态；team/mixed/unknown 未确认时
  行显"待确认"）、编辑抽屉（改 edited_payload）；
- 顶栏：模式切换（首次全量 / 只看标记——首次导入时后者禁用）、
  MinerU 重解析按钮、完成校对（全部 confirmed 后 resume 状态
  → confirmed）。
  **SettingsPage**：OpenAI 兼容端点 + key；mock 状态显示。

状态管理：React Query 拉取 + 本地选中态即可，不引入额外状态库。

## 9. M2/M3 接口契约（详细设计开工前补）

> **变更标注（2026-09-18，M3）**：M2 契约已实现（§12.7）；M3 两条契约
> 由 §15/§18 细化为实现契约（汇总见 §20）。一处微调：`POST /api/exports`
> 的 body 不再携带 gate_events——确认事件已按句落库（§17.1），服务端
> 按 rewrite_id 复核 pending 状态（§17.2），前端无需回传。

- `POST /api/diagnoses`（body: resume_id + jd_text）→ SSE 流：
  事件类型 `step`（parsing|evidencing n/N|reporting）、`done`、
  `error`。产物 = 要求清单（schema 见架构文档，ard/0004 单一来源）。
  【已实现，M2 §12.7】
- `POST /api/rewrites`（body: diagnosis_id）→ SSE 流：`step`
  （writing|validating round n|escalated）、`done`。产物 = 改写句集
  （句→fact_ids→derived.formula）+ 校验判定（按句持久化，ard/0006）。
  【M3 详设见 §15】
- `POST /api/exports`（body: rewrite_id）→ SSE：rendering → PDF。
  【M3 详设见 §18】
- JD 与简历输入消毒同一 sanitize 模块；quote 验证走
  `normalize.find_quote`。

## 10. 测试计划（M1）

| 层    | 内容                                           |
| ---- | -------------------------------------------- |
| 属性测试 | normalize（§3.3）                              |
| 单测   | anomaly 六条规则；sanitize 清洗规则；span 回验（含全半角差异用例） |
| 集成   | 上传→解析→校对写回 全链路（mock LLM）；重解析产生新 resume 行     |
| 前端   | ReviewPage span 联动高亮（Testing Library）        |
| 验收   | fixtures 三份中文简历跑通，span 100% 可回查              |

## 11. 环境

Python 3.11+、Node 20+；后端依赖：fastapi、uvicorn、pydantic v2、
pymupdf、pymupdf4llm、markitdown、litellm、hypothesis（测）、pytest；
前端：Vite + React 18 + TypeScript + React Query。MinerU 为可选
依赖，不装进默认环境。

> **变更标注（2026-09-18，M3）**：新增 playwright 为后端**默认依赖**
> （M3 导出唯一渲染路径，R4 已决）；首次安装需下载浏览器二进制
> （~150MB），安装失败时导出按钮置灰并给安装指引（与 MinerU 提示
> 同款交互）。单测不依赖 Playwright，集成/验收测试需要。

---

# M2 详细设计：JD 诊断

（开工前补写，2026-09-16。契约依据 ard/0003 举证式校验、ard/0004 失败分流。）

## 12. diagnose 模块

### 12.1 目录（并入 backend/app/，依赖单向：fact_store → diagnose）

```
app/diagnose/
├── api.py        # POST /api/diagnoses（SSE）+ GET /api/diagnoses/{id}
├── core.py       # 流水线编排：JD 拆解 → 逐条举证 → 汇总报告
├── jd.py         # JD 文本消毒（复用 ingest/sanitize）+ LLM 拆要求清单
├── evidence.py   # 举证：quote 生成 + find_quote_matches 程序验证 + 最近邻
├── prompts.py
├── io.py         # diagnoses / requirements 表读写
└── schemas.py
```

### 12.2 数据模型（SCHEMA_VERSION 2 迁移追加，不改历史表；v3 追加 mock 列）

```sql
CREATE TABLE diagnoses (
  id          TEXT PRIMARY KEY,
  resume_id   TEXT NOT NULL REFERENCES resumes(id),  -- 必须是 confirmed 状态
  jd_text     TEXT NOT NULL,                          -- 消毒后的 JD 原文（留档）
  report      TEXT,                                   -- JSON：汇总报告（§12.5）+ warnings
  created_at  TEXT NOT NULL,
  mock        INTEGER NOT NULL DEFAULT 0              -- v3：该次诊断生成来源是否 mock（按行记录，
                                                      -- 与 M1 审查 P3 同理，不随全局模式变化）
);
CREATE TABLE requirements (
  id           TEXT PRIMARY KEY,
  diagnosis_id TEXT NOT NULL REFERENCES diagnoses(id),
  req_index    INTEGER NOT NULL,      -- JD 中顺序
  priority     TEXT NOT NULL,         -- must | preferred
  text         TEXT NOT NULL,
  keywords     TEXT NOT NULL,         -- JSON 数组（含中英文别名）
  status       TEXT NOT NULL,         -- direct | nearest | gap（程序验证后写定）
  quote        TEXT,                  -- 证据（原文逐字；gap 时为 NULL）
  fact_id      TEXT,                  -- 证据/最近邻所在条目
  nearest_note TEXT,                  -- gap 时"最接近的是 X"的人类可读说明
  user_revived INTEGER NOT NULL DEFAULT 0  -- 人工把 gap 捞回（ard/0004）
);
```

### 12.3 流水线（SSE 分步，ard/0004 预检偏保守）

```
POST /api/diagnoses {resume_id, jd_text}
→ 校验 resume 状态 == confirmed（未校对完不允许诊断，咽喉顺序 ard/0002）
→ sanitize(jd_text)（JD 端消毒，双向防注入）
→ SSE step "parsing"：LLM 拆要求清单（schema §12.4，逐条 pydantic 校验，
  非法条目跳过记 warning——与 ingest 同款保守）
→ SSE step "evidencing n/N"：逐条举证
    LLM 候选生成（quote + fact_id 声明）→ 程序验证：
      · quote 必须经 find_quote_matches 在 canonical_text 唯一定位
        （0 处或多处 → 视为无证据；多处歧义不猜测，审查 P2 复用）
      · fact_id 声明只做参考，锚定以程序验证的 span 为准
    验证失败 → 最近邻：在该 resume 的 confirmed 事实源中按关键词重叠
      确定性排序取 top1（无 LLM），标 nearest；零关键词命中 → gap
      · gap 行附**降级候选**（`fallback_candidates`：按 section 相关性
        取 top-3，读取时确定性计算、不落库）。理由：纯词法最近邻会让
        "相关但用词不同"的可达条目标成 gap，而 gap 行的一键捞回是
        ard/0004 的最低成本确认点；给候选列表比断言"无相关经历"诚实
        （M2 审查 P2：我们验证的是关键词重叠，不是相关性）
→ SSE step "reporting"：汇总（§12.5）入库
→ SSE done（diagnosis_id）/ error
```

**保守偏向的机制保证**：找不到可验证 quote 一律 gap/nearest，无第三条路。
错标缺口用户可一句话捞回（`user_revived`），错判可达则 M3 擦边压力回归。

### 12.4 要求清单 LLM 输出 schema

```python
class RequirementItem(BaseModel):
    priority: Literal["must", "preferred"]
    text: str                     # 要求原文（如"3 年以上推荐系统经验"）
    keywords: list[str]           # 关键词含中英文别名（["推荐系统", "recommender"]）

class EvidenceCandidate(BaseModel):
    req_index: int
    quote: str | None             # 模型给出的逐字证据；找不到必须为 None
    fact_id: str | None           # 声明的出处条目（仅参考，程序复验）
    nearest_fact_id: str | None   # 无直接证据时声明的最接近条目
```

prompt 原则：与 ingest 同构的举证体——"找出证据"而非"评价匹配度"；
明确要求"没有可逐字引用的证据就输出 null，禁止转述、禁止概括"
（ard/0003：牵强配对在 quote 层面被程序拒收）。

### 12.5 报告结构（诊断页渲染依据）

```jsonc
{
  "summary": {
    "must_total": 5, "must_direct": 3, "must_nearest": 1, "must_gap": 1,
    "preferred_total": 3, "preferred_direct": 2,
    "worth_applying": true,          // 全 must 缺口 → false + 提示文案
    "notice": null                   // "该岗位 3 条硬性要求均无对应经历，可能不值得投"
  },
  "requirements": [/* 按 must 在前、缺口在前的顺序 */],
  "gaps": [/* 硬性缺口清单：M3 改写器目标函数剔除它们（分母修正） */]
}
```

must/preferred 分层呈现；全 must 缺口时 `worth_applying=false` 并生成
"岗位可能不值得投"提示——诊断报告的决策价值（ard/0004）。

### 12.6 人工捞回（ard/0004 低成本确认点）

`POST /api/requirements/{id}/revive {fact_id}`：用户确认"最接近的 X 其实
相关"→ status nearest→direct（user_revived=1），quote 取该 fact 的
raw_quote，程序复验后更新报告汇总。**诊断报告的重算必须确定性**
（基于 requirements 行，不重新调 LLM）。

**证据强度语义（勿与 pipeline 的 direct 混同）**：revive 是 *fact_id
锚定*——用户选定条目、人工判定，仅要求 quote 可定位（≥1 处），UI 以
「人工确认」标识；pipeline 的 direct 是 *quote 唯一定位*（程序验证），
UI 显示「有直接引用」。保留宽松门槛的理由：多处匹配的条目在校对页
无法消除歧义（raw_quote 不可改），严格门槛会堵死人工捞回（ard/0004）。

### 12.7 API

| 方法   | 路径                            | 说明                                               |
| ---- | ----------------------------- | ------------------------------------------------ |
| POST | /api/diagnoses                | body: resume_id + jd_text；SSE 流（step/done/error） |
| GET  | /api/diagnoses/{id}           | 报告 + 要求清单                                        |
| GET  | /api/resumes/{id}/diagnoses   | 历史诊断列表                                           |
| POST | /api/requirements/{id}/revive | 人工捞回（body: fact_id）                              |

### 12.8 前端 DiagnosePage

- 入口：ReviewPage 完成校对后 / 首页选已 confirmed 简历 → 粘贴 JD；
- 进度：SSE 分步（解析 JD → 举证 n/N → 生成报告），断流可重试；
- 报告页：summary 卡（worth_applying 显著提示）→ must 组 → preferred 组；
  每条要求一行：状态徽标（有证据/最接近/缺口）、quote 可点击跳
  ReviewPage 对应高亮；gap 行内"最接近的是 X，相关吗？"一键捞回；
- mock 模式：返回固定诊断（fixtures/mock_diagnosis.json），全链路可演示。

## 13. 标注测试集（M2 开工条件，人工含量高，立即开始）

### 13.1 格式（tests/fixtures/annotated/，每对两个文件）

```
annotated/
├── pairs/
│   ├── 001_resume.txt      # 简历原文（脱敏；走 sanitize→normalize 同链路）
│   ├── 001_jd.txt          # JD 原文
│   └── 001_expected.json   # 期望举证结果
```

```jsonc
// 001_expected.json
{
  "note": "人工复核定稿（<日期>，对照 <模型> 真实录制 vcr/001.json）",
  "requirements": [
    {
      "text_contains": "推荐系统",       // 期望被拆出的要求（模糊匹配）
      "priority": "must",
      "expect_status": "direct",         // direct | nearest | gap
      "expect_quote_contains": "推荐系统重构",  // direct 时期望证据包含此串
      "comment": "复核结论说明（改期望值时必须写明依据）"
    },
    { "text_contains": "管理经验", "priority": "must", "expect_status": "gap" },
    {
      "text_contains": "Spark",
      "priority": "must",
      "expect_status": "direct",
      "boundary": "m3-l2",               // 已知机制边界：不计入吻合率分母
      "comment": "M3 L2 验收输入：简历有证据但 LLM 漏引，M2 程序无法区分漏引与无证据"
    }
  ]
}
```

**boundary 的使用纪律**（防止变成"藏不达标项的后门"）：边界条目不进
吻合率分母，但每个必须带 `comment` 说明为什么是机制边界而非标注错误，
且总量有上限（测试断言 ≤5）；M3 的 L2 落地后应逐条翻正。

> **变更标注（2026-09-18，M3 收口回写）**：3 条 boundary: m3-l2 条目已逐条
> 翻正（C9）——L2 层落地（§16.5），由 `tests/fixtures/l2/` 标注集承接验收：
> 真实录制 9/9 吻合，其中 Spark/SQL 判"蕴含"（LLM 保守漏引是诊断层现象，
> 不是证据缺失）、Redis 半对证据判"不蕴含"。**本表（诊断层）口径不变**：
> 诊断 pipeline 未改（M3 只读不写），boundary 仍不计入本表分母、状态随
> M2 录制；3 条 comment 增补"M3 已由 L2 标注集承接翻正"仅作职责划分标记，
> 实际防线已移到改写校验阶段。

### 13.2 验收方式（tests/test_diagnose_annotated.py）

- 每对跑完整诊断链路（真实 LLM 或录制回放，见下），逐条比对
  expect_status；**direct 率与 gap 误判率分开统计**——gap 误判
  （可达错标缺口）容忍度高于 direct 误判（ard/0004 不对称性）；
- LLM 响应用 VCR 式录制（首次真实调用后固化 JSON），CI 回放——
  测试集回归不被 API 可用性阻塞，prompt 改动时才重录；
- **阈值（2026-09-18 人工复核定稿后写回）**：非边界条目状态吻合率
  ≥ **0.85**；当前基线 **39/39 = 100%**、gap 误判 **0**、拆解召回 100%
  （VCR 回放确定性，0.85 是 prompt/模型改动重录时的回归预警线）。
  统计口径：分母只计非边界条目；direct 率与 gap 误判率分开输出。
- 状态（2026-09-18，M2 验收完成）：10 对已由 mock 基线草稿复核为
  **人工标注**（`draft` 标记全部移除，守卫测试禁止残留草稿混入门槛）；
  真实 LLM 录制在 `tests/fixtures/annotated/vcr/`（deepseek-flash）。
  回归测试优先回放录制、无录制走 mock 规则。
- 复核过程沉淀（本次 9 条差异的处置）：
  · 6 条改期望值——3 条草稿期望过高（管理类要求实际应为 gap）、
    1 条草稿低估（LLM 找到"交易链路稳定性建设"逐字证据）、
    1 条 LLM 更诚实（技能行有 MySQL 无"优化"，direct→nearest）、
    1 条 quote 断言放宽（"Java 后端开发"的年限证据落在岗位行，
    Java 本身在技能行——单 quote 无法同时锚两处）；
  · 3 条标为 boundary（M3 L2 验收输入）：LLM 保守漏引 ×2、
    单条要求含多子项时的半对证据 ×1（Kafka 是消息队列但 Redis 无证据）。

### 13.3 标注来源

- fixtures 现有 4 份简历文本可直接配 2–3 个 JD（覆盖互联网技术岗：
  推荐算法/后端/数据）；
- 缺口案例必须包含"JD 要求的技能简历里完全没有"（验证如实报缺口）
  与"相关但不直接"（验证 nearest 而非硬配）两类；
- 总量 10–20 对，先 10 对解锁 M2 验收。

### 13.4 M2 测试计划（除标注回归外）

| 层   | 内容                                                                    |
| --- | --------------------------------------------------------------------- |
| 单测  | 证据验证（0 处/1 处/多处 quote → gap/direct/gap）；最近邻排序确定性；报告汇总与 worth_applying |
| 集成  | 诊断全链路（mock LLM）；revive 后报告确定性重算；未 confirmed 简历拒绝诊断（422）               |
| 前端  | DiagnosePage SSE 进度渲染；捞回交互                                            |

---

# M3 详细设计：改写、校验与导出

（M2 验收收口后补写，2026-09-18。契约依据 ard/0003 举证式校验、
ard/0004 失败分流、ard/0005 三层幻觉检测、ard/0006 事件落库；同步项
见架构文档 v0.4「M3 同步项」。）

## 14. M3 范围与相对 M2 的变更点

### 14.1 范围（PRD 用户故事 3.1–3.7）

M3 = rewrite（声明出处的改写）→ validate（L0/归因动词/L1/L2 四层）→
失败分流（≤2 轮重写 / 硬性升级）→ gate2（diff 逐条确认 +
gate_events 落库）→ export（ATS 单模板 PDF）→ ledger（版本/投递/
结果，只记录不分析）。

### 14.2 变更点总览（相对 M2）

| #   | 变更                                                                | 类型  | 说明                                                                  |
| --- | ----------------------------------------------------------------- | --- | ------------------------------------------------------------------- |
| C1  | rewrite 模块                                                        | 新增  | §15；消费 diagnoses/requirements，目标清单剔除硬性缺口（ard/0004 分母修正）             |
| C2  | validate 模块 + verb_lexicon 资产 + formula 求值器                       | 新增  | §16；L0/verb/L1 确定性三层 + L2 LLM 蕴含，判定按句按轮持久化（ard/0006）                |
| C3  | gate2 页面 + gate_events 表                                          | 新增  | §17；动作 × 当次判定快照配对落库（ard/0006 同步项，误报/漏报分析原料）                         |
| C4  | export 模块（ATS 单模板，Playwright 渲染）                                  | 新增  | §18；v2 双模板仅登记不做（架构 v0.4 已登记）                                        |
| C5  | ledger 三表（versions/applications/outcomes）                         | 新增  | §19；v1 只记录不分析                                                       |
| C6  | SCHEMA_VERSION 3 → 4                                              | 变更  | 迁移只追加新表，不改 M1/M2 历史表（沿用 §12.2 纪律）                                   |
| C7  | §9 契约中 /api/rewrites、/api/exports                                 | 落地  | 由 §15/§18 细化为实现契约（§20）；exports body 微调（见 §9 变更标注）                   |
| C8  | edited_payload 首次被下游消费                                            | 变更  | 改写与 L2 的事实源取值规则：edited_payload ?? payload，raw_quote 恒为锚（M1/M2 只存未用） |
| C9  | M2 标注集 3 条 boundary: m3-l2 条目                                     | 承接  | L2 验收输入；L2 落地后逐条翻正并回写 §13.2 口径（spec §13.1 纪律）                       |
| C10 | requirements.user_revived / fallback_candidates                   | 承接  | 直接作为改写目标清单与升级清单的输入，M3 不改其语义                                         |
| C11 | normalize / canonical_text / facts / diagnoses / requirements 表结构 | 不变  | M3 只读不写；gate_events 引用改写句，不回写事实源                                    |

## 15. rewrite 模块

### 15.1 目录（并入 backend/app/，依赖单向：fact_store / diagnose → rewrite）

```
app/rewrite/
├── api.py        # POST /api/rewrites（SSE）+ GET 系接口
├── core.py       # 编排：目标清单 → 改写 → 校验 → 失败分流（≤2 轮）
├── prompts.py
├── io.py         # rewrites / rewrite_sentences 表读写
└── schemas.py
```

### 15.2 改写输出 schema（逐句带锚点；ard/0005 数据模型级契约）

```python
class DerivedNumber(BaseModel):
    value: str                    # 稿面呈现值 "30%"；区间形式两端点分列
    formula: str                  # "(910000-700000)/700000"；白名单求值见 §16.4
    source_ids: list[str]         # 参与推导的事实源条目

class RewrittenSentence(BaseModel):
    section: Literal["summary","work","project","education","skill","other"]
    text: str
    source_fact_ids: list[str]    # 非空——无出处的句子程序拒收（ard/0003）
    derived_numbers: list[DerivedNumber]
    verbs: list[str]              # 模型自报，仅参考；动词检查由程序扫文本复核（§16.3）
    requirement_ids: list[str]    # 该句服务的目标要求（§15.3 清单内）
```

prompt 原则：与 ingest/diagnose 同构的举证体——"每句给出处"而非
"写得漂亮"；明确"事实源没有的数字/实体禁止出现，禁止概括式夸大"；低温。
事实源取值规则：payload 取 `edited_payload ?? payload`（人工修正优先，
C8），raw_quote 恒为锚定引用。

### 15.3 目标清单与失败分流（ard/0004 分母修正）

- 目标清单 = diagnosis 的 requirements 中 `status ∈ {direct, nearest}`
  或 `user_revived=1` 的条目；`status=gap` 且未捞回的（不分
  must/preferred）一律剔除——注定无证据的条目留在分母 = 制造擦边压力。
- 剔除项形成升级清单随产物返回（"该岗位要求 X，经历库中没有，建议
  补充经历或接受低覆盖"），不进入改写与重试。
- 校验失败分流：可修复（L0/L1/verb/格式）→ 带反馈重写 ≤2 轮（判定
  detail 进 prompt）；2 轮未过的句子不再自动重试，带全部判定历史进
  gate2 强标红交人工；改写中新暴露的硬性缺口（L2 连续判定出处不
  蕴含）→ escalated 升级回诊断报告，不重试。

### 15.4 流水线（SSE 分步）

```
POST /api/rewrites {diagnosis_id}
→ 复核 diagnosis 存在且其 resume 仍为 confirmed（咽喉顺序 ard/0002，
  M2 已卡诊断入口，此处服务端复核改写入口）
→ 计算目标清单 + 升级清单（§15.3）
→ SSE step "writing"（round n）：LLM 改写，逐句 pydantic 校验，
  非法句跳过记 warning——与 ingest/diagnose 同款保守
→ SSE step "validating round n"：四层校验（§16），判定按句按轮持久化
→ 可修复失败 → writing round n+1（≤2 轮）
→ SSE step "escalated"（有升级项时，附清单）
→ SSE done（rewrite_id）/ error
```

mock 模式：固定改写句集 + 固定判定（fixtures/mock_rewrite.json，
含标红例句供 gate2 演示）；rewrites.mock 按行记录（沿用 M1 P3 /
M2 v3 惯例）。

### 15.5 数据模型（SCHEMA_VERSION 4 迁移追加，不改历史表）

```sql
CREATE TABLE rewrites (
  id             TEXT PRIMARY KEY,
  diagnosis_id   TEXT NOT NULL REFERENCES diagnoses(id),
  status         TEXT NOT NULL,           -- writing|validating|done|escalated|exported
  rounds         INTEGER NOT NULL DEFAULT 0,   -- 已用重写轮次（≤2）
  target_req_ids TEXT NOT NULL,           -- JSON：目标清单（分母修正后）
  escalations    TEXT NOT NULL DEFAULT '[]',   -- JSON：升级回报告的缺口/不可修复项
  mock           INTEGER NOT NULL DEFAULT 0,   -- 按行记录 mock 溯源
  created_at     TEXT NOT NULL
);

CREATE TABLE rewrite_sentences (
  id               TEXT PRIMARY KEY,
  rewrite_id       TEXT NOT NULL REFERENCES rewrites(id),
  section          TEXT NOT NULL,
  seq              INTEGER NOT NULL,      -- 稿内顺序
  original_text    TEXT NOT NULL,         -- LLM 定稿文本（diff 基准，不再变）
  text             TEXT NOT NULL,         -- 当前生效文本（gate2 编辑后更新）
  source_fact_ids  TEXT NOT NULL,         -- JSON 数组
  derived          TEXT NOT NULL,         -- JSON：DerivedNumber[]
  verbs            TEXT NOT NULL DEFAULT '[]',  -- JSON：模型自报（与程序实扫对比=诚实度信号）
  requirement_ids  TEXT NOT NULL,         -- JSON 数组
  gate_status      TEXT NOT NULL DEFAULT 'pending',  -- pending|confirmed|rejected|edited
  created_at       TEXT NOT NULL
);
```

## 16. validate 模块

### 16.1 目录（并入 backend/app/，依赖单向：fact_store → validate；由 rewrite 调用）

```
app/validate/
├── core.py       # 四层编排 + 按句汇总判定 + 分流结论
├── l0.py         # 确定性实体比对
├── verbs.py      # 归因动词检查（读 verb_lexicon）
├── l1.py         # formula 白名单求值 + 衍生数字重算
├── l2.py         # LLM 蕴含判定
├── prompts.py
├── io.py         # sentence_validations 表读写
└── schemas.py

app/verb_lexicon/           # 独立模块资产（架构模块表）
├── lexicon.json            # {"version": N, "dominant": [...], "participatory": [...]}
└── loader.py               # 加载 + LEXICON_VERSION 暴露
```

### 16.2 L0 确定性实体比对（不看声明出处，全稿扫描）

- 数字：正则抽取句中全部数字；每个数字必须 ∈ 事实源
  entities.numbers，或 ∈ 本句 derived_numbers（转 L1 裁决）；
  其余 = 偷渡，fail。
- 公司名/组织/技能词：词表 + 事实源实体集比对（沿用 M2 诊断关键词
  别名表机制）。
- detail 必须含人类可读一句话（"数字 92 万在事实源中无出处"），
  gate2 直接展示。

> **补充口径（2026-09-18，真实走查后回写）**：数字抽取前先屏蔽**日期
> 表达式**（"2019.3 - 2021.6""2020.4""2020 年度"等）。原因：ingest 实体
> 数字抽取不收无单位小数（"2019.3" 视为日期/版本号，spec §5），而真实
> 改写句普遍重述经历时间——不屏蔽会成片误报"日期数字无出处"。日期一致性
> 由 `date_interpretations` + gate1 人工确认承接（ard/0001）；屏蔽方向 =
> 漏报（编造的年份日期不在此拦下，交 L2 与人工）。

### 16.3 归因动词检查（确定性，先于 L2；ard/0002 保守默认）

- 程序扫描句文本命中 verb_lexicon **主导级**动词 → 要求该句全部
  source_fact_ids 的 attribution == 'individual' 且已经 gate1 确认；
  任一不满足即 fail（team/mixed/unknown 只许**参与级**动词）。
- 词表命中以程序扫描为准，模型自报 verbs 字段仅参考。
- 词表漏词 = 漏报方向、过宽 = 误报方向；初版宁窄勿宽（漏报由 L2 与
  人工兜底）。初版口径（R3 已决）：来源 = fixtures 语料 + 高频简历
  动词人工整理，主导级/参与级各约 20 词起步，LEXICON_VERSION=1，
  随真实改写语料版本化迭代。

### 16.4 L1 衍生数字校验（formula 白名单求值，ard/0005 / 已知问题 P2）

- 白名单 AST：只允许数字、四则运算、括号、百分号字面量；其余节点
  类型（名称/调用/属性/下标…）一律拒绝；**禁用 eval**。
- 区间形式两个端点分别求值。
- **重算**而非信声明（容差规则 R6 已决）：formula 求值结果按句面
  value 的呈现精度舍入后须与 value 相等；source 数值按其原始文本
  精度参与比对（"91 万"精确到万位）。例：(91−70)/70=0.3000…，
  句面 "30%" 按百分位舍入相等 ✓；区间形式两端点各自比对。
- 模糊量化词（"近翻倍/约两倍/百余"）：不得引入原文没有的，除非映射
  到区间且原文数值落区间内。

### 16.5 L2 LLM 蕴含判定（唯一 LLM 层，ard/0003）

- 任务定义收窄：只判定"声明的出处是否蕴含这句话"（借壳加料检测），
  不评价措辞优劣；输入 = 句文本 + source_fact_ids 对应条目的
  raw_quote + 有效 payload（C8 取值规则）。
- 输出：句子→条目 ID 映射 + 不蕴含点说明；映射不上即标红 fail。
- 与 diagnose 共用要求清单单一来源（ard/0004）；VCR 录制回放机制
  沿用 §13.2。
- M2 标注集 3 条 boundary: m3-l2 条目为本层验收输入（C9）。
- L2 标注集（R5 已决）：`tests/fixtures/l2/`（cases/*.json + vcr/），
  格式 `{sentence, source_fact_ids, expect_entailed, comment}`；
  以上述 3 条 boundary 条目为种子扩 5–10 组，随 M3-2 批次交付。

> **交付记录（2026-09-18，M3 收口）**：`tests/fixtures/l2/cases/` 9 组——
> 3 条 boundary seed（Spark/SQL/Redis）+ 6 组扩充（忠实压缩、数字夸大、
> 归因借壳、真子集、借壳加料、多出处）。`vcr/` 为 deepseek-flash 真实
> 录制（`RECORD_VCR=1` 可重录），**首轮录制 9/9 全部吻合**，含全部 3 条
> seed 翻正。回归测试 `tests/test_l2_annotated.py`：有录制时严格断言
> 逐条吻合；无录制走 mock 规则**仅观测趋势**（6 字重叠启发式不代表
> 真实水平，mock 基线 5/9）。真实走查另发现并修复 L0 日期误报
> （细节见 `已知问题.md` M3 实现记录）。

### 16.6 判定持久化（SCHEMA_VERSION 4，ard/0006）

```sql
CREATE TABLE sentence_validations (
  id          TEXT PRIMARY KEY,
  sentence_id TEXT NOT NULL REFERENCES rewrite_sentences(id),
  round       INTEGER NOT NULL,         -- 校验轮次（0=初稿）
  layer       TEXT NOT NULL,            -- L0|verb|L1|L2
  verdict     TEXT NOT NULL,            -- pass|fail
  detail      TEXT NOT NULL,            -- JSON：机器可读参数 + 人类可读一句话
  created_at  TEXT NOT NULL
);
```

## 17. gate2 与事件落库（ard/0006 同步项）

### 17.1 gate_events 表（SCHEMA_VERSION 4）

```sql
CREATE TABLE gate_events (
  id                  TEXT PRIMARY KEY,
  sentence_id         TEXT NOT NULL REFERENCES rewrite_sentences(id),
  action              TEXT NOT NULL,    -- confirm|reject|edit
  before_text         TEXT,             -- edit 前文本；confirm/reject 为 NULL
  after_text          TEXT,             -- edit 后文本；其余为 NULL
  validation_snapshot TEXT NOT NULL,    -- JSON：当次四层判定快照（误报/漏报分析原料）
  created_at          TEXT NOT NULL
);
```

判定 × 行为配对语义：用户驳回被标红句 = 误报信号；用户手改未标红句 =
漏报信号。快照取自 sentence_validations 最新一轮，不重新计算
（确定性，与 §12.6 报告重算同款纪律）。

### 17.2 确认语义与导出咽喉（ard/0002）

- 每句三动作：confirm（采用）/ reject（不进导出版）/ edit（改后采用）。
- 全部句子 gate_status ≠ pending → 允许导出；POST /api/exports
  服务端复核，有 pending 即 422（第二咽喉，与 M2 诊断入口的
  confirmed 校验同构）。
- 硬性缺口不在 gate2 标红（ard/0004：已在诊断层呈现，不重复施压）；
  2 轮未过句强标红置前，必须人工处置。

### 17.3 编辑后重校验

edit 动作落 gate_events 后立即对该句重跑确定性三层（L0/verb/L1）。
L2 重跑触发条件（R2 已决，满足任一）：① 句中数字 token 集合发生
变化（L0 抽取比对）；② 编辑后文本与原句 difflib 相似度 < 0.8。
均不触发则沿用原 L2 判定入快照——数字是幻觉高发区，轻量措辞修改
不改变蕴含关系。重校验结果写新一轮 sentence_validations，并随该句
后续事件入快照。

### 17.4 前端 Gate2Page

- 逐句卡片：改写句（内联编辑）↔ 出处对照（source_fact_ids 点击跳
  ReviewPage 定位高亮，复用 M2 的 `?span=` 机制）；
- 判定徽标：L0/verb/L1/L2 各层红标 + detail 一句话 tooltip；
- 操作：确认 / 拒绝 / 编辑；全部处置完成后导出按钮解锁；
- 顶栏：升级清单提示（硬性缺口回诊断报告入口）。

## 18. export 模块

### 18.1 目录与渲染管线

```
app/export/
├── api.py        # POST /api/exports（SSE：step rendering → done/error）+ GET /api/exports/{id}/file
├── render.py     # ATS 模板 HTML 渲染（Playwright 出 PDF）
├── ats.py        # ATS 规则自检（导出前确定性检查）
└── io.py
```

- 导出内容 = rewrite 下 confirmed + edited 句（rejected 剔除），按
  section 分组、倒序时间；诊断关键词以中英文并写形式入 skill 区
  （ATS 规则 v1 只收确定项：单栏、标准章节名、无表格文本框、倒序
  时间、关键词中英文并写）。
- ats.py 自检：渲染前对句集跑确定性规则（超长行、表格字符、非标准
  section 名），fail 即 error 不出文件。
- 文件落 data/exports/，versions 表记一行（§19）。
- v2 登记：网申版 / 直聘版双模板（v1 只做 ATS 单模板）。

### 18.2 ATS 可解析性回测

导出 PDF 用 pymupdf 读回：文本可提取、章节标题命中标准名集合、句集
100% 可读回——ATS 可解析性的程序代理验收（PRD 3.7）。

## 19. ledger 三表（SCHEMA_VERSION 4；ard/0006：只记录不分析）

```sql
CREATE TABLE versions (            -- 简历版本：一次成功导出 = 一行
  id          TEXT PRIMARY KEY,
  rewrite_id  TEXT NOT NULL REFERENCES rewrites(id),
  path        TEXT NOT NULL,        -- 导出 PDF 本地路径
  template    TEXT NOT NULL DEFAULT 'ats-v1',
  created_at  TEXT NOT NULL
);

CREATE TABLE applications (        -- 投递记录
  id          TEXT PRIMARY KEY,
  version_id  TEXT NOT NULL REFERENCES versions(id),
  company     TEXT NOT NULL,
  position    TEXT NOT NULL,
  channel     TEXT,                 -- 北森/Moka/Boss 直聘/官网…
  applied_at  TEXT NOT NULL
);

CREATE TABLE outcomes (            -- 投递结果（追加式）
  id             TEXT PRIMARY KEY,
  application_id TEXT NOT NULL REFERENCES applications(id),
  stage          TEXT NOT NULL,     -- viewed|written_test|interview|offer|rejected
  note           TEXT,
  created_at     TEXT NOT NULL
);
```

v1 不做任何分析视图；表结构先行，错过就从第一天开始丢数据
（ard/0006）。

## 20. M3 API

| 方法   | 路径                              | 说明                                                                               |
| ---- | ------------------------------- | -------------------------------------------------------------------------------- |
| POST | /api/rewrites                   | body: diagnosis_id；SSE 流（step writing/validating round n/escalated + done/error） |
| GET  | /api/rewrites/{id}              | 句集 + 各层判定 + 目标/升级清单 + mock 标记                                                    |
| GET  | /api/diagnoses/{id}/rewrites    | 历史改写列表                                                                           |
| POST | /api/sentences/{id}/gate        | gate2 动作（action: confirm/reject/edit + edit 文本）→ 写 gate_events + 编辑重校验（§17.3）    |
| POST | /api/exports                    | body: rewrite_id；有 pending 句 → 422；SSE：rendering → done（version_id）/error        |
| GET  | /api/exports/{id}/file          | 下载 PDF                                                                           |
| POST | /api/applications               | 记录投递（version_id + company/position/channel/applied_at）                           |
| POST | /api/applications/{id}/outcomes | 追加投递结果（stage + note）                                                             |
| GET  | /api/applications               | 投递列表（只记录不分析）                                                                     |

## 21. M3 前端

- 入口：DiagnosisReportPage 加「开始改写」按钮（worth_applying=false
  时按钮保留但提示风险——用户有权低覆盖投递）；
- RewritePage：SSE 分步进度（writing round n → validating round n →
  escalated/done），断流可重试（沿用 M2 DiagnosePage 模式）；
- Gate2Page（核心）：见 §17.4；
- 导出：Gate2Page 内完成（渲染进度 + 下载 +「记录投递」可选表单）；
- mock 模式：固定改写 + 标红例句可演示全链路（沿用 M1/M2 标示纪律：
  演示数据，非真实水平）。

## 22. M3 测试计划

| 层     | 内容                                                                                                                                                                    |
| ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 单测    | formula 求值器（合法式/区间/百分号；注入拒绝：名称/调用/属性节点）；verb_lexicon 命中 × 归因组合矩阵（individual/team/mixed/unknown × 主导/参与）；L0 偷渡数字与合法衍生；L1 重算（精度容差、模糊量化词区间）；目标清单分母修正；失败分流（重试计数、硬性缺口不进重试） |
| L2 回归 | prompt + VCR 录制回放（沿用 §13.2 机制）；M2 的 3 条 boundary: m3-l2 条目逐条翻正验证（C9）；tests/fixtures/l2/ 标注 5–10 组（种子 = 3 条 boundary，R5）                                                                                                  |
| 集成    | 全链路（mock LLM）：改写→校验→重试≤2→gate2→导出；硬性缺口升级路径；gate_events 快照完整性（动作 × 判定可配对查询）；pending 句导出 422                                                                            |
| 前端    | Gate2Page 确认/拒绝/编辑交互；出处 `?span=` 跳转；导出解锁逻辑                                                                                                                            |
| 验收    | 导出 PDF pymupdf 回读（§18.2）；fixtures 简历全链路；真实 LLM（deepseek-flash）走查一次                                                                                                    |

## 23. 任务拆解与里程碑

| 批次          | 内容                                                            | 出口标准                                 |
| ----------- | ------------------------------------------------------------- | ------------------------------------ |
| M3-1 确定性资产  | SCHEMA v4 迁移；formula 求值器；verb_lexicon 初版（v1 词表）；L0/verb/L1 单测 | 三层确定性校验单测全绿                          |
| M3-2 改写校验链路 | rewrite + validate 编排 + 失败分流 + L2 + mock + VCR 录制 + L2 标注集扩充（R5） | 集成链路（mock）全绿；boundary 条目翻正结果回写 §13.2 |
| M3-3 gate2  | Gate2Page + gate_events + 导出咽喉 + 编辑重校验                        | 动作 × 快照配对可查；前端测试全绿                   |
| M3-4 出口     | export（Playwright）+ ATS 回测 + ledger 三表 + 真实 LLM 走查            | PDF 回读验收通过；全量测试全绿                    |

依赖顺序：M3-1 → M3-2 →（M3-3、M3-4 可并行）→ 验收。

## 24. 风险决策与依赖项

### 24.1 已决（2026-09-18 拍板，按 spec 建议方案）

| # | 事项 | 决定 | 状态 |
| --- | --- | --- | --- |
| R1 | L2 误报/漏报无量化门槛 | 接受"机制交付、阈值后调"：v1 只交付机制（映射不上必标红 + 判定落库），阈值随 gate_events 数据积累后调（PRD §7 指标即来源于此） | 已决 |
| R2 | gate2 编辑后 L2 是否重跑 | 确定性三层必重跑；L2 触发条件（满足任一）：句中数字 token 集合变化，或编辑后文本与原句 difflib 相似度 < 0.8；否则沿用原判定。细则见 §17.3 | 已决 |
| R3 | verb_lexicon 初版覆盖度 | 初版宁窄勿宽：fixtures 语料 + 高频简历动词人工整理，主导级/参与级各约 20 词起步，LEXICON_VERSION=1 版本化迭代。细则见 §16.3 | 已决 |
| R4 | Playwright 安装策略 | 列为后端默认依赖（导出唯一渲染路径，不沿用 MinerU 置灰策略）；首次安装下载浏览器二进制（~150MB），安装失败时导出按钮置灰并给明确指引。见 §11 | 已决 |
| R5 | L2 标注集是否扩充 | 扩充：tests/fixtures/l2/（格式见 §16.5），以 3 条 boundary 条目为种子扩 5–10 组，随 M3-2 交付 | 已决 |
| R6 | L1 精度容差带 | 按呈现精度舍入比对：formula 求值结果按句面 value 呈现精度舍入后与 value 相等，source 数值按原始文本精度参与。细则见 §16.4 | 已决 |

### 24.2 依赖（已就绪 / 外部）

- M2 要求清单契约稳定（PRD 里程碑依赖，已兑现：§12.2 表结构 +
  §12.5 报告结构冻结）；
- M2 标注集 3 条 boundary: m3-l2 条目 = L2 验收输入（已就绪）；
- VCR 录制回放链路（M2 已就绪，deepseek-flash）；
- Playwright（默认依赖，R4 已决，见 §11）；
- 真实 LLM key（真实走查用；无 key 时 mock 不阻塞开发，沿用
  M1/M2 纪律）。

## 25. M3 实现与验收记录（2026-09-18）

详情见 `已知问题.md`「M3 实现记录」，此处只记与规格相关的收口结论：

- **批次全部交付**：M3-1（SCHEMA v4 迁移 / formula 白名单求值器 /
  verb_lexicon v1）→ M3-2（rewrite + validate 四层 + 失败分流 + L2 标注集
  录制）→ M3-3（gate2 + gate_events + 编辑重校验 + 导出咽喉）→
  M3-4（export + ATS 回测 + ledger 三表 + 真实 LLM 走查）；
- **真实走查（deepseek-flash）**：pair 001 全链路一次通过——13 句 4 层
  全过、gate2 处置完成、PDF 回读 13/13、标准章节名命中；真实走查暴露
  并修复 1 处验证层误报（L0 日期数字，见 §16.2 说明与 `已知问题.md`）；
- **mock 全链路**：无 key 环境改写 → 校验 → 重试 → gate2 → 导出全通，
  界面/响应头/按行 `mock` 字段标示（ard/0007 纪律）；
- **已知观察**：pair 007 复跑（真实 LLM）与 M2 录制时行为不同
  （All gap vs Redis direct）——真实 LLM 非确定性，M2 回归仍走 VCR
  回放不受影响；重录时需按 §13.2 复核差异。



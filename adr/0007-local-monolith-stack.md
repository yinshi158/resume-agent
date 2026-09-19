# 0007. 本地单体：FastAPI + React/Vite + SQLite + LiteLLM，mock 模式

状态：已定稿
日期：2026-09-16

## 背景

面向国内求职市场，第一版做网页。需要一个能跑在求职者自己机器上、
简历数据不出本机的形态。前端曾权衡"无构建原生 JS 起步、后迁 React"
与"React/Vite 一步到位"，已拍板后者。

## 决定

1. 本地单体 Web 应用，localhost、单用户、无鉴权。
2. 后端 Python + FastAPI；前端 React + Vite（一步到位，不做原生 JS
   过渡）。
3. 存储 SQLite：事实源、诊断报告、举证表、事件表全入库，保留一键
   导出 JSON 便于调试。
4. LLM 走 LiteLLM 网关，默认 DeepSeek，可切 GLM/Kimi/Claude，本地
   Ollama 备选；API key 设置页配置。
5. **mock 模式**：无 key 时全链路可跑通（规则抽取 + 假改写），保证
   开发演示不被 key 卡住；但 mock 的中文解析质量不代表真实水平，
   演示不得冒充。
6. 分钟级 LLM 任务（诊断、改写-校验、导出）必须 SSE 分步进度。
7. 解析主用 pymupdf4llm/markitdown，复杂排版按需切 MinerU（gate1
   提供手动重解析入口）；PDF 导出用 Playwright 渲染 HTML。

## 后果

- 正面：隐私叙事干净（数据不出本机）；无运维负担；模型成本用户
  自付自选。
- 负面：单机形态排除了多人协作与云端同步（v2 以后再议）；
  React/Vite 引入 Node 工具链依赖。
- 不可逆性：部署形态与存储选型牵连所有模块，重写成本高。

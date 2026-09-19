"""SQLite 连接与迁移（ard/0007：数据全量本地存储，无云依赖）。

- 数据库文件：``<项目根>/data/resume_agent.db``，运行时生成；
- 测试/部署可用环境变量 ``RESUME_AGENT_DATA_DIR`` 覆盖数据目录；
- 迁移用 ``PRAGMA user_version`` 管理，版本号与建表语句一一对应，
  后续版本只追加迁移、不改历史（数据入库后偏移永久绑定，ard/0001）。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# backend/app/platform/db.py → 项目根 = parents[3]
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"

# schema 版本：v1 = M1（spec §2）；v2 = M2 追加诊断两张表（spec §12.2）；
# v3 = M2 诊断 mock 溯源列（按行记录，呼应 M1 审查 P3）
# 注：mock 解析来源按行记录在 resumes.parser 的 'mock' 枚举值上，不额外新增列
# v4 = M3：rewrites / rewrite_sentences（§15.5）、sentence_validations（§16.6）、
#          gate_events（§17.1）、ledger 三表 versions/applications/outcomes（§19）
#          ——只追加新表，不改 M1/M2 历史表（沿用 §12.2 纪律）
SCHEMA_VERSION = 4

_SCHEMA_V1 = [
    # 一份简历的一次解析（重解析产生新行，不覆盖）
    """
    CREATE TABLE IF NOT EXISTS resumes (
      id                 TEXT PRIMARY KEY,        -- uuid
      filename           TEXT NOT NULL,
      stored_path        TEXT NOT NULL,           -- 上传原件在 data/uploads 下的相对路径
      parser             TEXT NOT NULL,           -- 'pymupdf4llm' | 'markitdown' | 'mineru' | 'mock'
      normalize_version  INTEGER NOT NULL,        -- 入库时的 NORMALIZE_VERSION（ard/0001）
      status             TEXT NOT NULL,           -- 'parsed' | 'reviewing' | 'confirmed'
      created_at         TEXT NOT NULL
    )
    """,
    # 规范原文层：入库即不可变（ard/0001）
    """
    CREATE TABLE IF NOT EXISTS canonical_texts (
      resume_id  TEXT PRIMARY KEY REFERENCES resumes(id),
      content    TEXT NOT NULL                    -- 机械归一化后的纯文本
    )
    """,
    # 事实源条目
    """
    CREATE TABLE IF NOT EXISTS facts (
      id                     TEXT PRIMARY KEY,
      resume_id              TEXT NOT NULL REFERENCES resumes(id),
      section                TEXT NOT NULL,       -- summary|work|project|education|skill|other
      raw_quote              TEXT NOT NULL,       -- canonical_text 逐字片段
      span_start             INTEGER NOT NULL,    -- 字符偏移（Python 码点语义）；-1 表示回验失败未定位
      span_end               INTEGER NOT NULL,
      payload                TEXT NOT NULL,       -- JSON：结构化字段（含日期归一化结果、entities）
      attribution            TEXT NOT NULL DEFAULT 'unknown',
      attribution_confirmed  INTEGER NOT NULL DEFAULT 0,
      confirmed              INTEGER NOT NULL DEFAULT 0,
      edited_payload         TEXT,                -- 人工修正后的 payload（不改原文层）
      created_at             TEXT NOT NULL
    )
    """,
    # 异象标记（一次计算，随事实源版本失效）
    """
    CREATE TABLE IF NOT EXISTS anomaly_flags (
      id        TEXT PRIMARY KEY,
      fact_id   TEXT NOT NULL REFERENCES facts(id),
      rule      TEXT NOT NULL,                    -- date_order|date_overlap|date_ambiguous|
                                                  -- percent_bound|amount_magnitude|missing_field
      detail    TEXT NOT NULL,                    -- JSON：人类可读说明 + 机器可读参数
      resolved  INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    # 索引（实现优化，不改变契约）
    "CREATE INDEX IF NOT EXISTS idx_facts_resume ON facts(resume_id)",
    "CREATE INDEX IF NOT EXISTS idx_flags_fact ON anomaly_flags(fact_id)",
    "CREATE INDEX IF NOT EXISTS idx_resumes_filename ON resumes(filename)",
]

# mock 来源按行记录的方式：resumes.parser 记 'mock' 枚举值（spec §2），
# 接口不拿全局设置回答历史版本；因此无需新增列。

# M2：JD 诊断（spec §12.2）——只追加，不改历史表
_SCHEMA_V2 = [
    # 一次诊断（同一简历可对不同 JD 多次诊断）
    """
    CREATE TABLE IF NOT EXISTS diagnoses (
      id          TEXT PRIMARY KEY,
      resume_id   TEXT NOT NULL REFERENCES resumes(id),  -- 必须为 confirmed 状态
      jd_text     TEXT NOT NULL,                         -- 消毒后的 JD 原文（留档）
      report      TEXT,                                  -- JSON：汇总报告（spec §12.5）
      created_at  TEXT NOT NULL
    )
    """,
    # 要求清单：diagnose 与 M3 validate 的共享契约（ard/0004）
    """
    CREATE TABLE IF NOT EXISTS requirements (
      id           TEXT PRIMARY KEY,
      diagnosis_id TEXT NOT NULL REFERENCES diagnoses(id),
      req_index    INTEGER NOT NULL,        -- JD 中顺序
      priority     TEXT NOT NULL,           -- must | preferred
      text         TEXT NOT NULL,
      keywords     TEXT NOT NULL,           -- JSON 数组（含中英文别名）
      status       TEXT NOT NULL,           -- direct | nearest | gap（程序验证后写定）
      quote        TEXT,                    -- 证据（原文逐字；gap 时为 NULL）
      fact_id      TEXT,                    -- 证据/最近邻所在条目
      nearest_note TEXT,                    -- gap 时"最接近的是 X"的人类可读说明
      user_revived INTEGER NOT NULL DEFAULT 0  -- 人工把缺口/最近邻捞回 direct
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_diagnoses_resume ON diagnoses(resume_id)",
    "CREATE INDEX IF NOT EXISTS idx_requirements_diagnosis ON requirements(diagnosis_id)",
]

# M2 追加：诊断 mock 溯源按行记录（M1 审查 P3 同理——历史诊断的
# 生成来源不随全局模式切换而变）。只追加列，不改历史表结构。
_SCHEMA_V3 = [
    "ALTER TABLE diagnoses ADD COLUMN mock INTEGER NOT NULL DEFAULT 0",
]

# M3：改写 / 校验 / gate2 / ledger（spec §15.5、§16.6、§17.1、§19）
# 一次改写（入口复核 resume confirmed；重写失败不留半成品行，M2 同款）
_SCHEMA_V4 = [
    """
    CREATE TABLE IF NOT EXISTS rewrites (
      id             TEXT PRIMARY KEY,
      diagnosis_id   TEXT NOT NULL REFERENCES diagnoses(id),
      status         TEXT NOT NULL,              -- writing|validating|done|escalated|exported
      rounds         INTEGER NOT NULL DEFAULT 0, -- 已用重写轮次（≤2）
      target_req_ids TEXT NOT NULL,              -- JSON：目标清单（分母修正后）
      escalations    TEXT NOT NULL DEFAULT '[]', -- JSON：升级回缺口/不可修复项
      mock           INTEGER NOT NULL DEFAULT 0, -- 按行记录 mock 溯源
      created_at     TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS rewrite_sentences (
      id              TEXT PRIMARY KEY,
      rewrite_id      TEXT NOT NULL REFERENCES rewrites(id),
      section         TEXT NOT NULL,
      seq             INTEGER NOT NULL,          -- 稿内顺序
      original_text   TEXT NOT NULL,             -- LLM 定稿文本（diff 基准，不再变）
      text            TEXT NOT NULL,             -- 当前生效文本（gate2 编辑后更新）
      source_fact_ids TEXT NOT NULL,             -- JSON 数组
      derived         TEXT NOT NULL,             -- JSON：DerivedNumber[]
      verbs           TEXT NOT NULL DEFAULT '[]',-- JSON：模型自报（与程序实扫对比=诚实度信号）
      requirement_ids TEXT NOT NULL,             -- JSON 数组
      gate_status     TEXT NOT NULL DEFAULT 'pending', -- pending|confirmed|rejected|edited
      created_at      TEXT NOT NULL
    )
    """,
    # 四层判定按句按轮持久化（ard/0006；gate_events 快照取自最新一轮）
    """
    CREATE TABLE IF NOT EXISTS sentence_validations (
      id          TEXT PRIMARY KEY,
      sentence_id TEXT NOT NULL REFERENCES rewrite_sentences(id),
      round       INTEGER NOT NULL,              -- 校验轮次（0=初稿）
      layer       TEXT NOT NULL,                 -- L0|verb|L1|L2
      verdict     TEXT NOT NULL,                 -- pass|fail
      detail      TEXT NOT NULL,                 -- JSON：机器可读参数 + 人类可读一句话
      created_at  TEXT NOT NULL
    )
    """,
    # gate2 动作 × 当次判定快照（ard/0006 同步项：误报/漏报分析原料）
    """
    CREATE TABLE IF NOT EXISTS gate_events (
      id                  TEXT PRIMARY KEY,
      sentence_id         TEXT NOT NULL REFERENCES rewrite_sentences(id),
      action              TEXT NOT NULL,         -- confirm|reject|edit
      before_text         TEXT,                  -- edit 前文本；confirm/reject 为 NULL
      after_text          TEXT,                  -- edit 后文本；其余为 NULL
      validation_snapshot TEXT NOT NULL,         -- JSON：当次四层判定快照
      created_at          TEXT NOT NULL
    )
    """,
    # ledger：简历版本（一次成功导出 = 一行，spec §19；v1 只记录不分析）
    """
    CREATE TABLE IF NOT EXISTS versions (
      id          TEXT PRIMARY KEY,
      rewrite_id  TEXT NOT NULL REFERENCES rewrites(id),
      path        TEXT NOT NULL,                 -- 导出 PDF 本地路径
      template    TEXT NOT NULL DEFAULT 'ats-v1',
      created_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS applications (
      id          TEXT PRIMARY KEY,
      version_id  TEXT NOT NULL REFERENCES versions(id),
      company     TEXT NOT NULL,
      position    TEXT NOT NULL,
      channel     TEXT,                          -- 北森/Moka/Boss 直聘/官网…
      applied_at  TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outcomes (
      id             TEXT PRIMARY KEY,
      application_id TEXT NOT NULL REFERENCES applications(id),
      stage          TEXT NOT NULL,              -- viewed|written_test|interview|offer|rejected
      note           TEXT,
      created_at     TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rewrites_diagnosis ON rewrites(diagnosis_id)",
    "CREATE INDEX IF NOT EXISTS idx_sentences_rewrite ON rewrite_sentences(rewrite_id)",
    "CREATE INDEX IF NOT EXISTS idx_validations_sentence ON sentence_validations(sentence_id)",
    "CREATE INDEX IF NOT EXISTS idx_gate_events_sentence ON gate_events(sentence_id)",
    "CREATE INDEX IF NOT EXISTS idx_versions_rewrite ON versions(rewrite_id)",
    "CREATE INDEX IF NOT EXISTS idx_applications_version ON applications(version_id)",
    "CREATE INDEX IF NOT EXISTS idx_outcomes_application ON outcomes(application_id)",
]


def data_dir() -> Path:
    """运行时数据目录（可在测试中通过环境变量覆盖）。"""
    env = os.environ.get("RESUME_AGENT_DATA_DIR")
    return Path(env) if env else _DEFAULT_DATA_DIR


def db_path() -> Path:
    return data_dir() / "resume_agent.db"


def upload_dir() -> Path:
    return data_dir() / "uploads"


def connect() -> sqlite3.Connection:
    """打开一个连接（短生命周期，调用方负责关闭）。"""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    """建表 + 迁移到最新 schema 版本（幂等）。"""
    conn = connect()
    try:
        with conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                for stmt in _SCHEMA_V1:
                    conn.execute(stmt)
            if version < 2:
                for stmt in _SCHEMA_V2:
                    conn.execute(stmt)
            if version < 3:
                for stmt in _SCHEMA_V3:
                    conn.execute(stmt)
            if version < 4:
                for stmt in _SCHEMA_V4:
                    conn.execute(stmt)
            if version < SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            # 未来版本迁移：version < N 时在此追加语句，保持幂等
    finally:
        conn.close()

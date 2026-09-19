"""生成标注测试集草稿（spec §13.1 格式）：python tests/fixtures/annotated/make_drafts.py

⚠ 草稿性质：10 对 = 4 份 fixtures 简历 × 目标 JD 方向（后端/推荐算法/数据/运营）。
``expect_status`` 为"理想期望"的人工判断（**不是**真实 LLM 标注）；mock 链路的
实测吻合度见 tests/test_diagnose_annotated.py 的统计输出。真实标注 / VCR 录制
（``RECORD_VCR=1``）之后应替换本目录内容，并回写 spec §13.2 的数值门槛。

目录结构（生成后）：
annotated/
├── pairs/
│   ├── 001_resume.txt / 001_jd.txt / 001_expected.json
│   └── …（共 10 对）
└── vcr/            # 真实 LLM 录制回放（RECORD_VCR=1 生成，测试优先回放）

覆盖要求（spec §13.3）：
- 缺口案例含"JD 要求的技能简历里完全没有"（如 010）与"相关但不直接"
  （如 001 的管理经验、004 的高并发稳定性、007 的消息队列）两类；
- 强匹配案例含 direct 密集对（如 008 运营岗）。
"""

from __future__ import annotations

import json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_RESUMES_DIR = _HERE.parent / "resumes_text"
_PAIRS_DIR = _HERE / "pairs"

_HEADER = "任职要求："

# ---------------------------------------------------------------------------
# 10 对定义：resume = fixtures/resumes_text 中的简历；jd 一行一条要求
# （mock 拆解按行/分句，行内避免逗号连接多条要求，保证 1:1 对应）
# ---------------------------------------------------------------------------

PAIRS: list[dict] = [
    {
        "id": "001",
        "resume": "01_single_column.txt",
        "jd": f"""{_HEADER}
1. 3 年以上后端开发经验
2. 精通 Python
3. 熟悉高并发与分布式系统
4. 有推荐系统相关经验者优先
5. 有 5 年以上团队管理经验
""",
        "expect": [
            {"text_contains": "3 年以上后端开发经验", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "后端开发"},
            {"text_contains": "精通 Python", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "Python"},
            {"text_contains": "高并发与分布式系统", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "高并发与分布式系统"},
            {"text_contains": "推荐系统", "priority": "preferred",
             "expect_status": "direct", "expect_quote_contains": "推荐系统"},
            # 相关但不直接：简历有"带领 3 人小组"，非管理岗
            {"text_contains": "团队管理经验", "priority": "must",
             "expect_status": "nearest"},
        ],
    },
    {
        "id": "002",
        "resume": "01_single_column.txt",
        "jd": f"""{_HEADER}
1. 有推荐系统相关项目的落地经验
2. 熟悉深度学习框架（TensorFlow 或 PyTorch）
3. 精通 Python 与 Spark 做大规模数据处理
4. 有 A/B 实验平台建设经验者优先
5. 有 2 年以上算法团队管理经验
""",
        "expect": [
            {"text_contains": "推荐系统", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "推荐系统"},
            {"text_contains": "深度学习框架", "priority": "must",
             "expect_status": "gap"},
            {"text_contains": "Spark", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "Spark"},
            {"text_contains": "A/B 实验", "priority": "preferred",
             "expect_status": "gap"},
            {"text_contains": "算法团队管理", "priority": "must",
             "expect_status": "nearest"},
        ],
    },
    {
        "id": "003",
        "resume": "01_single_column.txt",
        "jd": f"""{_HEADER}
1. 3 年以上数据开发或数据仓库建设经验
2. 精通 SQL 与数据建模
3. 熟悉 Flink 或 Spark 实时计算链路
4. 有数据质量治理经验者优先
""",
        "expect": [
            {"text_contains": "数据仓库建设", "priority": "must",
             "expect_status": "gap"},
            {"text_contains": "SQL", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "SQL"},
            {"text_contains": "Spark", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "Spark"},
            {"text_contains": "数据质量治理", "priority": "preferred",
             "expect_status": "gap"},
        ],
    },
    {
        "id": "004",
        "resume": "02_two_column.txt",
        "jd": f"""{_HEADER}
1. 3 年以上 Java 后端开发经验
2. 精通 Spring 框架与微服务架构
3. 有高并发系统稳定性建设经验
4. 熟悉 MySQL 数据库优化
""",
        "expect": [
            {"text_contains": "Java 后端开发", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "Java"},
            {"text_contains": "Spring", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "Spring"},
            # 相关但不直接：有稳定性建设/大促流量保障，但无"高并发"字样
            {"text_contains": "高并发系统稳定性", "priority": "must",
             "expect_status": "nearest"},
            {"text_contains": "MySQL", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "MySQL"},
        ],
    },
    {
        "id": "005",
        "resume": "02_two_column.txt",
        "jd": f"""{_HEADER}
1. 有推荐系统或广告算法经验
2. 精通 Python 和深度学习框架
3. 有大规模特征工程经验
4. 有用户增长算法经验者优先
""",
        "expect": [
            {"text_contains": "推荐系统或广告算法", "priority": "must",
             "expect_status": "gap"},
            {"text_contains": "深度学习框架", "priority": "must",
             "expect_status": "gap"},
            {"text_contains": "特征工程", "priority": "must",
             "expect_status": "gap"},
            {"text_contains": "用户增长算法", "priority": "preferred",
             "expect_status": "gap"},
        ],
    },
    {
        "id": "006",
        "resume": "03_with_table.txt",
        "jd": f"""{_HEADER}
1. 3 年以上数据仓库建设经验
2. 精通 Flink 实时计算与 Kafka
3. 精通 SQL 与 Python
4. 有数据治理或元数据管理经验者优先
""",
        "expect": [
            {"text_contains": "数据仓库建设", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "数据仓库"},
            {"text_contains": "Flink", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "Flink"},
            {"text_contains": "SQL", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "SQL"},
            {"text_contains": "数据治理", "priority": "preferred",
             "expect_status": "gap"},
        ],
    },
    {
        "id": "007",
        "resume": "03_with_table.txt",
        "jd": f"""{_HEADER}
1. 精通 Java 微服务开发
2. 有高并发服务设计经验
3. 熟悉 Redis 与消息队列
4. 有 Kubernetes 容器化部署经验
""",
        "expect": [
            {"text_contains": "Java 微服务", "priority": "must",
             "expect_status": "gap"},
            {"text_contains": "高并发服务", "priority": "must",
             "expect_status": "gap"},
            # 相关但不直接：有 Kafka（消息队列），无 Redis
            {"text_contains": "Redis", "priority": "must",
             "expect_status": "nearest"},
            # 相关但不直接：有 Docker，无 Kubernetes
            {"text_contains": "Kubernetes", "priority": "must",
             "expect_status": "nearest"},
        ],
    },
    {
        "id": "008",
        "resume": "04_quantified.txt",
        "jd": f"""{_HEADER}
1. 3 年以上用户增长或活动运营经验
2. 擅长数据驱动的业务分析
3. 有会员体系搭建经验者优先
4. 有团队管理经验
""",
        "expect": [
            {"text_contains": "用户增长", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "用户增长"},
            {"text_contains": "数据驱动", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "数据驱动"},
            {"text_contains": "会员体系", "priority": "preferred",
             "expect_status": "direct", "expect_quote_contains": "会员体系"},
            {"text_contains": "团队管理", "priority": "must",
             "expect_status": "direct", "expect_quote_contains": "管理 5 人团队"},
        ],
    },
    {
        "id": "009",
        "resume": "04_quantified.txt",
        "jd": f"""{_HEADER}
1. 精通 SQL 与数据仓库建设
2. 有 Flink 实时计算经验
3. 熟练使用 Python 做数据分析
4. 有 A/B 实验设计经验
""",
        "expect": [
            {"text_contains": "SQL", "priority": "must", "expect_status": "gap"},
            {"text_contains": "Flink", "priority": "must", "expect_status": "gap"},
            {"text_contains": "Python", "priority": "must", "expect_status": "gap"},
            {"text_contains": "A/B 实验", "priority": "must", "expect_status": "gap"},
        ],
    },
    {
        "id": "010",
        "resume": "04_quantified.txt",
        "jd": f"""{_HEADER}
1. 精通 Java 或 Go 后端开发
2. 熟悉分布式系统设计
3. 有高并发系统经验
4. 熟悉 Redis 与 MySQL
""",
        "expect": [
            {"text_contains": "Java", "priority": "must", "expect_status": "gap"},
            {"text_contains": "分布式系统", "priority": "must", "expect_status": "gap"},
            {"text_contains": "高并发", "priority": "must", "expect_status": "gap"},
            {"text_contains": "Redis", "priority": "must", "expect_status": "gap"},
        ],
    },
]


def main() -> None:
    _PAIRS_DIR.mkdir(parents=True, exist_ok=True)
    for pair in PAIRS:
        resume_text = (_RESUMES_DIR / pair["resume"]).read_text(encoding="utf-8")
        (_PAIRS_DIR / f"{pair['id']}_resume.txt").write_text(resume_text, encoding="utf-8")
        (_PAIRS_DIR / f"{pair['id']}_jd.txt").write_text(pair["jd"], encoding="utf-8")
        expected = {
            "draft": True,
            "note": (
                "mock 基线草稿：expect_status 为人工理想期望（非真实 LLM 标注）；"
                "真实录制（RECORD_VCR=1）后应替换本目录内容"
            ),
            "resume_source": pair["resume"],
            "requirements": pair["expect"],
        }
        (_PAIRS_DIR / f"{pair['id']}_expected.json").write_text(
            json.dumps(expected, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"已生成 {len(PAIRS)} 对 → {_PAIRS_DIR}")


if __name__ == "__main__":
    main()

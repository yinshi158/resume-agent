"""FastAPI 装配（spec §1：main.py 只做路由挂载）。

- 启动时初始化 SQLite（建表 + 迁移）；
- ``/api/settings``（GET/PUT）：基础设施设置接口（无业务模块归属）；
- 中间件给所有 /api 响应附 ``X-Mock-Mode`` 头（spec §4：响应头与界面
  均标示 mock）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .diagnose.api import router as diagnose_router
from .export.api import router as export_router
from .fact_store.api import router as fact_store_router
from .gate1.api import router as gate1_router
from .gate2.api import router as gate2_router
from .ingest.api import router as ingest_router
from .ledger.api import router as ledger_router
from .platform import config, db
from .rewrite.api import router as rewrite_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="简历优化 Agent（M3：改写 + 校验 + 导出）", lifespan=lifespan)

# 本地单体：仅放行 Vite 开发端口的跨域（无鉴权、单用户，ard/0007）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def mock_mode_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["X-Mock-Mode"] = "true" if config.is_mock() else "false"
    return response


app.include_router(ingest_router)
app.include_router(fact_store_router)
app.include_router(gate1_router)
app.include_router(diagnose_router)
app.include_router(rewrite_router)
app.include_router(gate2_router)
app.include_router(export_router)
app.include_router(ledger_router)


# ---------------------------------------------------------------------------
# 设置（LLM 端点、key、模式）
# ---------------------------------------------------------------------------

class SettingsPatch(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_mode: Literal["auto", "live", "mock"] | None = None


def _settings_payload() -> dict:
    values = config.get_settings()
    return {
        "llm_base_url": values["llm_base_url"],
        "llm_api_key_set": bool(values["llm_api_key"].strip()),
        "llm_model": values["llm_model"],
        "llm_mode": values["llm_mode"],
        "mock": config.is_mock(),
        "mineru_available": config.mineru_available(),
        # R4：导出按钮置灰依据（Playwright 为默认依赖，缺失时给安装指引）
        "playwright_available": config.playwright_available(),
    }


@app.get("/api/settings")
def get_settings() -> dict:
    """查看设置（key 只回是否已配置，不回明文）。"""
    return _settings_payload()


@app.put("/api/settings")
def put_settings(patch: SettingsPatch) -> dict:
    """更新设置（未提供的字段不变；key 传空字符串 = 清除）。"""
    changes: dict[str, str] = {}
    if patch.llm_base_url is not None:
        changes["llm_base_url"] = patch.llm_base_url.strip()
    if patch.llm_api_key is not None:
        changes["llm_api_key"] = patch.llm_api_key.strip()
    if patch.llm_model is not None:
        changes["llm_model"] = patch.llm_model.strip()
    if patch.llm_mode is not None:
        changes["llm_mode"] = patch.llm_mode
    if changes:
        config.update_settings(changes)
    return _settings_payload()

"""设置读写：LLM 端点/key、模式（spec §7 ``GET/PUT /api/settings``）。

设置项存 SQLite ``settings`` 表（key-value），默认值如下：

- ``llm_base_url``：OpenAI 兼容端点，默认 DeepSeek；
- ``llm_api_key``：默认空（空 = mock 模式）；
- ``llm_model``：默认 deepseek-chat；
- ``llm_mode``：``auto`` | ``live`` | ``mock``；auto = 有 key 走真实调用、无 key 走 mock。

隐私（PRD §6）：唯一的出网请求是用户自配的 LLM API，端点在此处可查看。
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

from . import db

DEFAULTS: dict[str, str] = {
    "llm_base_url": "https://api.deepseek.com/v1",
    "llm_api_key": "",
    "llm_model": "deepseek-chat",
    "llm_mode": "auto",  # auto | live | mock
}

# 允许写入的设置键（白名单，防止任意键污染）
_WRITABLE_KEYS = frozenset(DEFAULTS) | {"llm_mode"}

# 应答中永远不返回明文 key 的字段名由 API 层处理（见 main.py）


def get_settings() -> dict[str, str]:
    """读取全部设置（DB 覆盖默认值）。"""
    values = dict(DEFAULTS)
    conn = db.connect()
    try:
        for row in conn.execute("SELECT key, value FROM settings"):
            if row["key"] in _WRITABLE_KEYS:
                values[row["key"]] = row["value"]
    finally:
        conn.close()
    return values


def update_settings(patch: dict[str, str]) -> dict[str, str]:
    """更新设置（只接受白名单键；值为字符串，空字符串合法 = 清除）。"""
    conn = db.connect()
    try:
        with conn:
            for key, value in patch.items():
                if key not in _WRITABLE_KEYS:
                    raise ValueError(f"未知设置项：{key}")
                if not isinstance(value, str):
                    raise ValueError(f"设置项 {key} 必须是字符串")
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
    finally:
        conn.close()
    return get_settings()


def is_mock() -> bool:
    """是否处于 mock 模式（无 key 时全链路可跑通，ard/0007）。

    - ``llm_mode == 'mock'``：强制 mock；
    - ``llm_mode == 'live'``：强制真实调用（无 key 时调用会失败，由用户负责）；
    - ``llm_mode == 'auto'``：有 key 走真实调用，无 key 走 mock。
    """
    s = get_settings()
    mode = s["llm_mode"]
    if mode == "mock":
        return True
    if mode == "live":
        return False
    return not s["llm_api_key"].strip()


def mineru_available() -> bool:
    """检测 MinerU 是否可用（可选依赖，不装进默认环境，spec §11）。"""
    try:
        if importlib.util.find_spec("mineru") is not None:
            return True
    except (ImportError, ValueError):
        pass
    return shutil.which("mineru") is not None or shutil.which("magic-pdf") is not None


def playwright_available() -> bool:
    """检测 Playwright 与其 Chromium 浏览器是否可用（R4：导出唯一渲染路径）。

    仅检查包与浏览器二进制目录是否存在（不启动浏览器，接口可频繁调用）；
    安装失败时导出按钮置灰并给安装指引（与 MinerU 提示同款交互）。
    """
    try:
        if importlib.util.find_spec("playwright") is None:
            return False
    except (ImportError, ValueError):
        return False

    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path))
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(Path(local_app_data) / "ms-playwright")
        candidates.append(Path.home() / ".cache" / "ms-playwright")
    for root in candidates:
        try:
            if root.is_dir() and any(
                entry.name.startswith(("chromium", "chrome")) for entry in root.iterdir()
            ):
                return True
        except OSError:
            continue
    return False

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

from web.paths import (
    CONFIG_PATH,
    DATA_DIR,
    PLAYWRIGHT_BROWSERS_DIR,
    PROJECT_ROOT,
    STATE_PATH,
)

_REQUIRED_MODULES = ("flask", "playwright", "dotenv", "tzdata")


def run_self_check() -> dict[str, Any]:
    checks = [
        _check("项目目录", PROJECT_ROOT.is_dir(), str(PROJECT_ROOT)),
        _check("数据目录", _is_writable(DATA_DIR), str(DATA_DIR)),
        _check("Python", sys.version_info >= (3, 10), f"{sys.version.split()[0]} · {sys.executable}"),
        _check("配置文件", CONFIG_PATH.is_file(), str(CONFIG_PATH)),
        _check("抖音登录状态", STATE_PATH.is_file(), "已保存" if STATE_PATH.is_file() else "未保存，请先扫码登录"),
        _check(
            "项目内浏览器",
            _has_project_browser(),
            str(PLAYWRIGHT_BROWSERS_DIR) if _has_project_browser() else "未安装，请运行 install.bat",
        ),
    ]
    for module in _REQUIRED_MODULES:
        checks.append(
            _check(
                f"Python 模块 {module}",
                importlib.util.find_spec(module) is not None,
                "已安装" if importlib.util.find_spec(module) is not None else "缺失，请运行 install.bat",
            )
        )
    return {
        "ok": all(item["ok"] for item in checks if item["name"] != "抖音登录状态"),
        "project_root": str(PROJECT_ROOT),
        "python": sys.executable,
        "browser_dir": str(PLAYWRIGHT_BROWSERS_DIR),
        "data_dir": str(DATA_DIR),
        "checks": checks,
    }


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        return os.access(directory, os.W_OK)
    except OSError:
        return False


def _has_project_browser() -> bool:
    if not PLAYWRIGHT_BROWSERS_DIR.is_dir():
        return False
    patterns = (
        "chromium-*/chrome-win/chrome.exe",
        "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
    )
    return any(any(PLAYWRIGHT_BROWSERS_DIR.glob(pattern)) for pattern in patterns)

from __future__ import annotations

from pathlib import Path

from app.config import DEFAULT_ARTIFACTS_DIR, DEFAULT_DATA_DIR

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = DEFAULT_DATA_DIR
UPLOADS_DIR = DATA_DIR / "uploads"
ASSETS_DIR = DATA_DIR / "assets"
SPARK_ASSETS_DIR = ASSETS_DIR / "sparks"
ENV_PATH = DATA_DIR / ".env.local"
CONFIG_PATH = DATA_DIR / "config.json"
CONVERSATIONS_PATH = DATA_DIR / "conversations.json"
STATE_PATH = DATA_DIR / "storage-state.json"
ACTIVITY_LOG_PATH = DATA_DIR / "activity-log.jsonl"
FEISHU_CONFIG_PATH = DATA_DIR / ".feishu.local.json"
FEISHU_APP_CONFIG_PATH = DATA_DIR / ".feishu-app.local.json"
FEISHU_HISTORY_PATH = DATA_DIR / "feishu-history.jsonl"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
PLAYWRIGHT_BROWSERS_DIR = RUNTIME_DIR / "ms-playwright"
PORTABLE_PYTHON_DIR = RUNTIME_DIR / "python"
ARTIFACTS_DIR = DEFAULT_ARTIFACTS_DIR
RESULT_PATH = ARTIFACTS_DIR / "result.json"
RUN_LOG_PATH = ARTIFACTS_DIR / "run.log"
HISTORY_PATH = ARTIFACTS_DIR / "history.json"

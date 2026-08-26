from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from web.paths import (
    CONFIG_PATH,
    CONVERSATIONS_PATH,
    DATA_DIR,
    ENV_PATH,
    PLAYWRIGHT_BROWSERS_DIR,
    RUNTIME_DIR,
    SPARK_ASSETS_DIR,
    STATE_PATH,
    UPLOADS_DIR,
)

DEFAULT_CONFIG: dict[str, Any] = {
    "task_id": "local-douyin-fire",
    "timezone": "Asia/Shanghai",
    "friends": [],
    "selected_conversations": [],
    "groups": [],
    "messages": [{"type": "text", "value": "续火花 ✨"}],
    "stickers": {},
    "send_interval_seconds": {"min": 45, "max": 120},
    "prevent_duplicates": True,
    "continue_on_error": True,
    "target_open_retries": 1,
    "target_open_timeout_seconds": 20,
    "runtime_paths": {
        "python": "",
        "browser": "",
    },
    "schedule": {
        "enabled": False,
        "times": ["09:15"],
        "random_jitter_seconds": 180,
        "entries": [
            {
                "group": "当前勾选",
                "hour": 9,
                "minute": 15,
                "content": "续火花 ✨",
                "time": "09:15",
                "interval_seconds": 20,
            }
        ],
    },
}

DEFAULT_ENV = {
    "HEADLESS": "false",
    "TRACE": "true",
}


def ensure_data_files() -> None:
    for directory in (
        DATA_DIR,
        UPLOADS_DIR,
        SPARK_ASSETS_DIR,
        RUNTIME_DIR,
        PLAYWRIGHT_BROWSERS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
    if not ENV_PATH.exists():
        save_env(DEFAULT_ENV)
    else:
        _migrate_default_storage_path()
    if not CONVERSATIONS_PATH.exists():
        CONVERSATIONS_PATH.write_text(
            json.dumps({"updated_at": None, "count": 0, "conversations": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def load_config() -> dict[str, Any]:
    ensure_data_files()
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(value, dict):
        return copy.deepcopy(DEFAULT_CONFIG)
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(value)
    config["messages"] = value.get("messages", config["messages"])
    config["stickers"] = value.get("stickers", config["stickers"])
    config["selected_conversations"] = value.get("selected_conversations", config["selected_conversations"])
    config["groups"] = value.get("groups", config.get("groups", []))
    config["runtime_paths"] = {**DEFAULT_CONFIG["runtime_paths"], **dict(value.get("runtime_paths", {}))}
    config["schedule"] = {**DEFAULT_CONFIG["schedule"], **dict(value.get("schedule", {}))}
    config["schedule"]["entries"] = value.get("schedule", {}).get("entries", config["schedule"]["entries"])
    entries = config["schedule"]["entries"]
    if isinstance(entries, list):
        config["schedule"]["entries"] = [
            {**entry, "group": entry.get("group") or "当前勾选"}
            for entry in entries
            if isinstance(entry, dict)
        ]
    send_interval = value.get("send_interval_seconds", {})
    if isinstance(send_interval, dict):
        current_min = send_interval.get("min", DEFAULT_CONFIG["send_interval_seconds"]["min"])
        current_max = send_interval.get("max", DEFAULT_CONFIG["send_interval_seconds"]["max"])
        if current_min == 3 and current_max == 8:
            current_min = DEFAULT_CONFIG["send_interval_seconds"]["min"]
            current_max = DEFAULT_CONFIG["send_interval_seconds"]["max"]
        config["send_interval_seconds"] = {
            "min": current_min,
            "max": current_max,
        }
    return config


def save_config(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = copy.deepcopy(DEFAULT_CONFIG)
    normalized.update(payload)
    normalized["messages"] = payload.get("messages", normalized["messages"])
    normalized["stickers"] = payload.get("stickers", normalized["stickers"])
    normalized["selected_conversations"] = payload.get("selected_conversations", normalized["selected_conversations"])
    normalized["groups"] = payload.get("groups", normalized.get("groups", []))
    normalized["runtime_paths"] = {**DEFAULT_CONFIG["runtime_paths"], **dict(payload.get("runtime_paths", {}))}
    normalized["schedule"] = {**DEFAULT_CONFIG["schedule"], **dict(payload.get("schedule", {}))}
    normalized["schedule"]["entries"] = dict(payload.get("schedule", {})).get("entries", normalized["schedule"]["entries"])
    entries = normalized["schedule"]["entries"]
    if isinstance(entries, list):
        normalized["schedule"]["entries"] = [
            {**entry, "group": entry.get("group") or "当前勾选"}
            for entry in entries
            if isinstance(entry, dict)
        ]
    selected = normalized.get("selected_conversations", [])
    normalized["friends"] = [item["name"] for item in selected if isinstance(item, dict) and item.get("enabled") and item.get("name")]
    CONFIG_PATH.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")


def load_env() -> dict[str, str]:
    ensure_data_files()
    env = dict(DEFAULT_ENV)
    if not ENV_PATH.exists():
        return env
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def save_env(env: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in env.items() if value is not None]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save_cookie_json(cookie_text: str) -> None:
    parsed = json.loads(cookie_text)
    if not isinstance(parsed, list):
        raise ValueError("Cookie 必须是 JSON 数组")
    env = load_env()
    env["DOUYIN_COOKIE"] = json.dumps(parsed, ensure_ascii=False)
    env.pop("DOUYIN_STORAGE_STATE", None)
    save_env(env)


def clear_cookie_json() -> None:
    env = load_env()
    env.pop("DOUYIN_COOKIE", None)
    save_env(env)


def set_storage_state(path: Path | None = None) -> None:
    env = load_env()
    target = (path or STATE_PATH).expanduser().resolve()
    if target == STATE_PATH.resolve():
        env.pop("DOUYIN_STORAGE_STATE", None)
    else:
        env["DOUYIN_STORAGE_STATE"] = str(target)
    env.pop("DOUYIN_COOKIE", None)
    save_env(env)


def _migrate_default_storage_path() -> None:
    """Remove stale absolute storage-state paths when the project default exists."""
    if not STATE_PATH.exists():
        return
    lines = ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    migrated: list[str] = []
    changed = False
    for line in lines:
        if not line.startswith("DOUYIN_STORAGE_STATE="):
            migrated.append(line)
            continue
        raw_path = line.split("=", 1)[1].strip()
        try:
            target = Path(raw_path).expanduser().resolve()
        except OSError:
            target = None
        if target == STATE_PATH.resolve() or target is None or not target.exists():
            changed = True
            continue
        migrated.append(line)
    if changed:
        ENV_PATH.write_text("\n".join(migrated).rstrip() + "\n", encoding="utf-8")


def auth_status() -> dict[str, Any]:
    env = load_env()
    has_state = STATE_PATH.exists()
    cookie_present = bool(env.get("DOUYIN_COOKIE"))
    return {
        "has_storage_state": has_state,
        "storage_state_path": str(STATE_PATH),
        "cookie_present": cookie_present,
        "env_path": str(ENV_PATH),
        "config_path": str(CONFIG_PATH),
    }

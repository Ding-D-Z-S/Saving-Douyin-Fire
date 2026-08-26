from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from web.paths import ACTIVITY_LOG_PATH

_LOCK = threading.RLock()
_MAX_BYTES = 2 * 1024 * 1024
_MAX_LINES = 2_000
_SECRET_PATTERNS = (
    re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9-]+", re.I),
    re.compile(r"(?i)(cookie|webhook|app[_ -]?secret)\s*[:=]\s*\S+"),
)


def record(
    message: str,
    *,
    level: str = "info",
    event: str = "general",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "level": level if level in {"info", "success", "warning", "error"} else "info",
        "event": event,
        "message": _redact(str(message).strip()),
        "context": _safe_context(context or {}),
    }
    with _LOCK:
        ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()
        with ACTIVITY_LOG_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def recent(limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(500, int(limit)))
    with _LOCK:
        if not ACTIVITY_LOG_PATH.exists():
            return []
        try:
            lines = ACTIVITY_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return []
    entries: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def tail_text(limit: int = 100) -> str:
    return "\n".join(
        f"[{_display_time(entry.get('time'))}] {entry.get('message', '')}"
        for entry in recent(limit)
    )


def clear() -> None:
    with _LOCK:
        ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        ACTIVITY_LOG_PATH.write_text("", encoding="utf-8")


def _rotate_if_needed() -> None:
    if not ACTIVITY_LOG_PATH.exists() or ACTIVITY_LOG_PATH.stat().st_size < _MAX_BYTES:
        return
    lines = ACTIVITY_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    ACTIVITY_LOG_PATH.write_text(
        "\n".join(lines[-(_MAX_LINES // 2):]) + "\n",
        encoding="utf-8",
    )


def _safe_context(context: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in context.items():
        normalized = str(key).lower()
        if any(secret in normalized for secret in ("cookie", "webhook", "secret", "token")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = _redact(str(value)) if isinstance(value, str) else value
    return result


def _redact(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub("[已隐藏敏感信息]", result)
    return result


def _display_time(value: Any) -> str:
    text = str(value or "")
    try:
        return datetime.fromisoformat(text).strftime("%m-%d %H:%M:%S")
    except ValueError:
        return text

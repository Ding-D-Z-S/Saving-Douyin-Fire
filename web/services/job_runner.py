from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from web.paths import (
    DATA_DIR,
    PLAYWRIGHT_BROWSERS_DIR,
    PORTABLE_PYTHON_DIR,
    PROJECT_ROOT,
    RESULT_PATH,
    RUN_LOG_PATH,
)
from web.services.activity_log import record, tail_text
from web.services.config_store import ensure_data_files, load_config
from web.services.feishu import notify_cookie_expired, notify_task

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "status": "idle",
    "mode": None,
    "pid": None,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "error": None,
    "metadata": {},
}
_PROCESS: subprocess.Popen[str] | None = None


def _python_executable() -> str:
    config = load_config()
    custom = str(config.get("runtime_paths", {}).get("python") or "").strip()
    candidates = []
    if custom:
        candidates.append(PROJECT_ROOT / custom)
    candidates.extend(
        (
            PORTABLE_PYTHON_DIR / "python.exe",
            PROJECT_ROOT / ".venv" / "Scripts" / "python.exe",
        )
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "python"


def build_interval_range(interval_seconds: float | None, member_count: int) -> dict[str, float]:
    """Convert a per-plan interval (seconds) into a jittered min/max range.

    A single-member group needs no inter-target wait, so it becomes 0. For larger
    groups the requested value is the centre of a +/- 20% band, clamped to keep the
    send cadence both faster than the old global default and non-mechanical.
    """
    if member_count <= 1:
        return {"min": 0.0, "max": 0.0}
    seconds = float(interval_seconds) if interval_seconds and interval_seconds > 0 else 20.0
    low = max(2.0, round(seconds * 0.8, 1))
    high = max(low, round(seconds * 1.2, 1))
    return {"min": low, "max": min(high, 300.0)}


def _prepare_runtime_config(
    override_messages: list[dict[str, Any]] | None = None,
    override_selected: list[dict[str, Any]] | None = None,
    override_task_id: str | None = None,
    override_interval_seconds: float | None = None,
) -> Path:
    """Build an isolated task file without changing the saved web configuration."""
    config = load_config()
    selected = override_selected if override_selected is not None else config.get("selected_conversations", [])
    enabled = [
        item
        for item in selected
        if isinstance(item, dict) and item.get("enabled") and item.get("name")
    ]
    config["selected_conversations"] = selected
    config["friends"] = [item["name"] for item in enabled]
    if override_task_id:
        config["task_id"] = override_task_id
    if override_messages is not None:
        config["messages"] = override_messages
    if override_interval_seconds is not None:
        config["send_interval_seconds"] = build_interval_range(override_interval_seconds, len(config["friends"]))
    if not config["friends"]:
        raise ValueError("没有可发送的会话成员")
    if not isinstance(config.get("messages"), list) or not config["messages"]:
        raise ValueError("没有可发送的消息内容")

    path = DATA_DIR / f".runtime-task-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _cleanup_runtime_config(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def start_run(
    dry_run: bool,
    override_messages: list[dict[str, Any]] | None = None,
    override_selected: list[dict[str, Any]] | None = None,
    override_task_id: str | None = None,
    override_interval_seconds: float | None = None,
    force_headful: bool = False,
    mode_label: str | None = None,
    metadata: dict[str, Any] | None = None,
    on_complete: Callable[[bool, str], None] | None = None,
) -> tuple[bool, str]:
    global _PROCESS
    ensure_data_files()
    meta = _normalized_metadata(metadata or {}, mode_label or ("检查" if dry_run else "发送"))
    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None:
            return False, "已有任务正在运行，请等待完成后再试"
        try:
            runtime_config = _prepare_runtime_config(
                override_messages=override_messages,
                override_selected=override_selected,
                override_task_id=override_task_id,
                override_interval_seconds=override_interval_seconds,
            )
        except (OSError, ValueError) as exc:
            message = str(exc)
            record(f"错误提示 error：任务无法启动：{message}", level="error", event="task.start.failed")
            return False, message

        command = [_python_executable(), "run.py"]
        if dry_run:
            command.append("--dry-run")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["TASK_CONFIG"] = str(runtime_config)
        runtime_paths = load_config().get("runtime_paths", {})
        custom_browser = str(runtime_paths.get("browser") or "").strip() if isinstance(runtime_paths, dict) else ""
        if custom_browser:
            env["BROWSER_PATH"] = str(PROJECT_ROOT / custom_browser)
        else:
            env.pop("BROWSER_PATH", None)
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(PLAYWRIGHT_BROWSERS_DIR)
        if force_headful:
            env["HEADLESS"] = "false"
        try:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError as exc:
            _cleanup_runtime_config(runtime_config)
            message = f"任务启动失败：{exc}"
            record(f"错误提示 error：{message}", level="error", event="task.start.failed")
            return False, message
        _PROCESS = process
        _STATE.update(
            {
                "status": "running",
                "mode": mode_label or ("check" if dry_run else "send"),
                "pid": process.pid,
                "started_at": datetime.now().astimezone().isoformat(),
                "finished_at": None,
                "exit_code": None,
                "error": None,
                "metadata": meta,
            }
        )
        record(_started_message(meta), event="task.started", context=meta)
        thread = threading.Thread(
            target=_wait_process,
            args=(process, runtime_config, meta, on_complete),
            daemon=True,
        )
        thread.start()
        return True, "任务已启动"


def _wait_process(
    process: subprocess.Popen[str],
    runtime_config: Path,
    metadata: dict[str, Any],
    on_complete: Callable[[bool, str], None] | None,
) -> None:
    global _PROCESS
    code = process.wait()
    _cleanup_runtime_config(runtime_config)
    error = _tail_log() if code != 0 else None
    success = code == 0
    message = _finished_message(metadata, success, error)
    with _LOCK:
        if _PROCESS is process:
            _PROCESS = None
        _STATE["finished_at"] = datetime.now().astimezone().isoformat()
        _STATE["exit_code"] = code
        _STATE["status"] = "success" if success else "failed"
        _STATE["error"] = error if not success else None
    record(
        message,
        level="success" if success else "error",
        event="task.succeeded" if success else "task.failed",
        context=metadata,
    )
    if not success and _is_repeated_auth_failure(error):
        notify_cookie_expired(3)
    else:
        notify_task(message)
    if on_complete:
        try:
            on_complete(success, message)
        except Exception as exc:
            record(
                f"错误提示 error：任务完成回调失败：{exc}",
                level="error",
                event="task.callback.failed",
            )


def _tail_log(limit: int = 40) -> str | None:
    if not RUN_LOG_PATH.exists():
        return None
    lines = RUN_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    return "\n".join(lines[-limit:])


def load_result() -> dict[str, Any] | None:
    if not RESULT_PATH.exists():
        return None
    try:
        value = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def status() -> dict[str, Any]:
    with _LOCK:
        payload = dict(_STATE)
    payload["log_path"] = str(RUN_LOG_PATH)
    payload["log_tail"] = tail_text(100)
    payload["result"] = load_result()
    return payload


def _normalized_metadata(metadata: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "kind": str(metadata.get("kind") or ("test" if "测试" in mode else "manual")),
        "group": str(metadata.get("group") or "当前勾选"),
        "time": str(metadata.get("time") or datetime.now().strftime("%H:%M")),
        "content": str(metadata.get("content") or "").strip()[:500],
        "mode": mode,
    }


def _started_message(metadata: dict[str, Any]) -> str:
    kind = metadata["kind"]
    group = metadata["group"]
    if kind == "scheduled":
        return f"已开始给{group}组发送 {metadata['time']} 的定时信息，信息内容：{metadata['content']}"
    if kind == "test":
        return f"{group}组测试信息已开始发送，信息内容：{metadata['content']}"
    return f"发送任务已开始：{metadata['mode']}"


def _finished_message(metadata: dict[str, Any], success: bool, error: str | None) -> str:
    kind = metadata["kind"]
    group = metadata["group"]
    if kind == "scheduled":
        prefix = "已成功" if success else "发送失败"
        base = f"{prefix}给{group}组发送 {metadata['time']} 的定时信息，信息内容：{metadata['content']}"
    elif kind == "test":
        base = f"{group}组测试信息发送{'成功' if success else '失败'}！信息内容：{metadata['content']}"
    else:
        base = f"任务{metadata['mode']}执行{'成功' if success else '失败'}"
    if not success:
        base = f"{base}；错误提示 error：{_concise_error(error)}"
    return base


def _concise_error(error: str | None) -> str:
    if not error:
        return "未知错误，请检查 Python、浏览器、网络和登录状态"
    lines = [line.strip() for line in error.splitlines() if line.strip()]
    return (lines[-1] if lines else error)[:500]


def _is_repeated_auth_failure(error: str | None) -> bool:
    text = str(error or "")
    return "AUTH_RETRY_EXHAUSTED" in text or "连续两次" in text and "登录" in text

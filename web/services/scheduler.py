from __future__ import annotations

import hashlib
import queue
import random
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from web.services.activity_log import record
from web.services.config_store import load_config
from web.services.job_runner import start_run, status as job_status

_THREAD: threading.Thread | None = None
_WORKER: threading.Thread | None = None
_STOP = threading.Event()
_LAST_ENQUEUED: set[str] = set()
_PENDING_KEYS: set[str] = set()
_QUEUE: queue.Queue["ScheduledJob | None"] = queue.Queue()
_LOCK = threading.Lock()


@dataclass(frozen=True)
class ScheduledJob:
    run_key: str
    group_name: str
    content: str
    selected: list[dict[str, Any]]
    jitter_seconds: int
    task_id: str
    schedule_time: str = ""
    interval_seconds: float | None = None


def start_scheduler() -> None:
    global _THREAD, _WORKER
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, daemon=True, name="douyin-scheduler")
        _WORKER = threading.Thread(target=_worker_loop, daemon=True, name="douyin-scheduler-worker")
        _THREAD.start()
        _WORKER.start()


def stop_scheduler() -> None:
    _STOP.set()
    _QUEUE.put(None)


def _loop() -> None:
    while not _STOP.is_set():
        try:
            _tick()
        except Exception as exc:
            record(
                f"错误提示 error：定时计划检查失败：{exc}",
                level="error",
                event="scheduler.tick.failed",
            )
        _STOP.wait(20)


def _tick(now: datetime | None = None) -> None:
    config = load_config()
    schedule = config.get("schedule", {}) if isinstance(config, dict) else {}
    if not schedule or not schedule.get("enabled"):
        return
    timezone = config.get("timezone", "Asia/Shanghai")
    current_time = now or datetime.now(ZoneInfo(timezone))
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=ZoneInfo(timezone))
    current = current_time.strftime("%H:%M")
    day_key = current_time.date().isoformat()
    jitter = _safe_jitter(schedule.get("random_jitter_seconds", 180))

    entries = schedule.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or _entry_time(entry) != current:
            continue
        group_name = str(entry.get("group") or "当前勾选").strip()
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        fingerprint = hashlib.sha1(
            f"{index}:{group_name}:{content}".encode("utf-8")
        ).hexdigest()[:12]
        run_key = f"{day_key}:{current}:{fingerprint}"
        with _LOCK:
            if run_key in _LAST_ENQUEUED or run_key in _PENDING_KEYS:
                continue
            _PENDING_KEYS.add(run_key)
        try:
            members = _resolve_members(config, group_name)
            selected = _selected_for_names(config, members)
            if not selected:
                record(
                    f"错误提示 error：{group_name}组没有可发送的会话成员，已跳过 {current} 的定时信息",
                    level="error",
                    event="schedule.empty-group",
                    context={"group": group_name, "time": current},
                )
                _finish_key(run_key, completed=True)
                continue
            task_id = str(config.get("task_id") or "local-douyin-fire")
            scheduled_task_id = f"{task_id}-schedule-{current.replace(':', '')}-{fingerprint}"
            entry_interval = _safe_interval(entry.get("interval_seconds"))
            _QUEUE.put(
                ScheduledJob(
                    run_key=run_key,
                    group_name=group_name,
                    content=content,
                    selected=selected,
                    jitter_seconds=jitter,
                    task_id=scheduled_task_id,
                    schedule_time=current,
                    interval_seconds=entry_interval,
                )
            )
        except Exception as exc:
            record(
                f"错误提示 error：无法创建{group_name}组 {current} 的定时任务：{exc}",
                level="error",
                event="schedule.enqueue.failed",
            )
            _finish_key(run_key, completed=True)

    with _LOCK:
        for item in list(_LAST_ENQUEUED):
            if not item.startswith(f"{day_key}:"):
                _LAST_ENQUEUED.remove(item)


def _worker_loop() -> None:
    while not _STOP.is_set():
        job = _QUEUE.get()
        if job is None:
            _QUEUE.task_done()
            return
        try:
            if job.jitter_seconds and _STOP.wait(random.uniform(0, job.jitter_seconds)):
                _finish_key(job.run_key, completed=False)
                return
            while not _STOP.is_set():
                ok, message = start_run(
                    False,
                    override_messages=[{"type": "text", "value": job.content}],
                    override_selected=job.selected,
                    override_task_id=job.task_id,
                    override_interval_seconds=job.interval_seconds,
                    mode_label=f"定时发送 · {job.group_name}",
                    metadata={
                        "kind": "scheduled",
                        "group": job.group_name,
                        "time": job.schedule_time,
                        "content": job.content,
                    },
                )
                if ok:
                    _finish_key(job.run_key, completed=True)
                    while not _STOP.is_set():
                        if job_status().get("status") != "running":
                            break
                        _STOP.wait(5)
                    break
                if "正在运行" not in message:
                    _finish_key(job.run_key, completed=True)
                    break
                _STOP.wait(5)
        finally:
            _QUEUE.task_done()


def _finish_key(run_key: str, *, completed: bool) -> None:
    with _LOCK:
        _PENDING_KEYS.discard(run_key)
        if completed:
            _LAST_ENQUEUED.add(run_key)


def _entry_time(entry: dict[str, Any]) -> str:
    try:
        hour = max(0, min(23, int(entry.get("hour", 0))))
        minute = max(0, min(59, int(entry.get("minute", 0))))
    except (TypeError, ValueError):
        return str(entry.get("time") or "")
    return f"{hour:02d}:{minute:02d}"


def _safe_jitter(value: Any) -> int:
    try:
        return max(0, min(3600, int(value)))
    except (TypeError, ValueError):
        return 180


def _safe_interval(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds is None or seconds <= 0 or seconds > 3600:
        return None
    return seconds


def _resolve_members(config: dict[str, Any], group_name: str) -> list[str]:
    if group_name in {"当前勾选", "默认分组"}:
        return [
            item["name"]
            for item in config.get("selected_conversations", [])
            if isinstance(item, dict) and item.get("enabled") and item.get("name")
        ]
    for group in config.get("groups", []):
        if isinstance(group, dict) and group.get("name") == group_name:
            return [
                name.strip()
                for name in group.get("members", [])
                if isinstance(name, str) and name.strip()
            ]
    return []


def _selected_for_names(config: dict[str, Any], names: list[str]) -> list[dict[str, Any]]:
    by_name: dict[str, dict[str, Any]] = {}
    for source in config.get("selected_conversations", []):
        if isinstance(source, dict) and source.get("name"):
            by_name.setdefault(source["name"], source)
    result: list[dict[str, Any]] = []
    for name in dict.fromkeys(names):
        item = dict(by_name.get(name, {"name": name, "kind": "contact"}))
        item["enabled"] = True
        result.append(item)
    return result

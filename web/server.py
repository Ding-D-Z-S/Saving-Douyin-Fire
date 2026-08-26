from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for

from app.browser import AuthenticationError, RiskControlError, open_douyin, open_private_messages, verify_login
from app.config import ConfigError, load_settings
from app.conversations import load_conversations, scrape_conversations
from web.paths import ARTIFACTS_DIR, PROJECT_ROOT, RUN_LOG_PATH, SPARK_ASSETS_DIR
from web.services.activity_log import clear as clear_activity_log
from web.services.activity_log import record
from web.services.auth_capture import capture_login_state
from web.services.config_store import (
    auth_status,
    clear_cookie_json,
    ensure_data_files,
    load_config,
    load_env,
    save_config,
    save_cookie_json,
    save_env,
)
from web.services.feishu import (
    clear_app_credentials,
    load_history as load_feishu_history,
    save_app_credentials,
    send_default_message,
    send_text as send_feishu_text,
    start_long_connection,
    stop_long_connection,
    webhook_status,
)
from web.services.job_runner import start_run, status as job_status
from web.services.scheduler import start_scheduler
from web.services.self_check import run_self_check

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH_AT = 0.0
_REFRESH_COOLDOWN_SECONDS = 30


def _flash_message(message: str, level: str = "info") -> dict[str, str]:
    return {"message": message, "level": level}


def _render_index(flash: dict[str, str] | None = None):
    payload = job_status()
    return render_template(
        "index.html",
        auth=auth_status(),
        config=load_config(),
        env=load_env(),
        job=payload,
        result=payload.get("result"),
        artifacts_dir=str(ARTIFACTS_DIR),
        self_check=run_self_check(),
        flash=flash,
    )


def _build_schedule_entries(form) -> list[dict]:
    groups = form.getlist("schedule_group")
    hours = form.getlist("schedule_hour")
    minutes = form.getlist("schedule_minute")
    intervals = form.getlist("schedule_interval")
    contents = form.getlist("schedule_content")
    entries: list[dict] = []
    for index, (group, hour, minute, content) in enumerate(zip(groups, hours, minutes, contents)):
        group = group.strip() or "当前勾选"
        content = content.strip()
        if not content:
            continue
        hour_value = max(0, min(23, int(hour)))
        minute_value = max(0, min(59, int(minute)))
        interval = _parse_interval(intervals[index]) if index < len(intervals) else None
        entries.append(
            {
                "group": group,
                "hour": hour_value,
                "minute": minute_value,
                "content": content,
                "time": f"{hour_value:02d}:{minute_value:02d}",
                "interval_seconds": interval,
            }
        )
    return entries


def _parse_interval(value: str) -> float | None:
    """Parse a per-plan send interval in seconds; empty means 'use default'."""
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise ValueError("发送间隔必须是数字（秒）") from exc
    if seconds < 0 or seconds > 3600:
        raise ValueError("发送间隔需在 0 到 3600 秒之间")
    return seconds


def _build_selected_conversations(
    cached: list[dict],
    selected_ids: set[str],
    selected_names: set[str] | None = None,
) -> list[dict]:
    selected_names = selected_names or set()
    result: list[dict] = []
    for item in cached:
        item_id = item.get("id")
        if not item_id:
            continue
        result.append(
            {
                "id": item_id,
                "name": item.get("name"),
                "kind": item.get("kind", "contact"),
                "avatar": item.get("avatar"),
                "has_spark": bool(item.get("has_spark")),
                "spark_days": item.get("spark_days"),
                "spark_label": item.get("spark_label"),
                "spark_native_label": item.get("spark_native_label"),
                "spark_icon_url": item.get("spark_icon_url"),
                "spark_icon_local": item.get("spark_icon_local"),
                "spark_mode": item.get("spark_mode"),
                "spark_is_broken": bool(item.get("spark_is_broken")),
                "spark_will_expire_in_days": item.get("spark_will_expire_in_days"),
                "spark_reignite_progress": item.get("spark_reignite_progress"),
                "spark_detail": item.get("spark_detail"),
                "sidebar_time": item.get("sidebar_time"),
                "enabled": item_id in selected_ids or item.get("name") in selected_names,
            }
        )
    return result


def _selected_names(config: dict) -> list[str]:
    return [
        item.get("name")
        for item in config.get("selected_conversations", [])
        if item.get("enabled") and item.get("name")
    ]


def _selected_for_names(config: dict, names: list[str]) -> list[dict]:
    cached = load_conversations().get("conversations", [])
    by_name: dict[str, dict] = {}
    for source in [*cached, *config.get("selected_conversations", [])]:
        name = source.get("name") if isinstance(source, dict) else None
        if name and name not in by_name:
            by_name[name] = source

    selected: list[dict] = []
    for name in dict.fromkeys(names):
        source = by_name.get(name, {"name": name, "kind": "contact"})
        item = dict(source)
        item["name"] = name
        item["enabled"] = True
        selected.append(item)
    return selected


def _resolve_group_members(config: dict, group_name: str) -> list[str]:
    if group_name in {"当前勾选", "默认分组"}:
        return _selected_names(config)
    for group in config.get("groups", []):
        if group.get("name") == group_name:
            return [
                name.strip()
                for name in group.get("members", [])
                if isinstance(name, str) and name.strip()
            ]
    raise ValueError(f"找不到分组：{group_name}")


@app.before_request
def require_local_request():
    if request.remote_addr not in {"127.0.0.1", "::1"}:
        abort(403)


def _project_relative_runtime_path(value: str, label: str) -> str:
    text = value.strip()
    if not text:
        return ""
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    try:
        candidate = candidate.resolve()
        relative = candidate.relative_to(PROJECT_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label}必须位于当前 Fire with dy 项目目录内") from exc
    if not candidate.is_file():
        raise ValueError(f"{label}文件不存在：{relative}")
    return str(relative)


@app.post("/runtime-paths")
def save_runtime_paths():
    try:
        config = load_config()
        config["runtime_paths"] = {
            "python": _project_relative_runtime_path(request.form.get("python_path", ""), "Python 环境"),
            "browser": _project_relative_runtime_path(request.form.get("browser_path", ""), "浏览器环境"),
        }
        save_config(config)
        record("运行环境位置已保存，仍限制在当前项目目录内", level="success", event="runtime-paths.saved")
        return redirect(url_for("index", message="运行环境位置已保存", started="1"))
    except Exception as exc:
        record(f"错误提示 error：运行环境位置保存失败：{exc}", level="error", event="runtime-paths.failed")
        return redirect(url_for("index", message=str(exc), started="0"))


@app.get("/")
def index():
    ensure_data_files()
    message = request.args.get("message", "").strip()
    started = request.args.get("started")
    flash = None
    if message:
        flash = _flash_message(message, "success" if started == "1" else "error")
    return _render_index(flash=flash)


@app.get("/config")
def config_page():
    ensure_data_files()
    message = request.args.get("message", "").strip()
    flash = _flash_message(message, "success" if request.args.get("cleared") == "1" else "error") if message else None
    return render_template(
        "config.html",
        config=load_config(),
        env=load_env(),
        conversations=load_conversations(),
        job=job_status(),
        flash=flash,
    )


@app.post("/config")
def save_config_page():
    ensure_data_files()
    try:
        config = load_config()
        cached_conversations = load_conversations().get("conversations", [])
        selected_ids = set(request.form.getlist("selected_conversation_ids"))
        selected = _build_selected_conversations(cached_conversations, selected_ids)
        form_action = request.form.get("form_action", "save_plan")

        if form_action == "create_group":
            group_name = request.form.get("group_name", "").strip()
            if not group_name:
                raise ValueError("分组名称不能为空")
            if group_name in {"当前勾选", "默认分组"}:
                raise ValueError("这个名称是系统保留名称，请换一个分组名称")
            members = [item.get("name") for item in selected if item.get("enabled") and item.get("name")]
            if not members:
                raise ValueError("请先勾选会话再创建分组")
            groups = [group for group in config.get("groups", []) if group.get("name") != group_name]
            groups.append({"name": group_name, "members": members})
            config["groups"] = groups
            config["selected_conversations"] = selected
            save_config(config)
            record(
                f"已创建分组：{group_name}，分组人数：{len(members)} 人",
                level="success",
                event="group.created",
                context={"group": group_name, "member_count": len(members)},
            )
            flash = _flash_message(f"已创建分组：{group_name}", "success")
            return render_template(
                "config.html",
                config=load_config(),
                env=load_env(),
                conversations=load_conversations(),
                job=job_status(),
                flash=flash,
            )

        schedule_entries = _build_schedule_entries(request.form)
        if not schedule_entries:
            raise ValueError("至少添加一个发送计划")
        selected_names = [item.get("name") for item in selected if item.get("enabled") and item.get("name")]
        named_groups = {
            group.get("name"): group.get("members", [])
            for group in config.get("groups", [])
            if isinstance(group, dict) and group.get("name")
        }
        for entry in schedule_entries:
            group_name = entry["group"]
            if group_name in {"当前勾选", "默认分组"}:
                if not selected_names:
                    raise ValueError("使用“当前勾选”的计划至少要勾选一个发送对象")
                continue
            members = named_groups.get(group_name)
            if not members:
                raise ValueError(f"分组“{group_name}”不存在或没有成员")

        payload = {
            **config,
            "task_id": request.form.get("task_id", config.get("task_id", "local-douyin-fire")).strip() or "local-douyin-fire",
            "timezone": request.form.get("timezone", config.get("timezone", "Asia/Shanghai")).strip() or "Asia/Shanghai",
            "selected_conversations": selected,
            "messages": [{"type": "text", "value": schedule_entries[0]["content"]}],
            "send_interval_seconds": {
                "min": float(request.form.get("interval_min", config.get("send_interval_seconds", {}).get("min", 45)) or 45),
                "max": float(request.form.get("interval_max", config.get("send_interval_seconds", {}).get("max", 120)) or 120),
            },
            "prevent_duplicates": request.form.get("prevent_duplicates") == "on",
            "continue_on_error": request.form.get("continue_on_error") == "on",
            "target_open_retries": int(request.form.get("target_open_retries", config.get("target_open_retries", 1)) or 1),
            "target_open_timeout_seconds": float(request.form.get("target_open_timeout_seconds", config.get("target_open_timeout_seconds", 20)) or 20),
            "schedule": {
                "enabled": request.form.get("schedule_enabled") == "on",
                "times": [entry["time"] for entry in schedule_entries],
                "random_jitter_seconds": int(request.form.get("schedule_jitter", config.get("schedule", {}).get("random_jitter_seconds", 180)) or 180),
                "entries": schedule_entries,
            },
        }
        save_config(payload)
        env = load_env()
        env["HEADLESS"] = "true" if request.form.get("headless") == "on" else "false"
        env["TRACE"] = "true" if request.form.get("trace") == "on" else "false"
        save_env(env)
        record(
            f"每日定时计划已保存：共 {len(schedule_entries)} 条，{'已启用' if payload['schedule']['enabled'] else '未启用'}",
            level="success",
            event="schedule.saved",
            context={"entry_count": len(schedule_entries), "enabled": payload["schedule"]["enabled"]},
        )
        flash = _flash_message("配置已保存", "success")
    except Exception as exc:
        record(f"错误提示 error：保存配置失败：{exc}", level="error", event="config.save.failed")
        flash = _flash_message(f"保存失败：{exc}", "error")
    return render_template(
        "config.html",
        config=load_config(),
        env=load_env(),
        conversations=load_conversations(),
        job=job_status(),
        flash=flash,
    )


@app.post("/groups/delete")
def delete_group():
    config = load_config()
    name = request.form.get("group_name", "").strip()
    config["groups"] = [group for group in config.get("groups", []) if group.get("name") != name]
    save_config(config)
    record(f"已删除分组：{name}", level="success", event="group.deleted", context={"group": name})
    return redirect(url_for("results_page"))


@app.post("/conversations/refresh")
def refresh_conversations():
    global _LAST_REFRESH_AT
    config = load_config()
    preserve_selection = request.form.get("preserve_selection") == "1"
    selected_ids = set(request.form.getlist("selected_conversation_ids")) if preserve_selection else set()
    submitted_names = {
        name.strip()
        for name in request.form.getlist("selected_conversation_names")
        if preserve_selection and name.strip()
    }
    previous_names = {
        item.get("name")
        for item in config.get("selected_conversations", [])
        if preserve_selection
        and request.form.get("live_selection_submitted") != "1"
        and item.get("enabled")
        and item.get("name")
    }
    now = time.monotonic()
    if not _REFRESH_LOCK.acquire(blocking=False):
        flash = _flash_message("会话列表正在温和读取，请勿重复点击", "error")
    elif now - _LAST_REFRESH_AT < _REFRESH_COOLDOWN_SECONDS:
        remaining = max(1, int(_REFRESH_COOLDOWN_SECONDS - (now - _LAST_REFRESH_AT)))
        _REFRESH_LOCK.release()
        flash = _flash_message(f"为降低抖音风控，请 {remaining} 秒后再刷新", "error")
    else:
        try:
            refreshed = asyncio.run(scrape_conversations())
            _LAST_REFRESH_AT = time.monotonic()
            if preserve_selection:
                config["selected_conversations"] = _build_selected_conversations(
                    refreshed.get("conversations", []),
                    selected_ids,
                    previous_names | submitted_names,
                )
                save_config(config)
            count = int(refreshed.get("count") or 0)
            record(
                f"读取列表成功：已完成读取列表，列表人数：{count} 人",
                level="success",
                event="conversations.refreshed",
                context={"count": count},
            )
            flash = _flash_message(f"已完成读取列表，列表人数：{count} 人", "success")
        except Exception as exc:
            record(
                f"错误提示 error：读取会话列表失败：{exc}",
                level="error",
                event="conversations.refresh.failed",
            )
            flash = _flash_message(f"刷新会话列表失败：{exc}", "error")
        finally:
            _REFRESH_LOCK.release()
    return render_template(
        "config.html",
        config=load_config(),
        env=load_env(),
        conversations=load_conversations(),
        job=job_status(),
        flash=flash,
    )


@app.post("/conversations/auto-select-spark")
def auto_select_spark():
    config = load_config()
    cached = load_conversations().get("conversations", [])
    selected = []
    for item in cached:
        selected.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "kind": item.get("kind", "contact"),
                "avatar": item.get("avatar"),
                "has_spark": bool(item.get("has_spark")),
                "spark_days": item.get("spark_days"),
                "spark_label": item.get("spark_label"),
                "spark_native_label": item.get("spark_native_label"),
                "spark_icon_url": item.get("spark_icon_url"),
                "spark_icon_local": item.get("spark_icon_local"),
                "spark_mode": item.get("spark_mode"),
                "spark_is_broken": bool(item.get("spark_is_broken")),
                "spark_will_expire_in_days": item.get("spark_will_expire_in_days"),
                "spark_reignite_progress": item.get("spark_reignite_progress"),
                "spark_detail": item.get("spark_detail"),
                "sidebar_time": item.get("sidebar_time"),
                "enabled": bool(item.get("has_spark")),
            }
        )
    config["selected_conversations"] = selected
    save_config(config)
    return render_template(
        "config.html",
        config=load_config(),
        env=load_env(),
        conversations=load_conversations(),
        job=job_status(),
        flash=_flash_message("已自动勾选带火花的会话", "success"),
    )


@app.get("/api/conversations")
def api_conversations():
    return jsonify(load_conversations())


@app.post("/auth/cookie")
def import_cookie():
    try:
        save_cookie_json(request.form.get("cookie_json", "").strip())
        flash = _flash_message("Cookie 已保存", "success")
    except Exception as exc:
        flash = _flash_message(f"Cookie 保存失败：{exc}", "error")
    return _render_index(flash=flash)


@app.post("/auth/cookie/clear")
def clear_cookie():
    clear_cookie_json()
    return _render_index(flash=_flash_message("Cookie 已清除", "success"))


@app.post("/auth/capture")
def capture_auth():
    try:
        capture_login_state()
        flash = _flash_message("登录状态已保存到 storage-state.json", "success")
    except Exception as exc:
        flash = _flash_message(f"获取登录状态失败：{exc}", "error")
    return _render_index(flash=flash)


async def _verify_auth_async() -> None:
    settings = load_settings()
    async with open_douyin(settings) as session:
        await open_private_messages(session.page)
        await verify_login(session.page)


@app.post("/auth/verify")
def verify_auth():
    try:
        asyncio.run(_verify_auth_async())
        flash = _flash_message("登录状态验证成功", "success")
    except (AuthenticationError, RiskControlError, ConfigError) as exc:
        flash = _flash_message(f"登录状态验证失败：{exc}", "error")
    except Exception as exc:
        flash = _flash_message(f"验证异常：{exc}", "error")
    return _render_index(flash=flash)


@app.post("/run/dry")
def run_dry():
    ok, message = start_run(True)
    return redirect(url_for("index", started="1" if ok else "0", message=message))


@app.post("/run/send")
def run_send():
    ok, message = start_run(False)
    return redirect(url_for("index", started="1" if ok else "0", message=message))


@app.post("/config/test-send")
def config_test_send():
    content = request.form.get("single_content", "").strip()
    group_name = request.form.get("single_group", "当前勾选").strip() or "当前勾选"
    config = load_config()
    if group_name in {"当前勾选", "默认分组"} and request.form.get("current_selection_submitted") == "1":
        selected_ids = set(request.form.getlist("selected_conversation_ids"))
        live_selected = _build_selected_conversations(
            load_conversations().get("conversations", []),
            selected_ids,
        )
        config["selected_conversations"] = live_selected
    if not content:
        ok, message = False, "测试发送内容不能为空"
    else:
        try:
            members = _resolve_group_members(config, group_name)
            if not members:
                raise ValueError(f"分组“{group_name}”中没有会话成员")
            selected = _selected_for_names(config, members)
            override_interval = _parse_interval(request.form.get("single_interval", ""))
            ok, message = start_run(
                False,
                override_messages=[{"type": "text", "value": content}],
                override_selected=selected,
                override_task_id=f"{config.get('task_id', 'local-douyin-fire')}-test-{uuid.uuid4().hex[:12]}",
                override_interval_seconds=override_interval,
                force_headful=True,
                mode_label=f"测试发送 · {group_name}",
                metadata={
                    "kind": "test",
                    "group": group_name,
                    "time": datetime.now().strftime("%H:%M"),
                    "content": content,
                },
            )
            if ok:
                message = f"已打开可见测试浏览器，将立即发送给“{group_name}”（{len(members)} 人）"
        except ValueError as exc:
            ok, message = False, str(exc)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": ok, "message": message}), 200 if ok else 400
    return render_template(
        "config.html",
        config=load_config(),
        env=load_env(),
        conversations=load_conversations(),
        job=job_status(),
        flash=_flash_message(message, "success" if ok else "error"),
    )


@app.get("/data-assets/sparks/<path:filename>")
def spark_asset(filename: str):
    return send_from_directory(SPARK_ASSETS_DIR, filename)


@app.post("/logs/clear")
def clear_logs():
    clear_activity_log()
    try:
        RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        RUN_LOG_PATH.write_text("", encoding="utf-8")
    except OSError as exc:
        return redirect(url_for("config_page", message=f"低层日志清空失败：{exc}"))
    return redirect(url_for("config_page", message="日志已清空", cleared="1"))


@app.get("/feishu")
def feishu_page():
    message = request.args.get("message", "").strip()
    sent = request.args.get("sent")
    flash = _flash_message(message, "success" if sent == "1" else "error") if message else None
    return render_template(
        "feishu.html",
        feishu=webhook_status(),
        history=load_feishu_history(100),
        flash=flash,
    )


@app.post("/feishu/app")
def save_feishu_app():
    app_id = request.form.get("app_id", "").strip()
    app_secret = request.form.get("app_secret", "").strip()
    allowed_ids = [item.strip() for item in request.form.get("allowed_ids", "").splitlines() if item.strip()]
    try:
        save_app_credentials(app_id, app_secret, allowed_open_ids=allowed_ids, allowed_chat_ids=allowed_ids)
        start_long_connection()
        message, sent = "飞书应用凭据已安全保存，长连接已尝试建立", "1"
    except Exception as exc:
        message, sent = f"保存失败：{exc}", "0"
    return redirect(url_for("feishu_page", message=message, sent=sent))


@app.post("/feishu/app/clear")
def clear_feishu_app():
    try:
        clear_app_credentials()
        message, sent = "飞书应用配置已清除", "1"
    except Exception as exc:
        message, sent = f"清除失败：{exc}", "0"
    return redirect(url_for("feishu_page", message=message, sent=sent))


@app.post("/feishu/send")
def send_feishu_message():
    content = request.form.get("message", "").strip()
    try:
        send_feishu_text(content, direction="outbound")
        message, sent = "消息已发送到飞书", "1"
    except Exception as exc:
        message, sent = str(exc), "0"
    return redirect(url_for("feishu_page", message=message, sent=sent))


@app.post("/feishu/send-default")
def send_feishu_default():
    try:
        send_default_message()
        message, sent = "默认使用说明已发送到飞书", "1"
    except Exception as exc:
        message, sent = str(exc), "0"
    return redirect(url_for("feishu_page", message=message, sent=sent))


@app.get("/api/feishu/history")
def api_feishu_history():
    return jsonify({"history": load_feishu_history(100), "status": webhook_status()})


@app.get("/api/status")
def api_status():
    return jsonify(job_status())


@app.get("/results")
def results_page():
    payload = job_status()
    screenshots: list[str] = []
    screenshot_dir = ARTIFACTS_DIR / "screenshots"
    if screenshot_dir.exists():
        screenshots = [path.name for path in sorted(screenshot_dir.glob("*.png"), reverse=True)]
    traces: list[str] = []
    trace_dir = ARTIFACTS_DIR / "traces"
    if trace_dir.exists():
        traces = [path.name for path in sorted(trace_dir.glob("*.zip"), reverse=True)]
    return render_template(
        "results.html",
        job=payload,
        result=payload.get("result"),
        screenshots=screenshots,
        traces=traces,
        artifacts_dir=str(ARTIFACTS_DIR),
        config=load_config(),
    )


@app.get("/health")
def health():
    return jsonify({"ok": True, "project_root": str(PROJECT_ROOT)})


if __name__ == "__main__":
    ensure_data_files()
    start_scheduler()
    start_long_connection()
    app.run(host="127.0.0.1", port=6161, debug=False)

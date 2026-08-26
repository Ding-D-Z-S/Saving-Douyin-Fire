from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from web.paths import FEISHU_APP_CONFIG_PATH, FEISHU_CONFIG_PATH, FEISHU_HISTORY_PATH
from web.services.activity_log import recent, record

DEFAULT_MESSAGE = """抖音续火花控制台使用说明

0：返回最近 20 条本地运行日志。
1：返回目前已启用的每日定时计划，包含分组、时间和发送内容。
2：返回当前保存的分组及成员。

每次测试或定时任务成功、失败后，系统都会通过应用机器人回复发送结果日志。
如果抖音 Cookie/登录状态连续两次检测为过期，系统会连续发送三条过期提示。

在飞书里直接给本机器人发送 0、1 或 2 即可执行对应命令。"""

_LOCK = threading.RLock()
_ALLOWED_HOST = "open.feishu.cn"
_ALLOWED_PATH_PREFIX = "/open-apis/bot/v2/hook/"
_MAX_HISTORY_LINES = 500

# 应用凭据 / 长连接状态
_APP_LOCK = threading.RLock()
_CONNECTION_CLIENT: Any = None
_CONNECTION_THREAD: threading.Thread | None = None
_CONNECTION_STARTED_EVER = False
_CONNECTION_STATE = "未配置"
_TOKEN_CACHE: dict[str, Any] = {}
_SEEN_EVENTS: set[str] = set()
_SEEN_EVENTS_ORDER: list[str] = []
_SEEN_EVENTS_LIMIT = 300
_NEXT_SEND_AT = 0.0
_SEND_COOLDOWN_SECONDS = 0.4
_DEDUP_WINDOW_SECONDS = 120

_FEISHU_HOST = "https://open.feishu.cn"
_TOKEN_URL = f"{_FEISHU_HOST}/open-apis/auth/v3/tenant_access_token/internal"
_SEND_URL = f"{_FEISHU_HOST}/open-apis/im/v1/messages"
_REPLY_URL_PREFIX = f"{_FEISHU_HOST}/open-apis/im/v1/messages/"


def save_webhook(webhook: str) -> None:
    value = webhook.strip()
    if not _valid_webhook(value):
        raise ValueError("请输入有效的飞书自定义机器人 Webhook")
    FEISHU_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        FEISHU_CONFIG_PATH.write_text(
            json.dumps({"webhook": value}, ensure_ascii=False),
            encoding="utf-8",
        )


def clear_webhook() -> None:
    with _LOCK:
        try:
            FEISHU_CONFIG_PATH.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(f"无法清除飞书配置：{exc}") from exc


def webhook_status() -> dict[str, Any]:
    value = _load_webhook()
    app = _load_app_credentials()
    app_configured = bool(app.get("app_id") and app.get("app_secret"))
    if app_configured:
        inbound_ready = connection_status() == "已连接"
        inbound_status = (
            "已连接，可通过飞书发送 0/1/2 执行命令"
            if inbound_ready
            else connection_status()
        )
    else:
        inbound_ready = False
        inbound_status = "等待飞书应用 App ID / App Secret 与消息长连接配置"
    return {
        "configured": bool(value),
        "masked": mask_webhook(value) if value else "未配置",
        "inbound_ready": inbound_ready,
        "inbound_status": inbound_status,
        "app": {
            "configured": app_configured,
            "masked_app_id": mask_app_id(app.get("app_id", "")) if app_configured else "未配置",
            "connection_state": connection_status(),
            "allowed_ids": list(app.get("allowed_open_ids", [])) + list(app.get("allowed_chat_ids", [])),
        },
    }


def mask_webhook(webhook: str) -> str:
    if not webhook:
        return ""
    suffix = webhook.rsplit("/", 1)[-1]
    visible = suffix[-4:] if len(suffix) >= 4 else "****"
    return f"https://open.feishu.cn/open-apis/bot/v2/hook/****{visible}"


def send_text(message: str, *, direction: str = "outbound", retries: int = 1) -> None:
    text = message.strip()
    if not text:
        raise ValueError("飞书消息内容不能为空")
    last_error: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            _send_via_preferred(text)
            append_history(text, direction=direction, status="success")
            return
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.0)
    append_history(text, direction=direction, status="failed", error=str(last_error or "未知错误"))
    raise RuntimeError(f"飞书消息发送失败：{last_error}") from last_error


def _send_via_preferred(text: str) -> None:
    """用应用机器人向最近会话发送。需要先在飞书给机器人发过任意 0/1/2 以记住会话。"""
    app = _load_app_credentials()
    target = _best_reply_target(app)
    if not (app.get("app_id") and app.get("app_secret") and target):
        raise ValueError("请先在飞书里给本机器人发送任意一条 0/1/2，以记住回复会话")
    _app_bot_send_text(text, target[0], target[1])


def send_default_message() -> None:
    send_text(DEFAULT_MESSAGE, direction="outbound")


def _any_outbound_channel() -> bool:
    app = _load_app_credentials()
    return bool(app.get("app_id") and app.get("app_secret") and _best_reply_target(app))


def save_app_credentials(
    app_id: str,
    app_secret: str,
    allowed_open_ids: list[str] | None = None,
    allowed_chat_ids: list[str] | None = None,
) -> None:
    app_id_value = (app_id or "").strip()
    app_secret_value = (app_secret or "").strip()
    if not app_id_value:
        raise ValueError("App ID 不能为空")
    if not app_secret_value:
        raise ValueError("App Secret 不能为空")
    if not app_id_value.startswith("cli_"):
        raise ValueError("App ID 应以 cli_ 开头")
    if len(app_secret_value) < 8:
        raise ValueError("App Secret 长度不足")
    FEISHU_APP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "app_id": app_id_value,
        "app_secret": app_secret_value,
        "allowed_open_ids": [item.strip() for item in (allowed_open_ids or []) if item.strip()],
        "allowed_chat_ids": [item.strip() for item in (allowed_chat_ids or []) if item.strip()],
    }
    with _APP_LOCK:
        FEISHU_APP_CONFIG_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear_app_credentials() -> None:
    with _APP_LOCK:
        try:
            FEISHU_APP_CONFIG_PATH.unlink(missing_ok=True)
        except OSError as exc:
            raise RuntimeError(f"无法清除飞书应用配置：{exc}") from exc
    stop_long_connection()


def app_status() -> dict[str, Any]:
    return webhook_status()["app"]


def mask_app_id(app_id: str) -> str:
    if not app_id:
        return ""
    if len(app_id) <= 8:
        return app_id[:2] + "****"
    return f"{app_id[:2]}****{app_id[-4:]}"


def _load_app_credentials() -> dict[str, Any]:
    with _APP_LOCK:
        if not FEISHU_APP_CONFIG_PATH.exists():
            return {}
        try:
            value = json.loads(FEISHU_APP_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return value if isinstance(value, dict) else {}


def _write_app_credentials(app: dict[str, Any]) -> None:
    FEISHU_APP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _APP_LOCK:
        FEISHU_APP_CONFIG_PATH.write_text(json.dumps(app, ensure_ascii=False), encoding="utf-8")


def _best_reply_target(app: dict[str, Any]) -> tuple[str, str] | None:
    chat_id = str(app.get("last_chat_id") or "")
    if chat_id:
        return "chat_id", chat_id
    return None


def _remember_reply_target(chat_id: str, open_id: str) -> None:
    if not chat_id and not open_id:
        return
    app = _load_app_credentials()
    app["last_chat_id"] = chat_id or app.get("last_chat_id", "")
    app["last_open_id"] = open_id or app.get("last_open_id", "")
    _write_app_credentials(app)


def _tenant_access_token() -> str:
    app = _load_app_credentials()
    app_id = str(app.get("app_id") or "")
    app_secret = str(app.get("app_secret") or "")
    if not app_id or not app_secret:
        raise ValueError("请先配置飞书应用 App ID / App Secret")
    cached = _TOKEN_CACHE.get("token")
    if cached and _TOKEN_CACHE.get("expires_at", 0) > time.time() + 60:
        return cached
    _rate_limit_send()
    body = _post_json_raw(_TOKEN_URL, {"app_id": app_id, "app_secret": app_secret})
    token = str(body.get("tenant_access_token") or "")
    if not token:
        raise RuntimeError(f"无法获取飞书应用 token：{body.get('msg') or '未知错误'}")
    expire_seconds = _safe_int(body.get("expire", 7200), 60, 86400)
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = time.time() + expire_seconds
    return token


def _app_bot_send_text(text: str, receive_id_type: str, receive_id: str) -> None:
    token = _tenant_access_token()
    _rate_limit_send()
    url = f"{_SEND_URL}?receive_id_type={receive_id_type}"
    _post_json_raw(
        url,
        {"receive_id": receive_id, "msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        token=token,
    )


def _app_bot_reply(message_id: str, text: str) -> None:
    if not message_id:
        raise ValueError("缺少可回复的 message_id")
    token = _tenant_access_token()
    _rate_limit_send()
    url = f"{_REPLY_URL_PREFIX}{message_id}/reply"
    _post_json_raw(
        url,
        {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)},
        token=token,
    )


def _post_json_raw(url: str, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("网络连接飞书失败") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("飞书应用返回了无法识别的响应") from exc
    code = result.get("code", result.get("StatusCode", 0))
    if code not in {0, "0", None}:
        raise RuntimeError(f"飞书应用返回错误：{result.get('msg') or result.get('StatusMessage') or '未知错误'}")
    return result


def _rate_limit_send() -> None:
    global _NEXT_SEND_AT
    now = time.monotonic()
    with _APP_LOCK:
        wait = _NEXT_SEND_AT - now
        if wait > 0:
            time.sleep(min(wait, 2.0))
            now = time.monotonic()
        _NEXT_SEND_AT = now + _SEND_COOLDOWN_SECONDS


_olds_map: dict[str, float] = {}


def _is_seen(event_id: str, message_id: str, now: float | None = None) -> bool:
    key = str(event_id or message_id or "")
    if not key:
        return False
    now = now if now is not None else time.time()
    with _APP_LOCK:
        if key in _SEEN_EVENTS:
            return True
        _SEEN_EVENTS.add(key)
        _SEEN_EVENTS_ORDER.append(key)
        _olds_map[key] = now
        while len(_SEEN_EVENTS_ORDER) > _SEEN_EVENTS_LIMIT:
            old = _SEEN_EVENTS_ORDER.pop(0)
            _SEEN_EVENTS.discard(old)
            _olds_map.pop(old, None)
        for old in [item for item in _SEEN_EVENTS_ORDER if _olds_map.get(item, now) < now - _DEDUP_WINDOW_SECONDS and item != key]:
            _SEEN_EVENTS.discard(old)
            _SEEN_EVENTS_ORDER.remove(old)
            _olds_map.pop(old, None)
    return False


def handle_inbound_message(payload: dict[str, Any]) -> str | None:
    """处理 im.message.receive_v1，只接受精确 0/1/2 并回复。不抛异常。"""
    try:
        header = payload.get("header", {}) or {}
        event = payload.get("event", {}) or {}
        event_id = str(header.get("event_id") or payload.get("event_id") or "")
        sender = event.get("sender", {}) or {}
        message = event.get("message", {}) or {}
        message_id = str(message.get("message_id") or "")
        chat_id = str(message.get("chat_id") or "")
        open_id = str((sender.get("sender_id") or {}).get("open_id") or "")
        if _is_seen(event_id, message_id):
            return None

        app = _load_app_credentials()
        if not app.get("app_id") or not app.get("app_secret"):
            return None
        if not _is_allowed_sender(app, open_id, chat_id):
            return None

        text = _extract_message_text(message)
        if text is None:
            return None

        _remember_reply_target(chat_id, open_id)
        append_history(text, direction="inbound", status="success")
        try:
            reply = dispatch_command(text)
        except ValueError:
            reply = "仅支持精确指令 0、1、2。发送 0 查看最近日志，1 查看定时计划，2 查看分组。"
        _app_bot_reply(message_id, reply)
        append_history(reply, direction="system", status="success")
        return reply
    except Exception as exc:
        record(
            f"错误提示 error：飞书消息接收处理失败：{_safe_error(exc)}",
            level="error",
            event="feishu.inbound.failed",
        )
        return None


def _is_allowed_sender(app: dict[str, Any], open_id: str, chat_id: str) -> bool:
    allowed_open = {item.strip() for item in app.get("allowed_open_ids", []) if item.strip()}
    allowed_chat = {item.strip() for item in app.get("allowed_chat_ids", []) if item.strip()}
    if not allowed_open and not allowed_chat:
        return True
    return open_id in allowed_open or chat_id in allowed_chat


def _extract_message_text(message: dict[str, Any]) -> str | None:
    if str(message.get("message_type") or "") != "text":
        return None
    content = message.get("content", "")
    try:
        body = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    text = str(body.get("text") or "").strip()
    return text or None


def start_long_connection(app_id: str | None = None, app_secret: str | None = None) -> None:
    global _CONNECTION_CLIENT, _CONNECTION_THREAD, _CONNECTION_STARTED_EVER
    with _APP_LOCK:
        if _CONNECTION_STARTED_EVER:
            if app_id or app_secret:
                save_app_credentials(app_id or "", app_secret or "")
            return
    if app_id or app_secret:
        save_app_credentials(app_id or "", app_secret or "")
    app = _load_app_credentials()
    real_id = str(app.get("app_id") or "")
    real_secret = str(app.get("app_secret") or "")
    if not real_id or not real_secret:
        _set_connection_state("未配置")
        return
    try:
        import lark_oapi as lark
    except ImportError:
        _set_connection_state("缺少 lark-oapi，请在终端运行 pip install lark-oapi -U")
        return
    try:
        dispatcher = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
            _lark_inbound_bridge
        ).build()
        client = lark.ws.Client(real_id, real_secret, event_handler=dispatcher, log_level=lark.LogLevel.INFO)
    except Exception as exc:
        record(f"错误提示 error：飞书长连接启动失败：{_safe_error(exc)}", level="error", event="feishu.connection.failed")
        _set_connection_state("启动失败")
        return
    _CONNECTION_CLIENT = client
    _CONNECTION_STARTED_EVER = True
    _set_connection_state("已连接")

    def run() -> None:
        try:
            client.start()
        except Exception as exc:
            record(f"错误提示 error：飞书长连接断开：{_safe_error(exc)}", level="error", event="feishu.connection.closed")
            _set_connection_state("连接断开，请重启控制台重连")

    _CONNECTION_THREAD = threading.Thread(target=run, daemon=True, name="feishu-long-connection")
    _CONNECTION_THREAD.start()


def stop_long_connection() -> None:
    """Best-effort stop. lark-oapi's ws.Client exposes no stop(), so a lingering
    daemon thread may continue; the inbound handler re-reads credentials on every
    event and silently ignores messages once the config is cleared."""
    global _CONNECTION_CLIENT
    with _APP_LOCK:
        _CONNECTION_CLIENT = None
    _set_connection_state("未配置")


def connection_status() -> str:
    with _APP_LOCK:
        return _CONNECTION_STATE


def _set_connection_state(value: str) -> None:
    global _CONNECTION_STATE
    with _APP_LOCK:
        _CONNECTION_STATE = value


def _lark_inbound_bridge(raw_event: Any) -> None:
    handle_inbound_message(_to_plain_dict(raw_event))


def _to_plain_dict(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _to_plain_dict(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_dict(item) for item in value]
    if hasattr(value, "__dict__"):
        return {key: _to_plain_dict(val) for key, val in vars(value).items()}
    return value


def notify_task(message: str) -> None:
    if not _any_outbound_channel():
        return
    try:
        send_text(message, direction="system")
    except Exception as exc:
        record(
            f"错误提示 error：飞书任务通知发送失败：{_safe_error(exc)}",
            level="error",
            event="feishu.notify.failed",
        )


def notify_cookie_expired(times: int = 3) -> None:
    if not _any_outbound_channel():
        return
    message = "抖音 Cookie/登录状态已连续两次检测过期，请尽快打开本地控制台重新扫码登录。"
    for index in range(max(1, times)):
        try:
            send_text(f"【Cookie 过期提醒 {index + 1}/{times}】{message}", direction="system", retries=0)
        except Exception as exc:
            record(
                f"错误提示 error：第 {index + 1} 条 Cookie 过期飞书提醒失败：{_safe_error(exc)}",
                level="error",
                event="feishu.cookie-alert.failed",
            )
        if index < times - 1:
            time.sleep(1.0)


def dispatch_command(command: str, config: dict[str, Any] | None = None) -> str:
    normalized = command.strip()
    if normalized == "0":
        return format_recent_logs()
    if normalized == "1":
        return format_active_plans(config)
    if normalized == "2":
        return format_groups(config)
    raise ValueError("只支持精确指令 0、1、2")


def format_recent_logs() -> str:
    entries = recent(20)
    if not entries:
        return "最近暂无本地运行日志。"
    lines = ["最近 20 条运行日志："]
    for index, entry in enumerate(entries, 1):
        time_text = str(entry.get("time") or "")
        try:
            time_text = datetime.fromisoformat(time_text).strftime("%m-%d %H:%M:%S")
        except ValueError:
            pass
        lines.append(f"{index}. [{time_text}] {entry.get('message', '')}")
    return "\n".join(lines)


def format_active_plans(config: dict[str, Any] | None = None) -> str:
    if config is None:
        from web.services.config_store import load_config

        config = load_config()
    schedule = config.get("schedule", {}) if isinstance(config, dict) else {}
    if not schedule.get("enabled"):
        return "目前没有启用每日定时计划。"
    entries = schedule.get("entries", [])
    lines = ["目前启用的每日定时计划："]
    active = 0
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not str(entry.get("content") or "").strip():
            continue
        active += 1
        hour = _safe_int(entry.get("hour"), 0, 23)
        minute = _safe_int(entry.get("minute"), 0, 59)
        group = str(entry.get("group") or "当前勾选")
        content = str(entry.get("content") or "").strip()
        lines.append(f"{active}.{group}；每日 {hour:02d}:{minute:02d}；信息内容：{content}")
    return "\n".join(lines) if active else "目前没有有效的每日定时计划。"


def format_groups(config: dict[str, Any] | None = None) -> str:
    if config is None:
        from web.services.config_store import load_config

        config = load_config()
    groups = config.get("groups", []) if isinstance(config, dict) else []
    lines = ["目前分组信息："]
    active = 0
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict) or not str(group.get("name") or "").strip():
            continue
        active += 1
        name = str(group.get("name")).strip()
        members = [str(item).strip() for item in group.get("members", []) if str(item).strip()]
        lines.append(f"{name}：{'、'.join(members) if members else '暂无成员'}")
    return "\n".join(lines) if active else "目前没有保存的分组。"


def append_history(
    message: str,
    *,
    direction: str,
    status: str,
    error: str | None = None,
) -> None:
    entry = {
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "direction": direction,
        "status": status,
        "message": message,
        "error": _safe_error(error) if error else None,
    }
    with _LOCK:
        FEISHU_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FEISHU_HISTORY_PATH.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim_history()


def load_history(limit: int = 100) -> list[dict[str, Any]]:
    with _LOCK:
        if not FEISHU_HISTORY_PATH.exists():
            return []
        lines = FEISHU_HISTORY_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    result: list[dict[str, Any]] = []
    for line in lines[-max(1, min(200, limit)):]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def _load_webhook() -> str:
    with _LOCK:
        if not FEISHU_CONFIG_PATH.exists():
            return ""
        try:
            value = json.loads(FEISHU_CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ""
    webhook = value.get("webhook") if isinstance(value, dict) else ""
    return webhook if isinstance(webhook, str) and _valid_webhook(webhook) else ""


def _valid_webhook(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    token = parsed.path.removeprefix(_ALLOWED_PATH_PREFIX)
    return (
        parsed.scheme == "https"
        and parsed.hostname == _ALLOWED_HOST
        and parsed.path.startswith(_ALLOWED_PATH_PREFIX)
        and bool(token)
        and "/" not in token
        and not parsed.query
        and not parsed.fragment
    )


def _post_json(url: str, payload: dict[str, Any]) -> None:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            body = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeError("网络连接飞书失败") from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("飞书机器人返回了无法识别的响应") from exc
    code = result.get("code", result.get("StatusCode", 0))
    if code not in {0, "0", None}:
        raise RuntimeError(f"飞书机器人返回错误：{result.get('msg') or result.get('StatusMessage') or '未知错误'}")


def _trim_history() -> None:
    lines = FEISHU_HISTORY_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    if len(lines) <= _MAX_HISTORY_LINES:
        return
    FEISHU_HISTORY_PATH.write_text(
        "\n".join(lines[-_MAX_HISTORY_LINES:]) + "\n",
        encoding="utf-8",
    )


def _safe_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return minimum


def _safe_error(error: Any) -> str:
    text = str(error or "未知错误")
    if "open.feishu.cn/open-apis/bot/v2/hook/" in text:
        return "飞书 Webhook 已隐藏"
    app = _load_app_credentials()
    secret = str(app.get("app_secret") or "")
    if secret and secret in text:
        text = text.replace(secret, "****")
    for key in ("tenant_access_token", "tenant access token"):
        if key in text:
            return "飞书应用 token 已隐藏"
    return text[:300]

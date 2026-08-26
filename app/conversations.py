from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from app.browser import open_douyin, open_private_messages
from app.config import load_settings
from web.paths import CONVERSATIONS_PATH, DATA_DIR, SPARK_ASSETS_DIR

_CONVERSATION_ROWS = (
    '[data-e2e="conversation-item"]',
    '[class*="conversationConversationItemwrapper"]',
    '[class*="conversation-item"]',
)
_TITLE_SELECTORS = (
    '[class="conversationConversationItemtitle"]',
    '[class*="conversationConversationItemtitle"]',
)
_AVATAR_SELECTORS = (
    'img[class*="commonConversationIcon"]',
    '[class*="commonIMAvataravatarContainer"] img',
    '[class*="semi-avatar"] img',
)
_STREAK_SELECTOR = '[class*="commonStreakstreakContainer"]'
_STREAK_IMAGE_SELECTOR = 'img[class*="commonStreakicon"], img'
_TIME_SELECTOR = '[class*="ConversationItemTagNextToTitletimeStr"], [class*="timeStr"]'
_PREVIEW_SELECTOR = (
    '[class*="ConversationItemDeschintWrapper"]',
    '[class*="ConversationItemHinttextBox"]',
)
_GROUP_HINTS = ("群", "group", "聊天室")
_ALLOWED_ASSET_HOST_SUFFIXES = (
    ".bytednsdoc.com",
    ".byteimg.com",
    ".douyinpic.com",
)
_ASSET_MAX_BYTES = 512 * 1024
_SCAN_MAX_ROUNDS = 8
_SCAN_STABLE_ROUNDS = 2


async def scrape_conversations() -> dict[str, Any]:
    settings = load_settings()
    async with open_douyin(settings) as session:
        page = session.page
        await open_private_messages(page)
        row_selector = None
        for selector in _CONVERSATION_ROWS:
            candidate = page.locator(selector)
            if await candidate.count():
                row_selector = selector
                break
        if row_selector is None:
            raise RuntimeError("抖音左侧会话栏结构已变化，未找到会话列表")

        conversations = await _collect_conversations(page, row_selector)
        payload = {
            "updated_at": datetime.now().astimezone().isoformat(),
            "count": len(conversations),
            "conversations": conversations,
        }
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONVERSATIONS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload


async def _collect_conversations(page, row_selector: str) -> list[dict[str, Any]]:
    """Read only sidebar rows with bounded, low-frequency lazy-list scrolling."""
    by_id: dict[str, dict[str, Any]] = {}
    stable_rounds = 0
    last_count = -1
    for _ in range(_SCAN_MAX_ROUNDS):
        rows = page.locator(row_selector)
        count = await rows.count()
        for index in range(count):
            try:
                data = await _extract_row(rows.nth(index), len(by_id))
            except Exception:
                continue
            if data and data.get("id"):
                by_id.setdefault(data["id"], data)

        if len(by_id) == last_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            last_count = len(by_id)
        if stable_rounds >= _SCAN_STABLE_ROUNDS or count == 0:
            break

        try:
            await rows.last.hover(timeout=2_000)
            await page.mouse.wheel(0, random.randint(380, 520))
            await page.wait_for_timeout(random.randint(1_600, 2_400))
        except Exception:
            break
    return list(by_id.values())


async def _extract_row(row, index: int) -> dict[str, Any] | None:
    name = await _extract_name(row)
    if not name:
        return None
    row_text = await _safe_text(row)
    sidebar_time = await _first_text(row, (_TIME_SELECTOR,))
    native_streak = await _extract_native_streak(row)
    spark = _extract_spark_state(
        name,
        row_text,
        streak_text=native_streak["label"],
        sidebar_time=sidebar_time,
        allow_anchored_number=False,
    )
    kind = _infer_kind(name, row_text)
    avatar = await _extract_avatar(row)
    preview = await _extract_sidebar_preview(row)
    stable_key = hashlib.sha1(f"{kind}:{name}".encode("utf-8")).hexdigest()[:12]
    spark_icon_local = _cache_spark_icon(native_streak["image_url"])
    return {
        "id": f"conversation-{stable_key}",
        "row_index": index,
        "name": name,
        "display_name": name,
        "kind": kind,
        "avatar": avatar,
        "has_spark": spark["has_spark"],
        "spark_days": spark["spark_days"],
        "spark_label": spark["spark_label"],
        "spark_native_label": native_streak["label"],
        "spark_icon": "",
        "spark_icon_url": native_streak["image_url"],
        "spark_icon_local": spark_icon_local,
        "spark_mode": spark["spark_mode"],
        "spark_reignite_progress": spark["spark_reignite_progress"],
        "spark_will_expire_in_days": spark["spark_will_expire_in_days"],
        "spark_is_broken": spark["spark_is_broken"],
        "spark_detail": spark["spark_detail"],
        "sidebar_time": sidebar_time,
        "enabled": False,
        "preview": preview or _extract_preview(name, row_text, spark, sidebar_time),
        "raw_text": row_text,
    }


async def _extract_name(row) -> str:
    for selector in _TITLE_SELECTORS:
        locator = row.locator(selector)
        count = await locator.count()
        for index in range(count):
            text = await _safe_text(locator.nth(index))
            if text and _is_reasonable_name(text):
                return text
    text = await _safe_text(row)
    for candidate in [segment.strip() for segment in text.splitlines() if segment.strip()]:
        if _is_reasonable_name(candidate):
            return candidate
    return ""


async def _extract_avatar(row) -> dict[str, str] | None:
    for selector in _AVATAR_SELECTORS:
        locator = row.locator(selector).first
        try:
            if not await locator.count():
                continue
            src = await locator.get_attribute("src") or ""
            if not src:
                continue
            return {
                "src": src,
                "alt": await locator.get_attribute("alt") or "",
                "class": await locator.get_attribute("class") or "",
            }
        except Exception:
            continue
    return None


async def _extract_native_streak(row) -> dict[str, str]:
    locator = row.locator(_STREAK_SELECTOR).first
    try:
        if not await locator.count():
            return {"label": "", "image_url": ""}
        label = await _safe_text(locator)
        image = locator.locator(_STREAK_IMAGE_SELECTOR).first
        image_url = ""
        if await image.count():
            image_url = await image.get_attribute("src") or ""
            if not image_url:
                srcset = await image.get_attribute("srcset") or ""
                image_url = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
        return {"label": label, "image_url": image_url}
    except Exception:
        return {"label": "", "image_url": ""}


async def _extract_sidebar_preview(row) -> str:
    return (await _first_text(row, _PREVIEW_SELECTOR))[:80]


async def _first_text(row, selectors: tuple[str, ...]) -> str:
    for selector in selectors:
        locator = row.locator(selector).first
        try:
            if await locator.count():
                text = await _safe_text(locator)
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _safe_text(locator) -> str:
    try:
        text = (await locator.inner_text()).strip()
    except Exception:
        return ""
    return re.sub(r"\s+", " ", text)


def _infer_kind(name: str, row_text: str) -> str:
    candidate = f"{name} {row_text}".lower()
    return "group" if any(token in candidate for token in _GROUP_HINTS) else "contact"


def _extract_spark_state(
    name: str,
    text: str,
    *,
    streak_text: str = "",
    sidebar_time: str = "",
    allow_anchored_number: bool = True,
) -> dict[str, Any]:
    compact = re.sub(r"\s+", " ", text).strip()
    native = re.sub(r"\s+", " ", streak_text).strip()
    spark_label = native
    spark_days = None
    spark_mode = None
    progress = None
    expire_days = None
    is_broken = False
    detail = ""

    candidate = native or compact
    reignite = re.search(r"重燃中\s*(\d+)\s*/\s*(\d+)", candidate)
    if reignite:
        current = int(reignite.group(1))
        total = int(reignite.group(2))
        spark_label = native or f"重燃中 {current}/{total}"
        spark_mode = "reignite"
        progress = {"current": current, "total": total}
        is_broken = True
    else:
        expiring = re.search(r"(\d+)\s*天后(?:火花将)?消失", candidate)
        if expiring:
            expire_days = int(expiring.group(1))
            spark_label = native or f"{expire_days} 天后消失"
            spark_mode = "expiring"
        else:
            native_number = re.fullmatch(r"\s*(\d+)\s*", native)
            explicit_days = re.search(r"连续\s*(\d+)\s*天", candidate)
            if native_number:
                spark_days = int(native_number.group(1))
            elif explicit_days:
                spark_days = int(explicit_days.group(1))
            elif allow_anchored_number:
                timestamp = _timestamp_pattern()
                anchored_number = re.search(
                    rf"{re.escape(name)}\s*(\d+)\s*(?={timestamp})",
                    compact,
                )
                if anchored_number:
                    spark_days = int(anchored_number.group(1))
            if spark_days is not None:
                spark_label = native or f"连续 {spark_days} 天"
                spark_mode = "active"

    detail_match = re.search(
        r"(\d+\s*天后火花将消失[^。；]*?(?:可恢复|续火花))",
        compact,
    )
    if detail_match:
        detail = detail_match.group(1).strip()
    elif spark_mode == "reignite" and progress:
        detail = f"连续聊天恢复进度 {progress['current']}/{progress['total']}"
    elif spark_mode == "expiring" and expire_days is not None:
        detail = f"火花将在 {expire_days} 天后消失"

    has_spark = bool(native or spark_label or spark_days is not None or expire_days is not None or progress)
    return {
        "has_spark": has_spark,
        "spark_days": spark_days,
        "spark_label": spark_label,
        "spark_mode": spark_mode,
        "spark_reignite_progress": progress,
        "spark_will_expire_in_days": expire_days,
        "spark_is_broken": is_broken,
        "spark_detail": detail,
        "sidebar_time": sidebar_time,
    }


def _timestamp_pattern() -> str:
    return (
        r"(?:\d{1,2}:\d{2}|今天|昨天|前天|刚刚|"
        r"\d+\s*分钟前|\d+\s*小时前|"
        r"(?:周|星期)[一二三四五六日天]|"
        r"\d{1,2}\s*[月/-]\s*\d{1,2}(?:\s*日)?)"
    )


def _extract_preview(
    name: str,
    text: str,
    spark: dict[str, Any],
    sidebar_time: str = "",
) -> str:
    compact = re.sub(r"\s+", " ", text)
    compact = compact.removeprefix(name).strip(" ：:")
    for label in (str(spark.get("spark_label") or ""), sidebar_time):
        if label:
            compact = compact.replace(label, "", 1).strip(" ：:")
    compact = re.sub(rf"^(?:{_timestamp_pattern()})\s*", "", compact).strip()
    return compact[:80]


def _cache_spark_icon(url: str) -> str:
    if not url or not _safe_asset_url(url):
        return ""
    SPARK_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    parsed = urlsplit(url)
    suffix = Path(parsed.path).suffix.lower()
    if suffix not in {".png", ".webp", ".jpg", ".jpeg", ".gif"}:
        suffix = ".png"
    filename = f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:24]}{suffix}"
    target = SPARK_ASSETS_DIR / filename
    if target.is_file() and target.stat().st_size:
        return filename
    try:
        request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(request, timeout=8) as response:
            content_type = response.headers.get_content_type()
            if not content_type.startswith("image/"):
                return ""
            body = response.read(_ASSET_MAX_BYTES + 1)
        if not body or len(body) > _ASSET_MAX_BYTES:
            return ""
        target.write_bytes(body)
        return filename
    except (OSError, ValueError):
        return ""


def _safe_asset_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(
        host == suffix.removeprefix(".") or host.endswith(suffix)
        for suffix in _ALLOWED_ASSET_HOST_SUFFIXES
    )


def _is_reasonable_name(text: str) -> bool:
    if not text or len(text) > 60:
        return False
    if any(token in text for token in ("发送", "搜索", "私信")):
        return False
    return True


def load_conversations() -> dict[str, Any]:
    if not CONVERSATIONS_PATH.exists():
        return {"updated_at": None, "count": 0, "conversations": []}
    try:
        value = json.loads(CONVERSATIONS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"updated_at": None, "count": 0, "conversations": []}
    if not isinstance(value, dict):
        return {"updated_at": None, "count": 0, "conversations": []}
    return value

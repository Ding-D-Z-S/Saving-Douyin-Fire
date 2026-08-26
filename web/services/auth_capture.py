from __future__ import annotations

import asyncio

from app.browser import capture_storage_state
from app.config import load_settings
from web.paths import STATE_PATH
from web.services.config_store import set_storage_state


def capture_login_state() -> None:
    settings = load_settings()
    asyncio.run(capture_storage_state(settings, STATE_PATH))
    set_storage_state(STATE_PATH)

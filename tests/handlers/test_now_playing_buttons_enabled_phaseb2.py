"""Phase B2: buttons_enabled gates optional now-playing controls."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def _all_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def test_buttons_enabled_true_keeps_full_controls():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.now_playing_controls("fa", repeat_on=False, show_extras=True)
    cbs = _all_callbacks(kb)
    assert CB["PB_VOL_DOWN"] in cbs
    assert CB["PB_REPEAT_TOGGLE"] in cbs
    assert CB["PB_STOP"] in cbs


def test_buttons_enabled_false_hides_optional_controls():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.now_playing_controls("fa", repeat_on=True, show_extras=False)
    cbs = _all_callbacks(kb)
    assert CB["PB_STOP"] in cbs
    assert CB["PB_PAUSE"] in cbs
    assert CB["PB_RESUME"] in cbs
    assert CB["PB_VOL_DOWN"] not in cbs
    assert CB["PB_REPEAT_TOGGLE"] not in cbs
    assert CB["PB_NEXT"] not in cbs


def test_stop_button_remains_visible_when_extras_hidden():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.now_playing_controls("en", repeat_on=False, show_extras=False)
    cbs = _all_callbacks(kb)
    assert CB["PB_STOP"] in cbs


def test_optional_callback_handlers_remain_registered():
    import re

    from app.services.playback_callback_dispatcher import playback_controls_regex
    from app.utils.ui import CB

    pattern = playback_controls_regex()
    for cb_key in ("PB_VOL_DOWN", "PB_REPEAT_TOGGLE", "PB_NEXT", "PB_PREV"):
        assert re.fullmatch(pattern, CB[cb_key]) is not None


@pytest.mark.asyncio
async def test_build_now_playing_controls_respects_buttons_enabled_flag():
    from app.services.media_capability_service import build_now_playing_controls
    from app.utils.ui import CB

    cs_on = SimpleNamespace(buttons_enabled=True)
    cs_off = SimpleNamespace(buttons_enabled=False)

    with patch(
        "app.repositories.settings_repo.get_chat_settings",
        AsyncMock(return_value=cs_on),
    ):
        kb_on = await build_now_playing_controls("fa", -4001)
    assert CB["PB_REPEAT_TOGGLE"] in _all_callbacks(kb_on)

    with patch(
        "app.repositories.settings_repo.get_chat_settings",
        AsyncMock(return_value=cs_off),
    ):
        kb_off = await build_now_playing_controls("fa", -4001)
    cbs = _all_callbacks(kb_off)
    assert CB["PB_STOP"] in cbs
    assert CB["PB_VOL_DOWN"] not in cbs


@pytest.mark.asyncio
async def test_playback_uses_build_now_playing_controls_helper():
    from pathlib import Path

    source = Path("app/handlers/playback.py").read_text(encoding="utf-8")
    assert "build_now_playing_controls" in source
    assert source.count("build_now_playing_controls") >= 2


@pytest.mark.asyncio
async def test_tv_radio_uses_build_now_playing_controls_helper():
    from pathlib import Path

    source = Path("app/handlers/tv_radio.py").read_text(encoding="utf-8")
    assert "build_now_playing_controls" in source
    assert "await _playing_kb" in source

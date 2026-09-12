"""Deprecated global channel_security UI and playback-lock separation."""
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

from app.config.settings import settings


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *_args, **_kwargs):
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _callbacks(kb) -> set[str]:
    return {btn.callback_data for row in kb.inline_keyboard for btn in row if btn.callback_data}


def test_dev_sub_settings_hides_channel_security_toggle():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.dev_sub_settings("fa")
    assert CB["DEV_CHANNEL_SECURITY_TOGGLE"] not in _callbacks(kb)


def test_owner_sub_settings_hides_channel_security_toggle():
    from app.utils.ui import CB, KeyboardFactory

    kb = KeyboardFactory.owner_sub_settings("fa")
    assert CB["OWN_CHANNEL_SECURITY_TOGGLE"] not in _callbacks(kb)


def test_playback_lock_callback_unchanged():
    from app.utils.ui import CB

    assert CB["GRP_SECURITY_CALL"] == "grp:set:security_call"


def test_real_call_security_entry_is_not_channel_security():
    from app.utils.i18n import t

    assert "امنیت" in t("fa", "panels.group.settings.call_security")
    assert "قفل پخش" in t("fa", "panels.group.settings.security_call")


@pytest.mark.asyncio
async def test_dev_channel_security_toggle_shows_deprecation_message():
    from app.handlers import dev_panel
    from app.utils.i18n import t

    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_channel_security_toggle")
    query = SimpleNamespace(
        data="dev:channel_security",
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=settings.DEVELOPER_ID, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )
    with patch("app.handlers.dev_panel._dev_settings_keyboard", AsyncMock(return_value=None)):
        await handler.__wrapped__(SimpleNamespace(), query)

    edit_text = query.message.edit_text.await_args.args[0]
    assert t("fa", "call_security.deprecated_global_toggle") in edit_text
    assert "امنیت کانال" not in edit_text


@pytest.mark.asyncio
async def test_panel_router_status_summary_excludes_channel_security():
    from app.services.panel_router import _build_status_summary

    with (
        patch(
            "app.services.panel_router.settings_repo.get_bot_setting_bool",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.panel_router.settings_repo.get_bot_setting",
            AsyncMock(return_value="0"),
        ),
    ):
        summary = await _build_status_summary("fa")
    assert "channel_security" not in summary.lower()
    assert "امنیت کانال" not in summary

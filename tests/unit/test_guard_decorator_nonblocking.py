"""Guard handlers must reply visibly and not silently swallow updates."""
from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")
    pyromod_exceptions.ListenerStopped = type("ListenerStopped", (Exception,), {})
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.handlers import group_guard
from app.utils.decorators import developer_only
from app.utils.i18n import t


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []

    def on_message(self, *args, **kwargs):
        def deco(fn):
            self.message_handlers.append(fn)
            return fn

        return deco


def _group_message(text: str = "پخش test") -> SimpleNamespace:
    return SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        chat=SimpleNamespace(id=-1001, type=SimpleNamespace(value="supergroup")),
        text=text,
        caption=None,
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
        continue_propagation=MagicMock(),
    )


@pytest.mark.asyncio
async def test_blacklisted_group_gets_visible_reply():
    bot = _RecorderBot()
    group_guard.register(bot, None)
    handler = bot.message_handlers[0]
    message = _group_message()

    with (
        patch("app.handlers.group_guard.blacklist_repo.is_blacklisted", AsyncMock(side_effect=[True, False])),
        patch("app.handlers.group_guard.resolve_lang", AsyncMock(return_value="fa")),
    ):
        await handler(MagicMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "common.errors.blacklisted"))
    message.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_blacklisted_user_gets_visible_reply():
    bot = _RecorderBot()
    group_guard.register(bot, None)
    handler = bot.message_handlers[0]
    message = _group_message()

    with (
        patch(
            "app.handlers.group_guard.blacklist_repo.is_blacklisted",
            AsyncMock(side_effect=[False, True]),
        ),
        patch("app.handlers.group_guard.resolve_lang", AsyncMock(return_value="fa")),
    ):
        await handler(MagicMock(), message)

    message.reply.assert_awaited_once_with(t("fa", "common.errors.blacklisted"))
    message.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_clean_group_continues_propagation():
    bot = _RecorderBot()
    group_guard.register(bot, None)
    handler = bot.message_handlers[0]
    message = _group_message()

    with (
        patch("app.handlers.group_guard.blacklist_repo.is_blacklisted", AsyncMock(return_value=False)),
        patch("app.handlers.group_guard.is_bot_enabled", AsyncMock(return_value=True)),
        patch("app.handlers.group_guard.is_developer", return_value=True),
        patch("app.handlers.group_guard.group_membership_age_service.record_member_seen", AsyncMock()),
    ):
        await handler(MagicMock(), message)

    message.continue_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_developer_only_callback_denial_answers_visibly():
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=424242),
        answer=AsyncMock(),
        message=SimpleNamespace(chat=SimpleNamespace(id=424242, type=SimpleNamespace(value="private"))),
    )
    inner = AsyncMock()
    guarded = developer_only(inner)

    with patch("app.utils.decorators.resolve_lang_from_update", AsyncMock(return_value="fa")):
        await guarded(MagicMock(), query)

    inner.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.no_access"), show_alert=True)

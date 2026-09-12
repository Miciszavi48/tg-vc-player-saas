"""Tests for پنل command permission gating on the group player panel."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


class _MessageRecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _handler_by_name(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _group_message(user_id: int = 42, text: str = "پنل"):
    message = MagicMock()
    message.from_user = SimpleNamespace(id=user_id)
    message.chat = SimpleNamespace(id=-100123, type=SimpleNamespace(value="supergroup"))
    message.text = text
    message.reply = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_panel_command_denied_when_can_open_group_panel_false():
    from app.handlers import group_panel

    bot = _MessageRecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "panel_command")
    message = _group_message()
    client = MagicMock()

    with patch(
        "app.utils.player_permissions.can_open_group_panel",
        AsyncMock(return_value=False),
    ):
        await handler(client, message)

    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0]  # denial text sent
    reply_markup = message.reply.await_args.kwargs.get("reply_markup")
    assert reply_markup is None


@pytest.mark.asyncio
async def test_panel_command_opens_group_panel_when_allowed():
    from app.handlers import group_panel
    from app.utils.i18n import t

    bot = _MessageRecorderBot()
    group_panel.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "panel_command")
    message = _group_message()
    client = MagicMock()
    sent = MagicMock()
    message.reply = AsyncMock(return_value=sent)

    with (
        patch(
            "app.utils.player_permissions.can_open_group_panel",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel._guard_group_chat_settings",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.remember_panel_from_message",
            AsyncMock(),
        ),
    ):
        await handler(client, message)

    message.reply.assert_awaited_once()
    assert t("fa", "panels.group.title") in message.reply.await_args.args[0]
    assert message.reply.await_args.kwargs.get("reply_markup") is not None

from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pyrogram
from pyrogram.handlers.callback_query_handler import CallbackQueryHandler

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

if "pyromod" not in sys.modules:
    pyromod_module = ModuleType("pyromod")
    pyromod_exceptions = ModuleType("pyromod.exceptions")

    class _ListenerStopped(Exception):
        pass

    pyromod_exceptions.ListenerStopped = _ListenerStopped
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.handlers import _wrap_callback_handlers_with_auto_answer
from app.handlers.priority import (
    GLOBAL_BAN_GROUP,
    HELP_CALLBACK_GROUP,
    PANEL_CALLBACK_GROUP,
    PYROMOD_CALLBACK_GROUP,
)


class _FakeBot:
    def __init__(self, handler: CallbackQueryHandler, *, group: int = 0) -> None:
        self.dispatcher = SimpleNamespace(groups={group: [handler]})


@pytest.mark.asyncio
async def test_callback_wrapper_prevents_double_answer_errors():
    async def _cb(client, query):
        await query.answer("first", show_alert=False)
        await query.answer("second", show_alert=True)
        await query.message.edit_text("done")

    handler = CallbackQueryHandler(_cb)
    bot = _FakeBot(handler)
    _wrap_callback_handlers_with_auto_answer(bot)

    base_answer = AsyncMock(side_effect=[None, RuntimeError("QUERY_ID_INVALID")])
    query = SimpleNamespace(
        data="any",
        answer=base_answer,
        message=SimpleNamespace(edit_text=AsyncMock()),
    )

    await handler.callback(SimpleNamespace(), query)

    # Guarded wrapper should call Telegram answer only once.
    assert base_answer.await_count == 1
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_wrapper_auto_answers_when_handler_skips_answer():
    async def _cb(client, query):
        await query.message.edit_text("done")

    handler = CallbackQueryHandler(_cb)
    bot = _FakeBot(handler)
    _wrap_callback_handlers_with_auto_answer(bot)

    base_answer = AsyncMock()
    query = SimpleNamespace(
        data="any",
        answer=base_answer,
        message=SimpleNamespace(edit_text=AsyncMock()),
    )

    await handler.callback(SimpleNamespace(), query)

    assert base_answer.await_count == 1
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [GLOBAL_BAN_GROUP, PYROMOD_CALLBACK_GROUP])
async def test_pre_dispatch_groups_do_not_auto_answer_callbacks(group: int):
    async def _cb(client, query):
        return None

    handler = CallbackQueryHandler(_cb)
    bot = _FakeBot(handler, group=group)
    _wrap_callback_handlers_with_auto_answer(bot)

    base_answer = AsyncMock()
    query = SimpleNamespace(
        data="hlp:add",
        answer=base_answer,
        message=SimpleNamespace(edit_text=AsyncMock()),
    )

    await handler.callback(SimpleNamespace(), query)

    base_answer.assert_not_awaited()
    assert not getattr(query, "_musicbot_callback_answered", False)


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [HELP_CALLBACK_GROUP, PANEL_CALLBACK_GROUP])
async def test_terminal_route_groups_stop_later_callback_handlers(group: int):
    async def _cb(client, query):
        await query.message.edit_text("done")

    handler = CallbackQueryHandler(_cb)
    bot = _FakeBot(handler, group=group)
    _wrap_callback_handlers_with_auto_answer(bot)

    base_answer = AsyncMock()
    query = SimpleNamespace(
        data="hlp:add",
        answer=base_answer,
        message=SimpleNamespace(edit_text=AsyncMock()),
    )

    with pytest.raises(pyrogram.StopPropagation):
        await handler.callback(SimpleNamespace(), query)

    base_answer.assert_awaited_once_with()
    query.message.edit_text.assert_awaited_once_with("done")

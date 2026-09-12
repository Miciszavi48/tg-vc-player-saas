from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pyrogram.handlers import CallbackQueryHandler

from app.handlers import _wrap_callback_handlers_with_auto_answer, callbacks
from app.handlers.priority import FALLBACK_CALLBACK_GROUP
from app.utils.callback_trace import safe_edit_or_send_callback
from app.utils.i18n import t
from app.utils.ui import CB


class _Dispatcher:
    def __init__(self) -> None:
        self.groups: dict[int, list] = {}


class _Bot:
    def __init__(self) -> None:
        self.dispatcher = _Dispatcher()

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        group = kwargs.get("group", 0)
        flt = args[0] if args else None

        def _decorator(fn):
            self.dispatcher.groups.setdefault(group, []).append(CallbackQueryHandler(fn, flt))
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            return fn

        return _decorator


def _query(data: str):
    return SimpleNamespace(
        id=f"query-{data}",
        data=data,
        from_user=SimpleNamespace(id=6909288370, first_name="Tester"),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=6909288370, type=SimpleNamespace(value="private")),
            text="menu",
            delete=AsyncMock(),
            edit_text=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


def _raw_handler(bot: _Bot, name: str):
    for handlers in bot.dispatcher.groups.values():
        for handler in handlers:
            if handler.callback.__name__ == name:
                return handler
    raise AssertionError(f"handler not found: {name}")


@pytest.mark.asyncio
async def test_unknown_callback_logs_unhandled_and_answers(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _Bot()
    callbacks.register(bot, None)
    handler = next(
        h for h in bot.dispatcher.groups[FALLBACK_CALLBACK_GROUP]
        if h.callback.__name__ == "unknown_callback_fallback"
    )
    query = _query("totally:unknown")

    await handler.callback(SimpleNamespace(), query)

    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.unknown_callback"),
        show_alert=True,
    )
    assert "callback.unhandled" in caplog.text
    assert "reason=unknown_prefix" in caplog.text


@pytest.mark.asyncio
async def test_edit_failure_falls_back_to_reply():
    query = _query(CB["HLP_HOME"])
    query.message.edit_text = AsyncMock(side_effect=RuntimeError("cant edit"))

    result = await safe_edit_or_send_callback(
        SimpleNamespace(send_message=AsyncMock()),
        query,
        "fallback panel",
        handler="helper_panel.hlp_home",
    )

    assert result == "fallback_sent"
    query.message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_nav_close_private_returns_start_home_and_logs_trace(caplog):
    from unittest.mock import patch

    from app.utils.ui import KeyboardFactory

    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _Bot()
    callbacks.register(bot, None)
    _wrap_callback_handlers_with_auto_answer(bot)
    handler = _raw_handler(bot, "nav_close")
    query = _query(CB["NAV_CLOSE"])
    home_kb = KeyboardFactory.with_management_entry("fa", "developer", None)

    with (
        patch("app.handlers.callbacks.clear_runtime_state", AsyncMock()),
        patch(
            "app.handlers.callbacks.build_private_start_home_payload",
            AsyncMock(return_value=("welcome home", home_kb)),
        ),
        patch("app.services.panel_message_service.get_redis", AsyncMock()),
    ):
        await handler.callback(SimpleNamespace(), query)

    query.message.delete.assert_not_awaited()
    query.message.edit_text.assert_awaited()
    query.answer.assert_awaited()
    assert "callback.route" in caplog.text
    assert "nav_close" in caplog.text
    assert "callback.handler.start" in caplog.text
    assert "callback.handler.done" in caplog.text


@pytest.mark.asyncio
async def test_nav_close_group_still_deletes_message(caplog):
    caplog.set_level(logging.INFO, logger="callback_trace")
    bot = _Bot()
    callbacks.register(bot, None)
    _wrap_callback_handlers_with_auto_answer(bot)
    handler = _raw_handler(bot, "nav_close")
    query = _query(CB["NAV_CLOSE"])
    query.message.chat = SimpleNamespace(
        id=-100123,
        type=SimpleNamespace(value="supergroup"),
    )

    await handler.callback(SimpleNamespace(), query)

    query.message.delete.assert_awaited_once()
    query.answer.assert_awaited()

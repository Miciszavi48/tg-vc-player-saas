"""NoSilent-2: shared ask-abort handling for dev/owner high-traffic flows."""
from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

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
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import dev_panel, owner_panel
from app.utils.ask_result import AskResult
from app.utils.i18n import t
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _noop(*args, **kwargs):  # noqa: ANN002, ANN003
                def _decorator(fn):
                    return fn

                return _decorator

            return _noop
        raise AttributeError(name)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
            reply=AsyncMock(),
        ),
    )


def _abort_text() -> str:
    return t("fa", "texts_links.ask_cancelled_or_timeout")


@pytest.mark.asyncio
async def test_dev_increase_credit_listener_stopped_sends_abort():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_increase_credit")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_INCREASE_CREDIT"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=None, abort_reason="listener_stopped")),
        ),
        patch("app.handlers.dev_panel.CreditService.charge_managed_chat", AsyncMock()) as charge_mock,
    ):
        await handler(client, query)

    charge_mock.assert_not_awaited()
    assert _abort_text() in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_dev_set_base_rate_listener_stopped_sends_abort():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_set_base_rate")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_SET_BASE_RATE"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=None, abort_reason="listener_stopped")),
        ),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
    ):
        await handler(client, query)

    set_mock.assert_not_awaited()
    assert _abort_text() in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_dev_filters_listener_stopped_sends_abort():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_filters")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_FILTERS"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.dev_panel.filter_repo.get_filter_words", AsyncMock(return_value=[])),
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(return_value=AskResult(message=None, abort_reason="listener_stopped")),
        ),
        patch("app.handlers.dev_panel.filter_repo.add_filter_word", AsyncMock()) as add_mock,
    ):
        await handler(client, query)

    add_mock.assert_not_awaited()
    assert _abort_text() in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_own_increase_bot_credit_listener_stopped_avoids_duplicate_abort():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_increase_bot_credit")
    query = _pm_query(settings.DEVELOPER_ID, CB["OWN_INCREASE_BOT_CREDIT"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel._require_developer_for_global_owner_route", AsyncMock(return_value=True)),
            patch(
                "app.handlers.owner_panel._ask",
                AsyncMock(
                    return_value=AskResult(
                        message=None,
                        abort_reason="listener_stopped",
                        user_notified=True,
                    )
                ),
            ),
        patch("app.handlers.owner_panel.settings_repo.set_bot_setting", AsyncMock()) as set_mock,
    ):
        await handler(client, query)

    set_mock.assert_not_awaited()
    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_own_filters_listener_stopped_sends_abort():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_filters")
    query = _pm_query(settings.DEVELOPER_ID, CB["OWN_FILTERS"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch("app.handlers.owner_panel._require_developer_for_global_owner_route", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel.filter_repo.get_filter_words", AsyncMock(return_value=[])),
        patch(
            "app.handlers.owner_panel._ask",
            AsyncMock(return_value=AskResult(message=None, abort_reason="listener_stopped")),
        ),
        patch("app.handlers.owner_panel.filter_repo.add_filter_word", AsyncMock()) as add_mock,
    ):
        await handler(client, query)

    add_mock.assert_not_awaited()
    assert _abort_text() in client.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_dev_increase_credit_timeout_does_not_double_send_abort():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_increase_credit")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_INCREASE_CREDIT"])
    client = SimpleNamespace(send_message=AsyncMock())

    with patch(
        "app.handlers.dev_panel._ask",
        AsyncMock(return_value=AskResult(message=None, abort_reason="timeout")),
    ):
        await handler(client, query)

    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_increase_credit_cancel_text_does_not_double_send_abort():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_increase_credit")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_INCREASE_CREDIT"])
    client = SimpleNamespace(send_message=AsyncMock())

    with patch(
        "app.handlers.dev_panel._ask",
        AsyncMock(return_value=AskResult(message=None, abort_reason="cancel_text")),
    ):
        await handler(client, query)

    client.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_increase_credit_valid_input_uses_message_text():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_increase_credit")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_INCREASE_CREDIT"])
    client = SimpleNamespace(send_message=AsyncMock())
    chat_msg = SimpleNamespace(text="12345")
    days_msg = SimpleNamespace(text="7")

    with (
        patch(
            "app.handlers.dev_panel._ask",
            AsyncMock(side_effect=[
                AskResult(message=chat_msg),
                AskResult(message=days_msg),
            ]),
        ),
        patch("app.handlers.dev_panel.CreditService.charge_managed_chat", AsyncMock()) as charge_mock,
        patch("app.handlers.dev_panel._send_done", AsyncMock()) as done_mock,
    ):
        await handler(client, query)

    charge_mock.assert_awaited_once()
    done_mock.assert_awaited_once()

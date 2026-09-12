"""NoSilent-1: dev_banall_panel AskResult handling tests."""
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
from app.handlers import dev_banall_panel
from app.services.texts_links_ui import AskResult
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


@pytest.mark.asyncio
async def test_banall_add_listener_stopped_sends_abort_feedback():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_add")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BANALL_ADD"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_banall_panel._ask",
            AsyncMock(return_value=AskResult(message=None, abort_reason="listener_stopped")),
        ),
        patch("app.handlers.dev_banall_panel.global_ban_repo.add_global_ban", AsyncMock()) as add_mock,
    ):
        await handler(client, query)

    add_mock.assert_not_awaited()
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.args[1] == t("fa", "texts_links.ask_cancelled_or_timeout")


@pytest.mark.asyncio
async def test_banall_remove_listener_stopped_sends_abort_feedback():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_remove")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BANALL_REMOVE"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_banall_panel._ask",
            AsyncMock(return_value=AskResult(message=None, abort_reason="listener_stopped")),
        ),
        patch("app.handlers.dev_banall_panel.global_ban_repo.remove_global_ban", AsyncMock()) as remove_mock,
    ):
        await handler(client, query)

    remove_mock.assert_not_awaited()
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.args[1] == t("fa", "texts_links.ask_cancelled_or_timeout")


@pytest.mark.asyncio
async def test_banall_add_timeout_returns_without_double_send():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_add")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BANALL_ADD"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_banall_panel._ask",
            AsyncMock(return_value=AskResult(message=None, abort_reason="timeout")),
        ),
        patch("app.handlers.dev_banall_panel.global_ban_repo.add_global_ban", AsyncMock()) as add_mock,
        patch("app.handlers.dev_banall_panel._send_done", AsyncMock()) as done_mock,
    ):
        await handler(client, query)

    add_mock.assert_not_awaited()
    done_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_banall_add_valid_user_id_uses_message_text():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_add")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BANALL_ADD"])
    client = SimpleNamespace(send_message=AsyncMock())
    msg = SimpleNamespace(text="  555001  ")

    with (
        patch(
            "app.handlers.dev_banall_panel._ask",
            AsyncMock(return_value=AskResult(message=msg)),
        ),
        patch(
            "app.handlers.dev_banall_panel.global_ban_repo.add_global_ban",
            AsyncMock(return_value=(SimpleNamespace(user_id=555001), True)),
        ),
        patch("app.handlers.dev_banall_panel.asyncio.create_task") as task_mock,
        patch("app.handlers.dev_banall_panel._send_done", AsyncMock()) as done_mock,
    ):
        await handler(client, query)

    task_mock.assert_called_once()
    done_mock.assert_awaited_once()
    assert done_mock.await_args.args[2] == t("fa", "global_ban.added_removal_started", user_id=555001)


@pytest.mark.asyncio
async def test_banall_remove_valid_user_id_uses_message_text():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_remove")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BANALL_REMOVE"])
    client = SimpleNamespace(send_message=AsyncMock())
    msg = SimpleNamespace(text="555002")

    with (
        patch(
            "app.handlers.dev_banall_panel._ask",
            AsyncMock(return_value=AskResult(message=msg)),
        ),
        patch(
            "app.handlers.dev_banall_panel.global_ban_repo.remove_global_ban",
            AsyncMock(return_value=True),
        ),
        patch("app.handlers.dev_banall_panel._send_done", AsyncMock()) as done_mock,
    ):
        await handler(client, query)

    done_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_banall_add_invalid_user_id_replies_with_existing_message():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_add")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BANALL_ADD"])
    client = SimpleNamespace(send_message=AsyncMock())
    msg = SimpleNamespace(text="not-a-number")

    with (
        patch(
            "app.handlers.dev_banall_panel._ask",
            AsyncMock(return_value=AskResult(message=msg)),
        ),
        patch("app.handlers.dev_banall_panel.global_ban_repo.add_global_ban", AsyncMock()) as add_mock,
        patch("app.handlers.dev_banall_panel._send_done", AsyncMock()) as done_mock,
    ):
        await handler(client, query)

    add_mock.assert_not_awaited()
    done_mock.assert_awaited_once()
    assert done_mock.await_args.args[2] == t("fa", "global_ban.invalid_user_id")


@pytest.mark.asyncio
async def test_banall_add_rejects_non_developer():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_add")
    query = _pm_query(999888, CB["DEV_BANALL_ADD"])
    client = SimpleNamespace(send_message=AsyncMock())

    with patch("app.handlers.dev_banall_panel._ask", AsyncMock()) as ask_mock:
        await handler(client, query)

    ask_mock.assert_not_awaited()


def test_banall_callback_constants_unchanged():
    assert CB["DEV_BANALL_ADD"] == "dev:banall:add"
    assert CB["DEV_BANALL_REMOVE"] == "dev:banall:remove"
    assert CB["DEV_BANALL_HOME"] == "dev:banall:home"

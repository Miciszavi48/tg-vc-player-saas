from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

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

from app.handlers import broadcast_panel, dev_panel  # noqa: E402
from app.utils.ui import CB, KeyboardFactory  # noqa: E402


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def __getattr__(self, name: str):
        if name.startswith("on_"):
            def _register_other(*args, **kwargs):  # noqa: ANN001, ANN002
                def _decorator(fn):
                    return fn

                return _decorator

            return _register_other
        raise AttributeError(name)


def _handler_by_name(handlers: list, name: str):
    for fn in handlers:
        if fn.__name__ == name:
            return fn
    raise AssertionError(f"handler not found: {name}")


def _query(data: str, user_id: int = 123456789):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Dev"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


def _callback_data_set(kb) -> set[str]:
    return {
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    }


@pytest.mark.asyncio
async def test_broadcast_cancel_first_click_only_shows_confirmation():
    bot = _RecorderBot()
    broadcast_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bc_cancel")
    query = _query(f"{CB['BC_CANCEL']}:9")

    with (
        patch("app.handlers.broadcast_panel.broadcast_repo.get_by_id", AsyncMock(return_value=SimpleNamespace(id=9, status="pending"))),
        patch("app.handlers.broadcast_panel.BroadcastServiceV2.cancel", AsyncMock()) as cancel_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    cancel_mock.assert_not_awaited()
    query.message.edit_text.assert_awaited_once()
    callbacks = _callback_data_set(query.message.edit_text.await_args.kwargs["reply_markup"])
    assert any(
        cb.startswith(f"{CB['BC_CANCEL_CONFIRM_PREFIX']}9:{query.from_user.id}:")
        for cb in callbacks
    )
    assert any(
        cb.startswith(f"{CB['BC_CANCEL_ABORT_PREFIX']}9:{query.from_user.id}:")
        for cb in callbacks
    )


@pytest.mark.asyncio
async def test_broadcast_cancel_confirm_same_user_executes_cancel():
    bot = _RecorderBot()
    broadcast_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bc_cancel_confirm")
    query = _query(f"{CB['BC_CANCEL_CONFIRM_PREFIX']}9:123456789:{int(time.time())}")

    with (
        patch("app.handlers.broadcast_panel.broadcast_repo.get_by_id", AsyncMock(return_value=SimpleNamespace(id=9, status="running"))),
        patch("app.handlers.broadcast_panel.broadcast_repo.get_recent", AsyncMock(return_value=[])),
        patch("app.handlers.broadcast_panel.BroadcastServiceV2.cancel", AsyncMock()) as cancel_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    cancel_mock.assert_awaited_once_with(9)
    query.answer.assert_awaited_once()
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_cancel_confirm_other_user_is_rejected():
    bot = _RecorderBot()
    broadcast_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bc_cancel_confirm")
    query = _query(f"{CB['BC_CANCEL_CONFIRM_PREFIX']}9:999999:{int(time.time())}")

    with patch("app.handlers.broadcast_panel.BroadcastServiceV2.cancel", AsyncMock()) as cancel_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    cancel_mock.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_broadcast_cancel_stale_confirmation_is_rejected():
    bot = _RecorderBot()
    broadcast_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bc_cancel_confirm")
    query = _query(f"{CB['BC_CANCEL_CONFIRM_PREFIX']}9:123456789:1")

    with patch("app.handlers.broadcast_panel.BroadcastServiceV2.cancel", AsyncMock()) as cancel_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    cancel_mock.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", ["bc:cancel:", "bc:cancel:abc", "bc:cancel:-1"])
async def test_broadcast_cancel_malformed_payloads_are_rejected(payload: str):
    bot = _RecorderBot()
    broadcast_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bc_cancel_invalid")
    query = _query(payload)

    await handler.__wrapped__(SimpleNamespace(), query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_broadcast_cancel_unauthorized_user_cannot_start_cancel():
    bot = _RecorderBot()
    broadcast_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bc_cancel")
    query = _query(f"{CB['BC_CANCEL']}:9", user_id=999999)

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.handlers.broadcast_panel.BroadcastServiceV2.cancel", AsyncMock()) as cancel_mock,
    ):
        await handler(SimpleNamespace(), query)

    cancel_mock.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_broadcast_cancel_done_status_is_not_cancelled():
    bot = _RecorderBot()
    broadcast_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "bc_cancel_confirm")
    query = _query(f"{CB['BC_CANCEL_CONFIRM_PREFIX']}9:123456789:{int(time.time())}")

    with (
        patch("app.handlers.broadcast_panel.broadcast_repo.get_by_id", AsyncMock(return_value=SimpleNamespace(id=9, status="done"))),
        patch("app.handlers.broadcast_panel.BroadcastServiceV2.cancel", AsyncMock()) as cancel_mock,
    ):
        await handler.__wrapped__(SimpleNamespace(), query)

    cancel_mock.assert_not_awaited()
    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_broadcast_history_and_detail_callbacks_still_render():
    broadcast = SimpleNamespace(
        id=9,
        target_scope="users",
        status="pending",
        sent_count=0,
        total_recipients=10,
    )
    callbacks = _callback_data_set(KeyboardFactory.broadcast_history("en", [broadcast]))

    assert f"{CB['BC_DETAIL']}:9" in callbacks
    assert f"{CB['BC_CANCEL']}:9" in callbacks


def test_sudo_link_menu_and_actions_not_on_visible_panels():
    """Sudo invite/referral link controls are hidden from visible panels."""
    users_callbacks = _callback_data_set(KeyboardFactory.dev_sub_users("en"))
    assert CB["DEV_SUDO_LINK_MENU"] not in users_callbacks

    hidden = {
        CB["DEV_SUDO_LINK_MENU"],
        CB["DEV_SUDO_LINK_SET"],
        CB["DEV_SUDO_LINK_RM"],
        CB["DEV_SUDO_LINK_LIST"],
    }
    for kb in (
        KeyboardFactory.developer_panel("en"),
        KeyboardFactory.dev_sub_settings("en"),
        KeyboardFactory.dev_sub_texts("en"),
        KeyboardFactory.dev_sub_users("en"),
    ):
        assert hidden.isdisjoint(_callback_data_set(kb))


@pytest.mark.asyncio
async def test_sudo_link_flow_rejects_unauthorized_user():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_sudo_link_list")
    query = _query(CB["DEV_SUDO_LINK_LIST"], user_id=999999)

    with patch("app.utils.decorators.is_developer", return_value=False):
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited_once()
    assert query.answer.await_args.kwargs.get("show_alert") is True


def test_sudo_link_malformed_prefix_has_no_broad_handler():
    src = Path("app/handlers/dev_panel.py").read_text(encoding="utf-8")

    assert "dev:sudo:link:" not in src
    assert "DEV_SUDO_LINK_SET" in src
    assert "DEV_SUDO_LINK_RM" in src
    assert "DEV_SUDO_LINK_LIST" in src

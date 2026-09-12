"""Tests for global user ban (ban-all / بن آل)."""
from __future__ import annotations

import os
import sys
import time
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
    pyromod_exceptions.ListenerStopped = Exception
    pyromod_module.exceptions = pyromod_exceptions
    sys.modules["pyromod"] = pyromod_module
    sys.modules["pyromod.exceptions"] = pyromod_exceptions

from app.config.settings import settings
from app.handlers import dev_banall_panel, global_ban_guard, start
from app.repositories.global_ban_repo import GlobalBanValidationError, validate_global_ban_user_id
from app.services.texts_links_ui import AskResult
from app.services.global_ban_service import RemovalSummary, remove_user_from_installed_chats
from app.utils.bot_guards import deny_if_globally_banned
from app.utils.i18n import t
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator

    def on_message(self, *args, **kwargs):  # noqa: ANN002, ANN003
        def _decorator(fn):
            self.message_handlers.append(fn)
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


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


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


def test_validate_rejects_developer_self_ban():
    with pytest.raises(GlobalBanValidationError):
        validate_global_ban_user_id(settings.DEVELOPER_ID)


def test_validate_rejects_non_positive_id():
    with pytest.raises(GlobalBanValidationError):
        validate_global_ban_user_id(0)


def test_dev_moderation_has_global_ban_button():
    moderation = KeyboardFactory.dev_sub_moderation("fa")
    users = KeyboardFactory.dev_sub_users("fa")
    assert CB["DEV_BANALL_HOME"] in _kb_callbacks(moderation)
    assert CB["DEV_BANALL_HOME"] not in _kb_callbacks(users)


def test_dev_banall_home_keyboard():
    kb = KeyboardFactory.dev_banall_home("fa")
    callbacks = _kb_callbacks(kb)
    assert CB["DEV_BANALL_ADD"] in callbacks
    assert CB["DEV_BANALL_REMOVE"] in callbacks
    assert f"{CB['DEV_BANALL_LIST_PREFIX']}0" in callbacks
    assert CB["DEV_BANALL_CLEAR"] in callbacks


@pytest.mark.asyncio
async def test_add_global_ban_idempotent_flag():
    from app.repositories import global_ban_repo

    existing = SimpleNamespace(user_id=555001, is_active=True)
    with (
        patch("app.repositories.global_ban_repo.async_session") as mock_session,
        patch("app.repositories.global_ban_repo.set_global_ban_cached", AsyncMock()),
    ):
        session = AsyncMock()
        mock_session.return_value.__aenter__.return_value = session
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=result_mock)

        _ban, created_new = await global_ban_repo.add_global_ban(555001, created_by=1)

    assert created_new is False
    assert _ban is existing


@pytest.mark.asyncio
async def test_deny_if_globally_banned_blocks_user():
    query = _pm_query(777001, "pb:stop")
    with (
        patch(
            "app.utils.bot_guards.global_ban_repo.is_globally_banned",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.utils.bot_guards._notify_blocked",
            AsyncMock(),
        ) as notify_mock,
    ):
        blocked = await deny_if_globally_banned(query, 777001)

    assert blocked is True
    notify_mock.assert_awaited_once_with(query, "global_ban.user_blocked")


@pytest.mark.asyncio
async def test_developer_bypasses_global_ban_guard():
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=settings.DEVELOPER_ID),
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
    )
    with patch(
        "app.utils.bot_guards.global_ban_repo.is_globally_banned",
        AsyncMock(return_value=True),
    ) as ban_mock:
        blocked = await deny_if_globally_banned(message, settings.DEVELOPER_ID)

    assert blocked is False
    ban_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_ban_message_guard_stops_propagation():
    bot = _RecorderBot()
    global_ban_guard.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.message_handlers, "global_ban_message_guard")

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=888001),
        stop_propagation=MagicMock(),
    )
    with patch(
        "app.handlers.global_ban_guard.deny_if_globally_banned",
        AsyncMock(return_value=True),
    ):
        await handler(SimpleNamespace(), message)

    message.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_start_handler_blocks_globally_banned_user():
    bot = _RecorderBot()
    start.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.message_handlers, "start_handler")

    message = SimpleNamespace(
        from_user=SimpleNamespace(id=888002, username="banned", first_name="Banned"),
        chat=SimpleNamespace(id=888002, type=SimpleNamespace(value="private")),
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
    )
    client = SimpleNamespace(stop_listening=AsyncMock())

    with patch(
        "app.handlers.start.deny_if_globally_banned",
        AsyncMock(return_value=True),
    ):
        await handler(client, message)

    message.stop_propagation.assert_called_once()
    message.reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_user_from_installed_chats_summary():
    group = SimpleNamespace(chat_id=-1001)
    channel = SimpleNamespace(chat_id=-1002)

    client = SimpleNamespace(ban_chat_member=AsyncMock())

    with (
        patch("app.services.global_ban_service.group_repo.get_all_active_groups", AsyncMock(return_value=[group])),
        patch("app.services.global_ban_service.channel_repo.get_all_active_channels", AsyncMock(return_value=[channel])),
    ):
        summary = await remove_user_from_installed_chats(client, 555001, concurrency=2)

    assert summary.attempted == 2
    assert summary.removed == 2
    assert summary.failed == 0
    assert client.ban_chat_member.await_count == 2


@pytest.mark.asyncio
async def test_remove_handles_admin_invalid_as_skipped():
    group = SimpleNamespace(chat_id=-1001)

    class UserAdminInvalid(Exception):
        pass

    client = SimpleNamespace(
        ban_chat_member=AsyncMock(side_effect=UserAdminInvalid("admin")),
    )

    with (
        patch("app.services.global_ban_service.group_repo.get_all_active_groups", AsyncMock(return_value=[group])),
        patch("app.services.global_ban_service.channel_repo.get_all_active_channels", AsyncMock(return_value=[])),
    ):
        summary = await remove_user_from_installed_chats(client, 555001)

    assert summary.attempted == 1
    assert summary.skipped == 1
    assert summary.removed == 0


@pytest.mark.asyncio
async def test_dev_banall_add_rejects_duplicate_without_removal():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_add")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_BANALL_ADD"])
    client = SimpleNamespace(send_message=AsyncMock())

    with (
        patch(
            "app.handlers.dev_banall_panel._ask",
            AsyncMock(return_value=AskResult(message=SimpleNamespace(text="555001"))),
        ),
        patch(
            "app.handlers.dev_banall_panel.global_ban_repo.add_global_ban",
            AsyncMock(return_value=(SimpleNamespace(user_id=555001), False)),
        ),
        patch("app.handlers.dev_banall_panel.asyncio.create_task") as task_mock,
        patch("app.handlers.dev_banall_panel._send_done", AsyncMock()) as done_mock,
    ):
        await handler(client, query)

    task_mock.assert_not_called()
    done_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_dev_banall_clear_wrong_user_rejected():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_clear_confirm")
    issued_at = int(time.time())
    query = _pm_query(999888, f"{CB['DEV_BANALL_CLEAR_DO_PREFIX']}{settings.DEVELOPER_ID}:{issued_at}")

    with patch("app.handlers.dev_banall_panel.global_ban_repo.clear_global_bans", AsyncMock()) as clear_mock:
        await handler(SimpleNamespace(), query)

    query.answer.assert_awaited()
    clear_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_banall_clear_stale_token_rejected():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_clear_confirm")
    stale_ts = int(time.time()) - 9999
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_BANALL_CLEAR_DO_PREFIX']}{settings.DEVELOPER_ID}:{stale_ts}")

    with patch("app.handlers.dev_banall_panel.global_ban_repo.clear_global_bans", AsyncMock()) as clear_mock:
        await handler(SimpleNamespace(), query)

    clear_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_banall_list_renders_rows():
    bot = _RecorderBot()
    dev_banall_panel.register(bot, SimpleNamespace())
    handler = _handler_by_name(bot.callback_handlers, "dev_banall_list")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_BANALL_LIST_PREFIX']}0")
    ban = SimpleNamespace(
        user_id=555001,
        reason="test",
        created_by=1,
        created_at=SimpleNamespace(strftime=lambda _fmt: "2026-06-04"),
        is_active=True,
    )

    with patch(
        "app.handlers.dev_banall_panel.global_ban_repo.list_global_bans",
        AsyncMock(return_value=([ban], 1)),
    ):
        await handler(SimpleNamespace(), query)

    query.message.edit_text.assert_awaited_once()
    kb = query.message.edit_text.await_args.kwargs["reply_markup"]
    assert f"{CB['DEV_BANALL_RM_PREFIX']}555001:0" in _kb_callbacks(kb)

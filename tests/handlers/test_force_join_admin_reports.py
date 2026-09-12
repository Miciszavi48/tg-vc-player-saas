from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
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

from app.handlers import dev_panel, owner_panel
from app.repositories.admin_report_repo import ChatInstallRow
from app.services.forced_membership_service import ForcedMembershipService
from app.services.wizard_ui import (
    TOKEN_DEV_FORCE_JOIN,
    TOKEN_DEV_LISTS,
    TOKEN_DEV_SETTINGS,
    TOKEN_OWNER_ROOT,
)
from app.utils.ui import CB


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


def _kb_callbacks(kb) -> set[str]:
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _pm_query(user_id: int, data: str = "dummy"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
        ),
    )


@pytest.mark.asyncio
async def test_force_join_add_remove_list_dev():
    bot = _RecorderBot()
    dev_panel.register(bot, None)

    add_handler = _handler_by_name(bot.callback_handlers, "dev_force_join_add")
    list_handler = _handler_by_name(bot.callback_handlers, "dev_force_join_list")
    rm_handler = _handler_by_name(bot.callback_handlers, "dev_force_join_remove")

    query_add = _pm_query(123456789, CB["DEV_FORCE_JOIN_ADD"])
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="@my_channel")),
    )
    target = SimpleNamespace(
        channel_id=-100123,
        channel_username="my_channel",
        display_name="My Channel",
        verify_status="ok",
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.ForcedMembershipService.add_target", AsyncMock(return_value={"target": target, "verify_status": "ok"})) as add_mock,
        patch("app.handlers.dev_panel.force_join_repo.get_active_targets_page", AsyncMock(return_value=([target], 1))),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await add_handler.__wrapped__(client, query_add)

    assert query_add.answer.await_count == 1
    assert add_mock.await_count == 1
    assert query_add.message.edit_text.await_count == 1
    kb = query_add.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert any(cb.startswith(CB["DEV_FORCE_JOIN_REMOVE_PREFIX"]) for cb in cbs)
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_FORCE_JOIN}" in cbs
    assert CB["WZ_HOME"] in cbs

    query_list = _pm_query(123456789, CB["DEV_FORCE_JOIN_LIST"])
    with patch("app.handlers.dev_panel.force_join_repo.get_active_targets_page", AsyncMock(return_value=([target], 1))):
        await list_handler.__wrapped__(SimpleNamespace(), query_list)

    assert query_list.answer.await_count == 1
    assert query_list.message.edit_text.await_count == 1

    query_rm = _pm_query(123456789, f"{CB['DEV_FORCE_JOIN_REMOVE_PREFIX']}-100123:0")
    with (
        patch("app.handlers.dev_panel.force_join_repo.get_active_target_by_channel_id", AsyncMock(return_value=target)),
        patch("app.handlers.dev_panel.ForcedMembershipService.remove_target", AsyncMock()) as rm_mock,
    ):
        await rm_handler.__wrapped__(SimpleNamespace(), query_rm)

    assert query_rm.answer.await_count == 1
    rm_mock.assert_not_awaited()
    assert query_rm.message.edit_text.await_count == 1
    confirm_cbs = _kb_callbacks(query_rm.message.edit_text.call_args.kwargs["reply_markup"])
    exec_cb = next(
        cb for cb in confirm_cbs if cb.startswith(CB["DEV_FORCE_JOIN_REMOVE_EXEC_PREFIX"])
    )
    assert exec_cb

    confirm_handler = _handler_by_name(bot.callback_handlers, "dev_force_join_remove_confirm")
    query_confirm = _pm_query(123456789, exec_cb)
    with (
        patch("app.handlers.dev_panel.force_join_repo.get_active_target_by_channel_id", AsyncMock(return_value=target)),
        patch("app.handlers.dev_panel.ForcedMembershipService.remove_target", AsyncMock()) as rm_mock,
        patch("app.handlers.dev_panel.force_join_repo.get_active_targets_page", AsyncMock(return_value=([], 1))),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await confirm_handler.__wrapped__(SimpleNamespace(), query_confirm)

    assert rm_mock.await_count == 1
    assert rm_mock.await_args.args[0] == -100123


@pytest.mark.asyncio
async def test_force_join_remove_confirm_rejects_other_user():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_force_join_remove_confirm")
    query = _pm_query(
        999999,
        f"{CB['DEV_FORCE_JOIN_REMOVE_EXEC_PREFIX']}-100123:0:123456789:{int(time.time())}",
    )
    with patch("app.handlers.dev_panel.ForcedMembershipService.remove_target", AsyncMock()) as rm_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    rm_mock.assert_not_awaited()
    assert query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_force_join_remove_abort_does_not_remove():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    target = SimpleNamespace(channel_id=-100123, channel_username="ch", display_name="Ch", verify_status="ok")
    abort_handler = _handler_by_name(bot.callback_handlers, "dev_force_join_remove_abort")
    issued_at = int(time.time())
    query = _pm_query(
        123456789,
        f"{CB['DEV_FORCE_JOIN_REMOVE_ABORT_PREFIX']}-100123:0:123456789:{issued_at}",
    )
    with (
        patch("app.handlers.dev_panel.ForcedMembershipService.remove_target", AsyncMock()) as rm_mock,
        patch("app.handlers.dev_panel.force_join_repo.get_active_targets_page", AsyncMock(return_value=([target], 1))),
    ):
        await abort_handler.__wrapped__(SimpleNamespace(), query)

    rm_mock.assert_not_awaited()
    assert query.message.edit_text.await_count == 1


@pytest.mark.asyncio
async def test_force_join_list_owner():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_force_join_list")

    query = _pm_query(900001, CB["OWN_FORCE_JOIN_LIST"])
    target = SimpleNamespace(
        channel_id=-10099,
        channel_username="owner_channel",
        display_name="Owner Channel",
        verify_status="ok",
    )

    with (
        patch("app.handlers.owner_panel._can_manage_force_join", AsyncMock(return_value=True)),
        patch("app.handlers.owner_panel.force_join_repo.get_active_targets_page", AsyncMock(return_value=([target], 1))),
    ):
        await handler(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert any(cb.startswith(CB["OWN_FORCE_JOIN_REMOVE_PREFIX"]) for cb in cbs)
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_OWNER_ROOT}" in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
async def test_force_join_denied_sudo_if_applicable():
    bot = _RecorderBot()
    owner_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "own_force_join_list")

    query = _pm_query(777777, CB["OWN_FORCE_JOIN_LIST"])

    with patch("app.handlers.owner_panel._can_manage_force_join", AsyncMock(return_value=False)):
        await handler(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_OWNER_ROOT}" in cbs
    assert CB["WZ_HOME"] in cbs


@pytest.mark.asyncio
async def test_force_join_cache_invalidation():
    target = SimpleNamespace(channel_id=-10012)
    client = SimpleNamespace(
        get_chat=AsyncMock(return_value=SimpleNamespace(
            id=-10012,
            username="chanx",
            invite_link=None,
            title="Chan X",
            type=SimpleNamespace(value="channel"),
        )),
        get_me=AsyncMock(return_value=SimpleNamespace(id=999)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))),
    )

    with (
        patch("app.services.forced_membership_service.force_join_repo.upsert_target", AsyncMock(return_value=target)),
        patch("app.services.forced_membership_service.invalidate_fm_targets", AsyncMock()) as inv_mock,
        patch("app.services.forced_membership_service.force_join_repo.deactivate", AsyncMock()) as deact_mock,
    ):
        result = await ForcedMembershipService.add_target(client, "@chanx", 123)
        await ForcedMembershipService.remove_target(-10012)

    assert result["target"] == target
    assert inv_mock.await_count == 2
    assert deact_mock.await_count == 1


@pytest.mark.asyncio
async def test_dev_list_groups_paginated():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_groups")

    query = _pm_query(123456789, CB["DEV_LIST_GROUPS"])
    row = ChatInstallRow(
        chat_id=-1001,
        chat_type="group",
        title="Group A",
        invite_link="https://t.me/joinchat/a",
        credit_days=10,
        expire_at=None,
        status="active",
    )

    with patch("app.handlers.dev_panel.admin_report_repo.get_groups_page", AsyncMock(return_value=([row], 2))):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert f"{CB['PAGE_DEV_GROUPS']}1" in cbs
    assert any(cb.startswith(CB["DEV_LEAVE_CONFIRM_PREFIX"]) for cb in cbs)


@pytest.mark.asyncio
async def test_dev_list_no_credit():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_no_credit")

    query = _pm_query(123456789, CB["DEV_LIST_NO_CREDIT"])
    row = ChatInstallRow(
        chat_id=-1002,
        chat_type="channel",
        title="Channel A",
        invite_link=None,
        credit_days=0,
        expire_at=None,
        status="active",
    )

    with patch("app.handlers.dev_panel.admin_report_repo.get_no_credit_page", AsyncMock(return_value=([row], 1))):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1


@pytest.mark.asyncio
async def test_dev_list_renewal():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_renewal_groups")

    query = _pm_query(123456789, CB["DEV_LIST_RENEWAL_GROUPS"])
    row = ChatInstallRow(
        chat_id=-1003,
        chat_type="group",
        title="Group Renewal",
        invite_link=None,
        credit_days=1,
        expire_at=datetime.now(timezone.utc),
        status="active",
    )

    with patch("app.handlers.dev_panel.admin_report_repo.get_renewal_page", AsyncMock(return_value=([row], 1))):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1


@pytest.mark.asyncio
async def test_dev_leave_group_confirm():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_leave_group")

    query = _pm_query(123456789, CB["DEV_LEAVE_GROUP"])
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="-10077")),
    )
    row = ChatInstallRow(
        chat_id=-10077,
        chat_type="group",
        title="Leave Me",
        invite_link=None,
        credit_days=1,
        expire_at=None,
        status="active",
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
    ):
        await handler.__wrapped__(client, query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert any(cb.startswith(CB["DEV_LEAVE_EXEC_PREFIX"]) for cb in cbs)
    assert any(cb.startswith(CB["DEV_LEAVE_CANCEL_PREFIX"]) for cb in cbs)
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_LISTS}" in cbs
    assert CB["WZ_HOME"] in cbs

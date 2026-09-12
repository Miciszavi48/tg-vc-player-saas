"""Developer list row actions: detail, links, credit, leave confirmation."""
from __future__ import annotations

import os
import sys
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

from app.config.settings import settings
from app.handlers import dev_panel
from app.repositories.admin_report_repo import ChatInstallRow, UserListRow
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


def _kb_callback_rows(kb) -> list[list[str | None]]:
    return [[btn.callback_data for btn in row] for row in kb.inline_keyboard]


def _kb_text_rows(kb) -> list[list[str]]:
    return [[btn.text for btn in row] for row in kb.inline_keyboard]


def _kb_urls(kb) -> set[str]:
    return {
        btn.url
        for row in kb.inline_keyboard
        for btn in row
        if btn.url
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


def _group_row(**overrides) -> ChatInstallRow:
    base = dict(
        chat_id=-1001,
        chat_type="group",
        title="Group A",
        invite_link="https://t.me/joinchat/abc",
        credit_days=10,
        expire_at=None,
        status="active",
        installed_by=555,
    )
    base.update(overrides)
    return ChatInstallRow(**base)


def _channel_row(**overrides) -> ChatInstallRow:
    base = dict(
        chat_id=-1002,
        chat_type="channel",
        title="Channel A",
        invite_link="https://t.me/publicchan",
        credit_days=5,
        expire_at=None,
        status="active",
        installed_by=666,
    )
    base.update(overrides)
    return ChatInstallRow(**base)


def _user_row(**overrides) -> UserListRow:
    base = dict(
        user_id=777,
        username="tester",
        first_name="Tester",
        is_banned=False,
        last_seen=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return UserListRow(**base)


@pytest.mark.asyncio
async def test_group_list_rows_include_action_buttons():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_groups")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_GROUPS"])
    row = _group_row()

    with patch("app.handlers.dev_panel.admin_report_repo.get_groups_page", AsyncMock(return_value=([row], 1))):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    payload = f"g:g:{row.chat_id}:0"
    assert f"{CB['DEV_LIST_DETAIL_PREFIX']}{payload}" in cbs
    assert f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}{payload}" in cbs
    assert f"{CB['DEV_LIST_CREDIT_DEC_PREFIX']}{payload}" in cbs
    assert f"{CB['DEV_LEAVE_CONFIRM_PREFIX']}{row.chat_id}:g:0" in cbs


@pytest.mark.asyncio
async def test_group_list_rows_are_compact_paginated_and_rtl_ordered():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_groups")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_GROUPS"])
    row = _group_row()
    get_groups = AsyncMock(return_value=([row], 2))

    with patch("app.handlers.dev_panel.admin_report_repo.get_groups_page", get_groups):
        await handler.__wrapped__(SimpleNamespace(), query)

    get_groups.assert_awaited_once_with(0, page_size=3)
    text = query.message.edit_text.call_args.args[0]
    assert "1) Group A" in text
    assert "▫️ لینک: موجود" in text
    assert "https://t.me" not in text
    assert "صفحه 1 از 2" in text
    assert "[missing:" not in text
    assert query.message.edit_text.call_args.kwargs["disable_web_page_preview"] is True

    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    rows = _kb_text_rows(kb)
    assert ["🔎 جزئیات", "🔗 لینک"] in rows
    assert ["➖ کسر", "🚪 خروج", "➕ شارژ"] in rows

    payload = f"g:g:{row.chat_id}:0"
    callback_rows = _kb_callback_rows(kb)
    assert [
        f"{CB['DEV_LIST_CREDIT_DEC_PREFIX']}{payload}",
        f"{CB['DEV_LEAVE_CONFIRM_PREFIX']}{row.chat_id}:g:0",
        f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}{payload}",
    ] in callback_rows


@pytest.mark.asyncio
async def test_channel_list_rows_include_action_buttons():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_channels")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_CHANNELS"])
    row = _channel_row()

    with patch("app.handlers.dev_panel.admin_report_repo.get_channels_page", AsyncMock(return_value=([row], 1))):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    payload = f"c:c:{row.chat_id}:0"
    assert f"{CB['DEV_LIST_DETAIL_PREFIX']}{payload}" in cbs
    assert f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}{payload}" in cbs


@pytest.mark.asyncio
async def test_public_invite_link_creates_url_button():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_channels")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_CHANNELS"])
    row = _channel_row(invite_link="https://t.me/publicchan")

    with patch("app.handlers.dev_panel.admin_report_repo.get_channels_page", AsyncMock(return_value=([row], 1))):
        await handler.__wrapped__(SimpleNamespace(), query)

    urls = _kb_urls(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert "https://t.me/publicchan" in urls


@pytest.mark.asyncio
async def test_missing_link_does_not_create_url_button():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_groups")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_GROUPS"])
    row = _group_row(invite_link=None)

    with patch("app.handlers.dev_panel.admin_report_repo.get_groups_page", AsyncMock(return_value=([row], 1))):
        await handler.__wrapped__(SimpleNamespace(), query)

    urls = _kb_urls(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert urls == set()
    text = query.message.edit_text.call_args.args[0]
    assert "▫️ لینک: نامشخص" in text
    assert "https://t.me" not in text


@pytest.mark.asyncio
async def test_extended_resource_lists_use_three_item_pages():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_extended")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_PLAYBACK_GROUPS"])
    row = _group_row()
    get_playback = AsyncMock(return_value=([row], 1))

    with patch("app.handlers.dev_panel.admin_report_repo.get_playback_groups_page", get_playback):
        await handler.__wrapped__(SimpleNamespace(), query)

    get_playback.assert_awaited_once_with(0, page_size=3)


@pytest.mark.asyncio
async def test_active_user_lists_use_three_item_pages():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_active_users")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_ACTIVE_USERS"])
    row = _user_row()
    get_users = AsyncMock(return_value=([row], 2, 4))

    with patch("app.handlers.dev_panel.admin_report_repo.get_users_page", get_users):
        await handler.__wrapped__(SimpleNamespace(), query)

    get_users.assert_awaited_once_with(0, page_size=3, banned=False)
    text = query.message.edit_text.call_args.args[0]
    assert "صفحه 1 از 2" in text
    assert "[missing:" not in text


@pytest.mark.asyncio
async def test_empty_resource_lists_render_cleanly():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_groups")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_GROUPS"])

    with patch("app.handlers.dev_panel.admin_report_repo.get_groups_page", AsyncMock(return_value=([], 1))):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "موردی در این لیست وجود ندارد" in text
    assert "[missing:" not in text


@pytest.mark.asyncio
async def test_pagination_buttons_still_work():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_groups")
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_GROUPS"])
    row = _group_row()

    with patch("app.handlers.dev_panel.admin_report_repo.get_groups_page", AsyncMock(return_value=([row], 3))):
        await handler.__wrapped__(SimpleNamespace(), query)

    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['PAGE_DEV_GROUPS']}1" in cbs


@pytest.mark.asyncio
async def test_detail_view_shows_metadata():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_detail")
    row = _group_row(expire_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LIST_DETAIL_PREFIX']}g:g:{row.chat_id}:2")

    with patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "Group A" in text
    assert str(row.chat_id) in text
    assert "555" in text


@pytest.mark.asyncio
async def test_back_from_detail_returns_same_list_page():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_back")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LIST_BACK_PREFIX']}g:2")
    row = _group_row()

    with patch("app.handlers.dev_panel.admin_report_repo.get_groups_page", AsyncMock(return_value=([row], 3))):
        await handler.__wrapped__(SimpleNamespace(), query)

    assert query.message.edit_text.await_count == 1
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['PAGE_DEV_GROUPS']}1" in cbs
    assert f"{CB['PAGE_DEV_GROUPS']}3" not in cbs or f"{CB['PAGE_DEV_GROUPS']}2" in cbs


@pytest.mark.asyncio
async def test_row_charge_group_uses_group_chat_type():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_credit_inc")
    row = _group_row()
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}g:g:{row.chat_id}:0")
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="5")),
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.CreditService.charge_managed_chat", AsyncMock()) as charge_mock,
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await handler.__wrapped__(client, query)

    charge_mock.assert_awaited_once_with(
        row.chat_id,
        "group",
        5,
        operated_by=settings.DEVELOPER_ID,
        note="developer_report_list",
    )


@pytest.mark.asyncio
async def test_row_charge_channel_uses_channel_chat_type():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_credit_inc")
    row = _channel_row()
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}c:c:{row.chat_id}:1")
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="3")),
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.CreditService.charge_managed_chat", AsyncMock()) as charge_mock,
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await handler.__wrapped__(client, query)

    charge_mock.assert_awaited_once_with(
        row.chat_id,
        "channel",
        3,
        operated_by=settings.DEVELOPER_ID,
        note="developer_report_list",
    )


@pytest.mark.asyncio
async def test_row_deduct_group():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_credit_dec")
    row = _group_row()
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LIST_CREDIT_DEC_PREFIX']}g:g:{row.chat_id}:0")
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="2")),
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.CreditService.adjust_managed_credit", AsyncMock()) as deduct_mock,
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await handler.__wrapped__(client, query)

    deduct_mock.assert_awaited_once_with(
        row.chat_id,
        "group",
        mode="decrease",
        amount=2,
        operated_by=settings.DEVELOPER_ID,
        note="developer_report_list",
    )


@pytest.mark.asyncio
async def test_row_deduct_channel():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_credit_dec")
    row = _channel_row()
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LIST_CREDIT_DEC_PREFIX']}n:c:{row.chat_id}:0")
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="1")),
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.CreditService.adjust_managed_credit", AsyncMock()) as deduct_mock,
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
    ):
        await handler.__wrapped__(client, query)

    deduct_mock.assert_awaited_once_with(
        row.chat_id,
        "channel",
        mode="decrease",
        amount=1,
        operated_by=settings.DEVELOPER_ID,
        note="developer_report_list",
    )


@pytest.mark.asyncio
async def test_invalid_days_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_credit_inc")
    row = _group_row()
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}g:g:{row.chat_id}:0")
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(side_effect=[SimpleNamespace(text="abc"), SimpleNamespace(text="/cancel")]),
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.CreditService.charge_managed_chat", AsyncMock()) as charge_mock,
    ):
        await handler.__wrapped__(client, query)

    charge_mock.assert_not_awaited()
    assert client.ask.await_count == 2
    retry_prompt = client.ask.call_args_list[1].args[1]
    assert "invalid" in retry_prompt.lower() or "نامعتبر" in retry_prompt


@pytest.mark.asyncio
async def test_zero_days_rejected():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_credit_inc")
    row = _group_row()
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}g:g:{row.chat_id}:0")
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(side_effect=[SimpleNamespace(text="0"), SimpleNamespace(text="/cancel")]),
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.CreditService.charge_managed_chat", AsyncMock()) as charge_mock,
    ):
        await handler.__wrapped__(client, query)

    charge_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_developer_cannot_trigger_row_credit():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_list_credit_inc")
    row = _group_row()
    query = _pm_query(99999, f"{CB['DEV_LIST_CREDIT_INC_PREFIX']}g:g:{row.chat_id}:0")

    with patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)):
        result = await handler(SimpleNamespace(), query)

    assert result is None
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_leave_still_requires_confirmation():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_leave_confirm")
    row = _group_row(chat_id=-100777)
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LEAVE_CONFIRM_PREFIX']}-100777:g:2")

    with patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)):
        await handler.__wrapped__(SimpleNamespace(), query)

    text = query.message.edit_text.call_args.args[0]
    assert "آیا از خروج این منبع مطمئن هستید؟" in text
    cbs = _kb_callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert f"{CB['DEV_LEAVE_EXEC_PREFIX']}-100777:g:2" in cbs
    assert f"{CB['DEV_LEAVE_CANCEL_PREFIX']}-100777:g:2" in cbs
    rows = _kb_text_rows(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert ["✅ بله، خروج", "❌ لغو"] in rows


@pytest.mark.asyncio
async def test_leave_cancel_does_not_deactivate():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_leave_cancel")
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LEAVE_CANCEL_PREFIX']}-100777:c:1")

    with patch("app.handlers.dev_panel._render_dev_report_page", AsyncMock()) as render_mock:
        await handler.__wrapped__(SimpleNamespace(), query)

    render_mock.assert_awaited_once()
    assert render_mock.call_args.args[2] == 1


@pytest.mark.asyncio
async def test_leave_confirm_group_calls_repos():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_leave_execute")
    row = _group_row(chat_id=-100777)
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LEAVE_EXEC_PREFIX']}-100777:g:0")
    client = SimpleNamespace(leave_chat=AsyncMock())

    with (
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.group_repo.deactivate_group", AsyncMock()) as deact_group,
        patch("app.handlers.dev_panel.channel_repo.deactivate_channel", AsyncMock()) as deact_chan,
        patch("app.handlers.dev_panel.log_repo.log_install", AsyncMock()),
        patch("app.handlers.dev_panel.NotificationService.notify_uninstall", AsyncMock()),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
        patch("app.handlers.dev_panel._render_dev_report_page", AsyncMock()),
    ):
        await handler.__wrapped__(client, query)

    deact_group.assert_awaited_once_with(-100777)
    deact_chan.assert_not_awaited()


@pytest.mark.asyncio
async def test_leave_confirm_channel_calls_repos():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_leave_execute")
    row = _channel_row(chat_id=-100888)
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LEAVE_EXEC_PREFIX']}-100888:c:0")
    client = SimpleNamespace(leave_chat=AsyncMock())

    with (
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.group_repo.deactivate_group", AsyncMock()) as deact_group,
        patch("app.handlers.dev_panel.channel_repo.deactivate_channel", AsyncMock()) as deact_chan,
        patch("app.handlers.dev_panel.log_repo.log_install", AsyncMock()),
        patch("app.handlers.dev_panel.NotificationService.notify_uninstall", AsyncMock()),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
        patch("app.handlers.dev_panel._render_dev_report_page", AsyncMock()),
    ):
        await handler.__wrapped__(client, query)

    deact_chan.assert_awaited_once_with(-100888)
    deact_group.assert_not_awaited()


@pytest.mark.asyncio
async def test_malformed_list_callbacks_do_not_crash():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    detail_handler = _handler_by_name(bot.callback_handlers, "dev_list_detail")
    credit_handler = _handler_by_name(bot.callback_handlers, "dev_list_credit_inc")
    back_handler = _handler_by_name(bot.callback_handlers, "dev_list_back")

    bad_detail = _pm_query(settings.DEVELOPER_ID, "dev:list:detail:bad")
    bad_credit = _pm_query(settings.DEVELOPER_ID, "dev:list:credit:inc:x")
    bad_back = _pm_query(settings.DEVELOPER_ID, "dev:list:back:zzz")

    await detail_handler.__wrapped__(SimpleNamespace(), bad_detail)
    await credit_handler.__wrapped__(SimpleNamespace(), bad_credit)
    await back_handler.__wrapped__(SimpleNamespace(), bad_back)

    assert bad_detail.answer.await_count >= 1
    assert bad_credit.answer.await_count >= 1
    assert bad_back.answer.await_count >= 1


@pytest.mark.asyncio
async def test_leave_return_page_preserved():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_leave_execute")
    row = _channel_row(chat_id=-100999)
    query = _pm_query(settings.DEVELOPER_ID, f"{CB['DEV_LEAVE_EXEC_PREFIX']}-100999:c:3")
    client = SimpleNamespace(leave_chat=AsyncMock())

    with (
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.group_repo.deactivate_group", AsyncMock()),
        patch("app.handlers.dev_panel.channel_repo.deactivate_channel", AsyncMock()),
        patch("app.handlers.dev_panel.log_repo.log_install", AsyncMock()),
        patch("app.handlers.dev_panel.NotificationService.notify_uninstall", AsyncMock()),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()),
        patch("app.handlers.dev_panel._render_dev_report_page", AsyncMock()) as render_mock,
    ):
        await handler.__wrapped__(client, query)

    assert render_mock.call_args.args[2] == 3

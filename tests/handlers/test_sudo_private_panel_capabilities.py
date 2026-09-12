from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select

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

from app.config.settings import settings
from app.database.engine import async_session
from app.database.models import Channel, CreditHistory, Group, GroupCredit, Owner, Sudo
from app.handlers import sudo_panel
from app.repositories.user_repo import SUDO_PERMISSION_FIELD_NAMES
from app.services.credit_service import CreditService
from app.utils.filters import dev_filter, owner_filter
from app.utils.ui import CB, KeyboardFactory


class _RecorderBot:
    def __init__(self):
        self.callback_handlers = []

    def on_callback_query(self, filters=None, group=0):
        def decorator(func):
            self.callback_handlers.append(SimpleNamespace(callback=func, filters=filters, group=group))
            return func

        return decorator


def _handler(bot: _RecorderBot, name: str):
    for item in bot.callback_handlers:
        if item.callback.__name__ == name:
            return item
    raise AssertionError(f"handler not registered: {name}")


def _callbacks(kb) -> list[str]:
    return [
        btn.callback_data
        for row in getattr(kb, "inline_keyboard", [])
        for btn in row
        if getattr(btn, "callback_data", None)
    ]


def _query(user_id: int, data: str, *, chat_type: str = "private"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        data=data,
        message=SimpleNamespace(
            id=None,
            chat=SimpleNamespace(id=user_id if chat_type == "private" else -10055, type=SimpleNamespace(value=chat_type)),
            text="panel",
            edit_text=AsyncMock(),
            edit_caption=AsyncMock(),
            edit_reply_markup=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


async def _cleanup_scope_rows(chat_ids: list[int], user_ids: list[int]) -> None:
    async with async_session() as session:
        async with session.begin():
            await session.execute(delete(CreditHistory).where(CreditHistory.chat_id.in_(chat_ids)))
            await session.execute(delete(GroupCredit).where(GroupCredit.chat_id.in_(chat_ids)))
            await session.execute(delete(Group).where(Group.chat_id.in_(chat_ids)))
            await session.execute(delete(Channel).where(Channel.chat_id.in_(chat_ids)))
            await session.execute(delete(Sudo).where(Sudo.user_id.in_(user_ids)))
            await session.execute(delete(Owner).where(Owner.user_id.in_(user_ids)))


def test_sudo_root_layout_emits_only_current_private_sections():
    kb = KeyboardFactory.sudo_panel("fa")
    rows = [[btn.callback_data for btn in row] for row in kb.inline_keyboard]

    assert rows == [
        [CB["SUDO_STATUS"]],
        [CB["SUDO_GROUPS"], CB["SUDO_CREDIT"]],
        [CB["SUDO_PERMISSIONS"]],
        [CB["SUDO_LISTS"]],
        [CB["NAV_START"]],
    ]
    callbacks = _callbacks(kb)
    assert not any(cb.startswith(("dev:", "own:")) for cb in callbacks)
    assert CB["SUDO_INSTALLS_REPORT"] not in callbacks
    assert CB["SUDO_CREDIT_REPORT"] not in callbacks
    assert CB["SUDO_MY_STATS"] not in callbacks
    assert CB["SUDO_LOW_CREDIT"] not in callbacks


@pytest.mark.asyncio
async def test_sudo_routes_are_private_chat_and_role_filtered():
    bot = _RecorderBot()
    sudo_panel.register(bot, None)
    route = _handler(bot, "sudo_groups")
    group_query = _query(90001, CB["SUDO_GROUPS"], chat_type="supergroup")
    dev_query = _query(settings.DEVELOPER_ID, CB["SUDO_GROUPS"])
    sudo_query = _query(90001, CB["SUDO_GROUPS"])
    owner_query = _query(90002, CB["SUDO_GROUPS"])
    regular_query = _query(90003, CB["SUDO_GROUPS"])

    with patch("app.utils.filters.user_repo.is_sudo", AsyncMock(side_effect=lambda uid: uid == 90001)):
        group_matched = await sudo_panel._pm_sudo(None, group_query)
        dev_matched = await sudo_panel._pm_sudo(None, dev_query)
        sudo_matched = await sudo_panel._pm_sudo(None, sudo_query)
        owner_matched = await sudo_panel._pm_sudo(None, owner_query)
        regular_matched = await sudo_panel._pm_sudo(None, regular_query)

    assert route.filters is not None
    assert group_matched is False
    assert dev_matched is True
    assert sudo_matched is True
    assert owner_matched is False
    assert regular_matched is False


@pytest.mark.asyncio
async def test_cross_role_forged_panel_filters_keep_surfaces_separate():
    sudo_query = _query(90001, CB["OWN_GROUPS"])
    owner_query = _query(90002, CB["SUDO_GROUPS"])
    regular_query = _query(90003, CB["SUDO_STATUS"])

    own_filter = owner_filter()
    developer_filter = dev_filter()

    with (
        patch("app.utils.filters.user_repo.is_sudo", AsyncMock(side_effect=lambda uid: uid == 90001)),
        patch("app.utils.filters.user_repo.is_owner", AsyncMock(side_effect=lambda uid: uid == 90002)),
    ):
        assert await own_filter(None, sudo_query) is False
        assert await developer_filter(None, sudo_query) is False
        assert await sudo_panel._pm_sudo(None, owner_query) is False
        assert await sudo_panel._pm_sudo(None, regular_query) is False


@pytest.mark.asyncio
async def test_sudo_callback_is_blocked_when_sudo_panel_disabled():
    bot = _RecorderBot()
    sudo_panel.register(bot, None)
    route = _handler(bot, "sudo_status").callback
    query = _query(90001, CB["SUDO_STATUS"])

    with (
        patch("app.utils.decorators.is_developer", return_value=False),
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.utils.decorators.user_repo.is_sudo", AsyncMock(return_value=True)),
        patch("app.utils.decorators.deny_if_sudo_panel_disabled", AsyncMock(return_value=True)) as deny,
    ):
        result = await route(SimpleNamespace(), query)

    assert result is None
    deny.assert_awaited_once()
    query.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_permissions_page_shows_effective_flags_without_toggle_callbacks():
    bot = _RecorderBot()
    sudo_panel.register(bot, None)
    route = _handler(bot, "sudo_permissions").callback.__wrapped__
    query = _query(90001, CB["SUDO_PERMISSIONS"])
    perms = {field: True for field in SUDO_PERMISSION_FIELD_NAMES}
    perms["can_manage_credit"] = False

    with patch("app.handlers.sudo_panel.user_repo.get_sudo_permissions", AsyncMock(return_value=perms)):
        await route(SimpleNamespace(), query)

    rendered = query.message.edit_text.await_args.args[0]
    callbacks = _callbacks(query.message.edit_text.call_args.kwargs["reply_markup"])
    assert "🔐" in rendered
    assert "❌" in rendered
    assert not any(cb.startswith(("sudo:sp:", "dev:sp:", "own:sp:")) for cb in callbacks)


def test_group_detail_buttons_follow_effective_permissions():
    row = SimpleNamespace(chat_id=-1001, title="Managed", credit_days=5)
    kb = sudo_panel._group_detail_kb(
        row,
        "active",
        0,
        {
            "can_manage_credit": False,
            "can_remove_bot": False,
        },
    )
    callbacks = _callbacks(kb)

    assert not any(cb.startswith(CB["SUDO_GRP_CREDIT_INC_PREFIX"]) for cb in callbacks)
    assert not any(cb.startswith(CB["SUDO_GRP_CREDIT_DEC_PREFIX"]) for cb in callbacks)
    assert not any(cb.startswith(CB["SUDO_GRP_LEAVE_CONFIRM_PREFIX"]) for cb in callbacks)
    assert f"{CB['SUDO_GRP_LIST_PREFIX']}active:0" in callbacks


@pytest.mark.asyncio
async def test_credit_update_uses_sudo_scoped_service_with_actor_attribution():
    query = _query(90001, CB["SUDO_CREDIT_ADD"])
    result = SimpleNamespace(after=12)

    with patch(
        "app.handlers.sudo_panel.CreditService.adjust_sudo_scoped_group_credit",
        AsyncMock(return_value=result),
    ) as scoped_update:
        await sudo_panel._execute_credit_update(
            SimpleNamespace(),
            query,
            target_id=-1001,
            days=7,
            mode="increase",
            return_cb=CB["SUDO_CREDIT"],
            title="Managed",
        )

    scoped_update.assert_awaited_once_with(
        90001,
        -1001,
        mode="increase",
        amount=7,
        note="sudo_panel",
    )
    rendered = query.message.edit_text.await_args.args[0]
    assert "12" in rendered


def test_leave_confirmation_payload_is_actor_and_ttl_bound():
    now = 123456
    payload = f"{CB['SUDO_GRP_LEAVE_EXEC_PREFIX']}active:-1001:2:90001:{now}"

    parsed = sudo_panel._parse_leave_payload(payload, CB["SUDO_GRP_LEAVE_EXEC_PREFIX"])

    assert parsed == ("active", -1001, 2, 90001, now)


@pytest.mark.asyncio
async def test_sudo_disposable_db_scope_queries_isolate_installed_by():
    ids = [-92001, -92002, -92003, -92004, -93001, -91001, -99001]
    users = [100, 200, 300]
    now = datetime.now(timezone.utc)
    await _cleanup_scope_rows(ids, users)
    async with async_session() as session:
        async with session.begin():
            session.add(Owner(user_id=100, display_name="Owner", is_active=True))
            session.add_all(
                [
                    Sudo(user_id=200, display_name="Sudo A", added_by=100, is_active=True, total_installs=4),
                    Sudo(user_id=300, display_name="Sudo B", added_by=100, is_active=True, total_installs=1),
                ]
            )
            session.add_all(
                [
                    Group(chat_id=-92001, chat_title="Alpha Active", status="active", installed_by=200),
                    Group(chat_id=-92002, chat_title="A2 Inactive", status="inactive", installed_by=200),
                    Group(chat_id=-92003, chat_title="A3 No Credit", status="active", installed_by=200),
                    Group(chat_id=-92004, chat_title="A4 Renewal", status="active", installed_by=200),
                    Group(chat_id=-93001, chat_title="Bravo Other Sudo", status="active", installed_by=300),
                    Group(chat_id=-91001, chat_title="Owner Group", status="active", installed_by=100),
                    Group(chat_id=-99001, chat_title="Developer Group", status="active", installed_by=999999),
                ]
            )
            session.add_all(
                [
                    GroupCredit(chat_id=-92001, chat_type="group", credit_days=12, status="active"),
                    GroupCredit(chat_id=-92002, chat_type="group", credit_days=8, status="active"),
                    GroupCredit(chat_id=-92003, chat_type="group", credit_days=0, status="expired"),
                    GroupCredit(
                        chat_id=-92004,
                        chat_type="group",
                        credit_days=2,
                        status="active",
                        expire_at=now + timedelta(days=2),
                    ),
                    GroupCredit(chat_id=-93001, chat_type="group", credit_days=5, status="active"),
                    GroupCredit(chat_id=-91001, chat_type="group", credit_days=5, status="active"),
                    GroupCredit(chat_id=-99001, chat_type="group", credit_days=5, status="active"),
                ]
            )

    active, _pages, _page = await sudo_panel._fetch_sudo_group_list(200, "active", 0)
    inactive, _pages, _page = await sudo_panel._fetch_sudo_group_list(200, "inactive", 0)
    no_credit, _pages, _page = await sudo_panel._fetch_sudo_group_list(200, "no_credit", 0)
    renewal, _pages, _page = await sudo_panel._fetch_sudo_group_list(200, "renewal", 0)
    searched = await sudo_panel._search_sudo_groups(200, "Alpha")
    hidden_search = await sudo_panel._search_sudo_groups(200, "Bravo")

    assert {row.chat_id for row in active} == {-92001, -92003, -92004}
    assert {row.chat_id for row in inactive} == {-92002}
    assert {row.chat_id for row in no_credit} == {-92003}
    assert {row.chat_id for row in renewal} == {-92004}
    assert [row.chat_id for row in searched] == [-92001]
    assert hidden_search == []
    assert await sudo_panel._get_sudo_group_row(200, -92001) is not None
    assert await sudo_panel._get_sudo_group_row(200, -93001) is None
    assert await sudo_panel._get_sudo_group_row(200, -91001) is None
    assert await sudo_panel._get_sudo_group_row(200, -99001) is None
    await _cleanup_scope_rows(ids, users)


@pytest.mark.asyncio
async def test_sudo_scoped_credit_mutation_uses_disposable_db_and_denies_foreign_targets():
    owned_chat = -92101
    dev_owned_chat = -92102
    foreign_ids = [-93101, -91101, -99101]
    all_ids = [owned_chat, dev_owned_chat, *foreign_ids]
    users = [100, 200, 300, int(settings.DEVELOPER_ID)]
    await _cleanup_scope_rows(all_ids, users)
    async with async_session() as session:
        async with session.begin():
            session.add(Owner(user_id=100, display_name="Owner", is_active=True))
            session.add_all(
                [
                    Sudo(user_id=200, display_name="Sudo A", added_by=100, is_active=True),
                    Sudo(user_id=300, display_name="Sudo B", added_by=100, is_active=True),
                ]
            )
            session.add_all(
                [
                    Group(chat_id=owned_chat, chat_title="Owned by Sudo", status="active", installed_by=200),
                    Group(chat_id=dev_owned_chat, chat_title="Owned by Dev", status="active", installed_by=int(settings.DEVELOPER_ID)),
                    Group(chat_id=-93101, chat_title="Other Sudo", status="active", installed_by=300),
                    Group(chat_id=-91101, chat_title="Owner Only", status="active", installed_by=100),
                    Group(chat_id=-99101, chat_title="Developer", status="active", installed_by=999999),
                ]
            )
            session.add_all(
                [
                    GroupCredit(chat_id=owned_chat, chat_type="group", credit_days=4, status="active"),
                    GroupCredit(chat_id=dev_owned_chat, chat_type="group", credit_days=4, status="active"),
                    GroupCredit(chat_id=-93101, chat_type="group", credit_days=4, status="active"),
                    GroupCredit(chat_id=-91101, chat_type="group", credit_days=4, status="active"),
                    GroupCredit(chat_id=-99101, chat_type="group", credit_days=4, status="active"),
                ]
            )

    with (
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="token")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
        patch("app.services.credit_service._clear_expired_pending_marker", AsyncMock()),
        patch("app.services.credit_service.track_event", AsyncMock()),
    ):
        with pytest.raises(ValueError, match="only_developer_can_mutate_credit"):
            await CreditService.adjust_sudo_scoped_group_credit(
                200,
                owned_chat,
                mode="increase",
                amount=3,
                note="sudo_scope_test",
            )

        result = await CreditService.adjust_sudo_scoped_group_credit(
            int(settings.DEVELOPER_ID),
            dev_owned_chat,
            mode="increase",
            amount=3,
            note="sudo_scope_test",
        )
        assert result.before == 4
        assert result.after == 7

    async with async_session() as session:
        history = (
            await session.execute(
                select(CreditHistory).where(
                    CreditHistory.chat_id == dev_owned_chat,
                    CreditHistory.operated_by == int(settings.DEVELOPER_ID),
                    CreditHistory.note == "sudo_scope_test",
                )
            )
        ).scalar_one()
        assert history.operation == "charge"
        assert history.amount_days == 3
    await _cleanup_scope_rows(all_ids, users)


@pytest.mark.asyncio
async def test_legacy_bulk_leave_uses_actor_install_log_scope_only():
    query = _query(200, CB["SUDO_LEAVE_INSTALLS"])
    client = SimpleNamespace(leave_chat=AsyncMock())
    call_py = SimpleNamespace()
    logs = [
        SimpleNamespace(chat_id=-92001, chat_type="group", action="install"),
        SimpleNamespace(chat_id=-93001, chat_type="group", action="remove"),
    ]

    with (
        patch("app.handlers.sudo_panel._require_remove_bot_permission", AsyncMock(return_value=True)),
        patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock(return_value=logs)) as get_logs,
        patch(
            "app.handlers.sudo_panel._get_current_sudo_leave_targets",
            AsyncMock(return_value={-92001: "group"}),
        ) as get_targets,
        patch("app.handlers.sudo_panel.CallService.leave_voice_chat", AsyncMock()) as leave_vc,
        patch("app.handlers.sudo_panel.group_repo.deactivate_group", AsyncMock()) as deactivate_group,
        patch("app.handlers.sudo_panel.channel_repo.deactivate_channel", AsyncMock()) as deactivate_channel,
        patch("app.handlers.sudo_panel._sudo_panel_kb", AsyncMock(return_value=KeyboardFactory.sudo_panel("fa"))),
    ):
        await sudo_panel._execute_sudo_leave_installs(client, call_py, query)

    get_logs.assert_awaited_once_with(sudo_id=200, limit=500)
    get_targets.assert_awaited_once_with(200, {-92001: "group"})
    leave_vc.assert_awaited_once_with(call_py, -92001)
    client.leave_chat.assert_awaited_once_with(-92001)
    deactivate_group.assert_awaited_once_with(-92001)
    deactivate_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_bulk_leave_intersects_install_logs_with_current_active_scope():
    owned_group = -94101
    owned_channel = -94102
    foreign_group = -94103
    inactive_group = -94104
    foreign_channel = -94105
    all_ids = [owned_group, owned_channel, foreign_group, inactive_group, foreign_channel]
    users = [200, 300]
    await _cleanup_scope_rows(all_ids, users)
    async with async_session() as session:
        async with session.begin():
            session.add_all(
                [
                    Sudo(user_id=200, display_name="Sudo A", is_active=True),
                    Sudo(user_id=300, display_name="Sudo B", is_active=True),
                    Group(chat_id=owned_group, chat_title="Owned Group", status="active", installed_by=200),
                    Channel(chat_id=owned_channel, chat_title="Owned Channel", status="active", installed_by=200),
                    Group(chat_id=foreign_group, chat_title="Foreign Group", status="active", installed_by=300),
                    Group(chat_id=inactive_group, chat_title="Inactive Group", status="inactive", installed_by=200),
                    Channel(chat_id=foreign_channel, chat_title="Foreign Channel", status="active", installed_by=300),
                ]
            )

    query = _query(200, CB["SUDO_LEAVE_INSTALLS"])
    client = SimpleNamespace(leave_chat=AsyncMock())
    call_py = SimpleNamespace()
    logs = [
        SimpleNamespace(chat_id=owned_group, chat_type="group", action="install"),
        SimpleNamespace(chat_id=owned_channel, chat_type="channel", action="install"),
        SimpleNamespace(chat_id=foreign_group, chat_type="group", action="install"),
        SimpleNamespace(chat_id=inactive_group, chat_type="group", action="install"),
        SimpleNamespace(chat_id=foreign_channel, chat_type="channel", action="install"),
    ]

    with (
        patch("app.handlers.sudo_panel._require_remove_bot_permission", AsyncMock(return_value=True)),
        patch("app.handlers.sudo_panel.log_repo.get_install_logs", AsyncMock(return_value=logs)),
        patch("app.handlers.sudo_panel.CallService.leave_voice_chat", AsyncMock()) as leave_vc,
        patch("app.handlers.sudo_panel.group_repo.deactivate_group", AsyncMock()) as deactivate_group,
        patch("app.handlers.sudo_panel.channel_repo.deactivate_channel", AsyncMock()) as deactivate_channel,
        patch("app.handlers.sudo_panel._sudo_panel_kb", AsyncMock(return_value=KeyboardFactory.sudo_panel("fa"))),
    ):
        await sudo_panel._execute_sudo_leave_installs(client, call_py, query)

    assert [call.args[0] for call in client.leave_chat.await_args_list] == [owned_group, owned_channel]
    assert [call.args[1] for call in leave_vc.await_args_list] == [owned_group, owned_channel]
    deactivate_group.assert_awaited_once_with(owned_group)
    deactivate_channel.assert_awaited_once_with(owned_channel)
    query.message.edit_text.assert_awaited_once()
    await _cleanup_scope_rows(all_ids, users)

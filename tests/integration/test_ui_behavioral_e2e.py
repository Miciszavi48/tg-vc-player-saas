from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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
from app.handlers import callbacks, dev_panel, force_join, start
from app.repositories.admin_report_repo import ChatInstallRow
from app.services.forced_membership_service import ForcedMembershipService
from app.services.wizard_ui import TOKEN_DEV_LISTS, TOKEN_DEV_USERS
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.callback_handlers: list = []
        self.message_handlers: list = []
        self.other_handlers: list = []

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
                    self.other_handlers.append(fn)
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
    if kb is None:
        return set()
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _kb_urls(kb) -> set[str]:
    if kb is None:
        return set()
    return {
        btn.url
        for row in kb.inline_keyboard
        for btn in row
        if getattr(btn, "url", None)
    }


def _pm_query(user_id: int, data: str):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="Tester", username="tester"),
        data=data,
        answer=AsyncMock(),
        message=SimpleNamespace(
            id=100,
            chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
            edit_text=AsyncMock(),
            delete=AsyncMock(),
            text="seed",
        ),
    )


def _pm_message(user_id: int, first_name: str = "Tester"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name=first_name, username="tester"),
        chat=SimpleNamespace(id=200, type=SimpleNamespace(value="private")),
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
    )


def _group_message(user_id: int):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user_id, first_name="GroupUser", username="group_user"),
        chat=SimpleNamespace(id=-1001234, type=SimpleNamespace(value="group")),
        reply=AsyncMock(),
        stop_propagation=MagicMock(),
        continue_propagation=MagicMock(),
    )


_LEGACY_DEV_ROOT_CBS = {
    CB["DEV_INCREASE_CREDIT"],
    CB["DEV_DECREASE_CREDIT"],
    CB["DEV_SET_BASE_RATE"],
    CB["DEV_SET_MUSIC_RATE"],
    CB["DEV_SET_VIDEO_RATE"],
    CB["DEV_BROADCAST_GROUP"],
    CB["DEV_FORWARD_GROUP"],
    CB["DEV_BROADCAST_PRIVATE"],
    CB["DEV_FORWARD_PRIVATE"],
    CB["DEV_BROADCAST_CHANNEL"],
    CB["DEV_FORWARD_CHANNEL"],
}

_LEGACY_OWNER_ROOT_CBS = {
    CB["OWN_BROADCAST_GROUP"],
    CB["OWN_FORWARD_GROUP"],
    CB["OWN_BROADCAST_PRIVATE"],
    CB["OWN_FORWARD_PRIVATE"],
    CB["OWN_BROADCAST_CHANNEL"],
    CB["OWN_FORWARD_CHANNEL"],
    CB["OWN_CHANNEL_SECURITY_TOGGLE"],
    CB["OWN_INSTALL_REPORTS"],
    CB["OWN_NO_CREDIT_REPORTS"],
    CB["OWN_TOPUP_SUDO_WALLET"],
}


@pytest.mark.asyncio
async def test_start_sends_single_message_per_role():
    bot = _RecorderBot()
    start.register(bot, None)
    handler = _handler_by_name(bot.message_handlers, "start_handler")

    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
    )

    cases = [
        ("developer", settings.DEVELOPER_ID, True, False, CB["WZ_HOME"]),
        ("owner", 900001, True, False, CB["WZ_HOME"]),
        ("sudo", 900002, False, True, CB["WZ_HOME"]),
        ("regular", 900003, False, False, None),
    ]

    for role, user_id, is_owner, is_sudo, expected_cb in cases:
        msg = _pm_message(user_id, first_name=role.title())
        with (
            patch("app.handlers.start.check_force_join", AsyncMock(return_value=True)),
            patch("app.handlers.start.track_event", AsyncMock()),
            patch("app.handlers.start.user_repo.upsert_user", AsyncMock()),
            patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=is_owner)),
            patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=is_sudo)),
            patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")),
        ):
            await handler(client, msg)

        assert msg.reply.await_count == 1
        kb = msg.reply.call_args.kwargs["reply_markup"]
        cbs = _kb_callbacks(kb)
        if expected_cb:
            assert expected_cb in cbs
            assert cbs == {CB["WZ_HOME"]} | {
                cb for cb in cbs if cb.startswith("start:")
            }
        else:
            assert CB["WZ_HOME"] not in cbs
            assert _kb_urls(kb)
            assert CB["START_PRICING"] not in cbs
            assert CB["START_ABOUT"] not in cbs
        msg.stop_propagation.assert_called_once()


@pytest.mark.asyncio
async def test_back_never_loads_legacy_keyboard():
    bot = _RecorderBot()
    callbacks.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "nav_back")

    client = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(username="test_bot")),
        send_message=AsyncMock(),
    )
    query = _pm_query(settings.DEVELOPER_ID, CB["NAV_BACK"])

    with patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")):
        await handler(client, query)

    assert query.answer.await_count == 1
    assert query.message.edit_text.await_count == 1
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["DEV_CAT_CREDIT"] in cbs
    assert cbs.isdisjoint(_LEGACY_DEV_ROOT_CBS)

    owner_query = _pm_query(900001, CB["NAV_BACK"])
    with (
        patch("app.services.panel_router.user_repo.is_owner", AsyncMock(return_value=True)),
        patch("app.services.panel_router.user_repo.is_sudo", AsyncMock(return_value=False)),
        patch("app.services.panel_router.settings_repo.get_bot_setting", AsyncMock(return_value="1")),
    ):
        await handler(client, owner_query)

    owner_kb = owner_query.message.edit_text.call_args.kwargs["reply_markup"]
    owner_cbs = _kb_callbacks(owner_kb)
    assert CB["OWN_GROUPS"] in owner_cbs
    assert owner_cbs.isdisjoint(_LEGACY_OWNER_ROOT_CBS)


@pytest.mark.asyncio
async def test_wizard_success_has_navigation_buttons():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_remove_sudo")

    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="123")),
        send_message=AsyncMock(),
    )
    query = _pm_query(settings.DEVELOPER_ID, CB["DEV_REMOVE_SUDO"])

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.user_repo.get_sudo", AsyncMock(return_value=SimpleNamespace(user_id=123))),
        patch("app.handlers.dev_panel.user_repo.remove_sudo", AsyncMock()),
        patch("app.handlers.dev_panel.invalidate_sudolist", AsyncMock()),
    ):
        await handler.__wrapped__(client, query)

    prompt_kb = client.ask.call_args.kwargs["reply_markup"]
    prompt_cbs = _kb_callbacks(prompt_kb)
    assert f"{CB['WZ_CANCEL_PREFIX']}{TOKEN_DEV_USERS}" in prompt_cbs
    assert CB["WZ_HOME"] not in prompt_cbs

    done_kb = client.send_message.call_args.kwargs["reply_markup"]
    done_cbs = _kb_callbacks(done_kb)
    assert any(cb.startswith(CB["DEV_SUDO_REMOVE_EXEC_PREFIX"]) for cb in done_cbs)
    assert any(cb.startswith(CB["DEV_SUDO_REMOVE_ABORT_PREFIX"]) for cb in done_cbs)
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_USERS}" in done_cbs
    assert CB["WZ_HOME"] not in done_cbs


@pytest.mark.asyncio
async def test_force_join_crud_persists_and_invalidate_cache():
    store: dict[int, SimpleNamespace] = {}

    async def _upsert_target(
        channel_id: int,
        channel_username: str | None = None,
        invite_link: str | None = None,
        display_name: str | None = None,
        chat_type: str = "channel",
        added_by: int | None = None,  # noqa: ARG001
        verify_status: str = "pending",
    ):
        existing = store.get(channel_id)
        if existing is None:
            existing = SimpleNamespace(
                id=len(store) + 1,
                channel_id=channel_id,
                channel_username=channel_username,
                invite_link=invite_link,
                display_name=display_name,
                chat_type=chat_type,
                verify_status=verify_status,
                is_active=True,
            )
            store[channel_id] = existing
        else:
            existing.channel_username = channel_username
            existing.invite_link = invite_link
            existing.display_name = display_name
            existing.chat_type = chat_type
            existing.verify_status = verify_status
            existing.is_active = True
        return existing

    async def _deactivate(channel_id: int) -> None:
        if channel_id in store:
            store[channel_id].is_active = False

    client = SimpleNamespace(
        get_chat=AsyncMock(
            return_value=SimpleNamespace(
                id=-10055,
                username="force_chan",
                invite_link="https://t.me/force_chan",
                title="Force Channel",
                type=SimpleNamespace(value="channel"),
            )
        ),
        get_me=AsyncMock(return_value=SimpleNamespace(id=777)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))),
    )

    with (
        patch("app.services.forced_membership_service.force_join_repo.upsert_target", AsyncMock(side_effect=_upsert_target)),
        patch("app.services.forced_membership_service.force_join_repo.deactivate", AsyncMock(side_effect=_deactivate)),
        patch("app.services.forced_membership_service.invalidate_fm_targets", AsyncMock()) as inv_mock,
    ):
        result = await ForcedMembershipService.add_target(client, "@force_chan", settings.DEVELOPER_ID)
        await ForcedMembershipService.remove_target(-10055)

    assert result["target"].channel_id == -10055
    assert -10055 in store
    assert store[-10055].is_active is False
    assert inv_mock.await_count == 2


@pytest.mark.asyncio
async def test_force_join_enforcement_blocks_regular_user():
    missing_targets = [
        {
            "channel_id": -100777,
            "username": "must_join",
            "invite_link": "https://t.me/must_join",
            "display_name": "Must Join",
        }
    ]

    regular_msg = _group_message(555001)
    with (
        patch("app.handlers.force_join.user_repo.is_sudo_or_above", AsyncMock(return_value=False)),
        patch("app.handlers.force_join.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.handlers.force_join.ForcedMembershipService.check", AsyncMock(return_value=missing_targets)),
        patch("app.handlers.force_join.track_event", AsyncMock()),
    ):
        blocked = await force_join.check_force_join(SimpleNamespace(), regular_msg, 555001)

    assert blocked is False
    assert regular_msg.reply.await_count == 1
    blocked_kb = regular_msg.reply.call_args.kwargs["reply_markup"]
    blocked_cbs = _kb_callbacks(blocked_kb)
    assert CB["START_FORCE_JOIN"] in blocked_cbs

    exempt_msg = _group_message(settings.DEVELOPER_ID)
    allowed = await force_join.check_force_join(SimpleNamespace(), exempt_msg, settings.DEVELOPER_ID)
    assert allowed is True
    assert exempt_msg.reply.await_count == 0


@pytest.mark.asyncio
async def test_dev_lists_match_db_seed_and_paging():
    bot = _RecorderBot()
    dev_panel.register(bot, None)

    groups_handler = _handler_by_name(bot.callback_handlers, "dev_list_groups")
    groups_page_handler = _handler_by_name(bot.callback_handlers, "dev_list_groups_page")
    channels_handler = _handler_by_name(bot.callback_handlers, "dev_list_channels")
    no_credit_handler = _handler_by_name(bot.callback_handlers, "dev_list_no_credit")
    renewal_handler = _handler_by_name(bot.callback_handlers, "dev_list_renewal_groups")

    now = datetime.now(timezone.utc)

    groups_page0 = [
        ChatInstallRow(
            chat_id=-1001001,
            chat_type="group",
            title="Group A",
            invite_link="https://t.me/a",
            credit_days=12,
            expire_at=None,
            status="active",
        ),
        ChatInstallRow(
            chat_id=-1001002,
            chat_type="group",
            title="Group B",
            invite_link="https://t.me/b",
            credit_days=5,
            expire_at=None,
            status="active",
        ),
    ]
    groups_page1 = [
        ChatInstallRow(
            chat_id=-1001003,
            chat_type="group",
            title="Group C",
            invite_link=None,
            credit_days=0,
            expire_at=None,
            status="active",
        )
    ]

    channels_page0 = [
        ChatInstallRow(
            chat_id=-1002001,
            chat_type="channel",
            title="Channel A",
            invite_link=None,
            credit_days=9,
            expire_at=None,
            status="active",
        ),
        ChatInstallRow(
            chat_id=-1002002,
            chat_type="channel",
            title="Channel B",
            invite_link=None,
            credit_days=0,
            expire_at=None,
            status="active",
        ),
    ]

    no_credit_page0 = [
        ChatInstallRow(
            chat_id=-1001003,
            chat_type="group",
            title="Group C",
            invite_link=None,
            credit_days=0,
            expire_at=None,
            status="active",
        ),
        ChatInstallRow(
            chat_id=-1002002,
            chat_type="channel",
            title="Channel B",
            invite_link=None,
            credit_days=0,
            expire_at=None,
            status="active",
        ),
    ]

    renewal_page0 = [
        ChatInstallRow(
            chat_id=-1003001,
            chat_type="group",
            title="Group Renewal",
            invite_link=None,
            credit_days=1,
            expire_at=now + timedelta(hours=12),
            status="active",
        )
    ]

    def _groups_page(page: int, page_size: int = 10):  # noqa: ARG001
        if page <= 0:
            return groups_page0, 2
        return groups_page1, 2

    q_groups = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_GROUPS"])
    q_groups_page = _pm_query(settings.DEVELOPER_ID, f"{CB['PAGE_DEV_GROUPS']}1")
    q_channels = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_CHANNELS"])
    q_no_credit = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_NO_CREDIT"])
    q_renewal = _pm_query(settings.DEVELOPER_ID, CB["DEV_LIST_RENEWAL_GROUPS"])

    with (
        patch("app.handlers.dev_panel.admin_report_repo.get_groups_page", AsyncMock(side_effect=_groups_page)),
        patch("app.handlers.dev_panel.admin_report_repo.get_channels_page", AsyncMock(return_value=(channels_page0, 1))),
        patch("app.handlers.dev_panel.admin_report_repo.get_no_credit_page", AsyncMock(return_value=(no_credit_page0, 1))),
        patch("app.handlers.dev_panel.admin_report_repo.get_renewal_page", AsyncMock(return_value=(renewal_page0, 1))),
    ):
        await groups_handler.__wrapped__(SimpleNamespace(), q_groups)
        await groups_page_handler.__wrapped__(SimpleNamespace(), q_groups_page)
        await channels_handler.__wrapped__(SimpleNamespace(), q_channels)
        await no_credit_handler.__wrapped__(SimpleNamespace(), q_no_credit)
        await renewal_handler.__wrapped__(SimpleNamespace(), q_renewal)

    groups_text = q_groups.message.edit_text.call_args.args[0]
    groups_cbs = _kb_callbacks(q_groups.message.edit_text.call_args.kwargs["reply_markup"])
    assert "-1001001" in groups_text
    assert "-1001002" in groups_text
    assert f"{CB['PAGE_DEV_GROUPS']}1" in groups_cbs

    groups_page_text = q_groups_page.message.edit_text.call_args.args[0]
    assert "-1001003" in groups_page_text

    channels_text = q_channels.message.edit_text.call_args.args[0]
    assert "-1002001" in channels_text
    assert "-1002002" in channels_text

    no_credit_text = q_no_credit.message.edit_text.call_args.args[0]
    assert "-1001003" in no_credit_text
    assert "-1002002" in no_credit_text

    renewal_text = q_renewal.message.edit_text.call_args.args[0]
    assert "-1003001" in renewal_text
    assert renewal_page0[0].expire_at.strftime("%Y-%m-%d %H:%M") in renewal_text


@pytest.mark.asyncio
async def test_leave_flow_confirm_and_db_update():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    confirm_handler = _handler_by_name(bot.callback_handlers, "dev_leave_confirm")

    row = ChatInstallRow(
        chat_id=-100777,
        chat_type="group",
        title="Leave Target",
        invite_link=None,
        credit_days=4,
        expire_at=None,
        status="active",
    )

    q_confirm = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_LEAVE_CONFIRM_PREFIX']}-100777:g:0",
    )

    with patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)):
        await confirm_handler.__wrapped__(SimpleNamespace(), q_confirm)

    confirm_kb = q_confirm.message.edit_text.call_args.kwargs["reply_markup"]
    confirm_cbs = _kb_callbacks(confirm_kb)
    assert f"{CB['DEV_LEAVE_EXEC_PREFIX']}-100777:g:0" in confirm_cbs
    assert f"{CB['DEV_LEAVE_CANCEL_PREFIX']}-100777:g:0" in confirm_cbs
    assert f"{CB['WZ_BACK_PREFIX']}{TOKEN_DEV_LISTS}" in confirm_cbs

    client = SimpleNamespace(leave_chat=AsyncMock())
    with (
        patch("app.handlers.dev_panel.admin_report_repo.get_install_row", AsyncMock(return_value=row)),
        patch("app.handlers.dev_panel.group_repo.deactivate_group", AsyncMock()) as deact_group,
        patch("app.handlers.dev_panel.channel_repo.deactivate_channel", AsyncMock()) as deact_channel,
        patch("app.handlers.dev_panel.log_repo.log_install", AsyncMock()),
        patch("app.handlers.dev_panel.NotificationService.notify_uninstall", AsyncMock()),
        patch("app.handlers.dev_panel.AdminDashboardService.invalidate_cache", AsyncMock()) as inv_cache,
    ):
        result = await dev_panel._leave_install(client, -100777, settings.DEVELOPER_ID)

    assert result is not None
    assert result.chat_id == -100777
    assert deact_group.await_count == 1
    assert deact_channel.await_count == 0
    assert client.leave_chat.await_count == 1
    assert [c.args[0] for c in inv_cache.await_args_list] == ["lists", "credit"]


@pytest.mark.asyncio
async def test_texts_links_text_persist():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    handler = _handler_by_name(bot.callback_handlers, "dev_text_set_text")

    store: dict[str, str | None] = {}

    async def _set_setting(key: str, value: str | None, *, updated_by: int) -> None:  # noqa: ARG001
        store[key] = value

    async def _get_setting(key: str) -> str | None:
        return store.get(key)

    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=SimpleNamespace(text="welcome from qa")),
        send_message=AsyncMock(),
    )
    query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_TEXT_SET_TEXT_PREFIX']}start_text",
    )

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock(side_effect=_set_setting)),
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(side_effect=_get_setting)),
    ):
        await handler.__wrapped__(client, query)

    assert store.get("start_text") == "welcome from qa"
    text = query.message.edit_text.call_args.args[0]
    assert "welcome from qa" in text
    kb = query.message.edit_text.call_args.kwargs["reply_markup"]
    cbs = _kb_callbacks(kb)
    assert CB["DEV_TEXTS_HOME"] in cbs
    assert CB["WZ_HOME"] not in cbs


@pytest.mark.asyncio
async def test_texts_links_media_persist_and_preview():
    bot = _RecorderBot()
    dev_panel.register(bot, None)
    set_media_handler = _handler_by_name(bot.callback_handlers, "dev_text_set_media")
    preview_handler = _handler_by_name(bot.callback_handlers, "dev_text_preview")

    store: dict[str, str | None] = {}

    async def _set_setting(key: str, value: str | None, *, updated_by: int) -> None:  # noqa: ARG001
        store[key] = value

    async def _get_setting(key: str) -> str | None:
        return store.get(key)

    media_message = SimpleNamespace(
        text=None,
        photo=[SimpleNamespace(file_id="photo_small"), SimpleNamespace(file_id="photo_big")],
        document=None,
        caption="caption from qa",
    )
    sent_preview = SimpleNamespace(
        id=201,
        chat=SimpleNamespace(id=100, type=SimpleNamespace(value="private")),
        edit_text=AsyncMock(),
        edit_caption=AsyncMock(),
        delete=AsyncMock(),
    )
    client = SimpleNamespace(
        stop_listening=AsyncMock(),
        ask=AsyncMock(return_value=media_message),
        send_message=AsyncMock(),
        send_photo=AsyncMock(return_value=sent_preview),
    )

    set_query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_TEXT_SET_MEDIA_PREFIX']}start_text",
    )
    preview_query = _pm_query(
        settings.DEVELOPER_ID,
        f"{CB['DEV_TEXT_PREVIEW_PREFIX']}start_text",
    )
    preview_chat_id = preview_query.message.chat.id
    preview_old_message = preview_query.message

    with (
        patch("app.handlers.dev_panel.remember_return_token", AsyncMock()),
        patch("app.handlers.dev_panel.settings_repo.set_bot_setting", AsyncMock(side_effect=_set_setting)),
        patch("app.services.texts_links_ui.settings_repo.get_bot_setting", AsyncMock(side_effect=_get_setting)),
    ):
        await set_media_handler.__wrapped__(client, set_query)
        await preview_handler.__wrapped__(client, preview_query)

    raw = store.get("start_text")
    assert raw is not None
    parsed = json.loads(raw)
    assert parsed["__mode"] == "media"
    assert parsed["file_id"] == "photo_big"
    assert parsed["caption"] == "caption from qa"

    assert client.send_photo.await_count == 1
    args = client.send_photo.await_args
    assert args.args[0] == preview_chat_id
    assert args.args[1] == "photo_big"
    assert args.kwargs["caption"] == "caption from qa"
    assert preview_query.message is sent_preview
    preview_old_message.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_category_pages_show_summary_not_empty():
    bot = _RecorderBot()
    dev_panel.register(bot, None)

    credit_handler = _handler_by_name(bot.callback_handlers, "dev_cat_credit")
    lists_handler = _handler_by_name(bot.callback_handlers, "dev_cat_lists")
    settings_handler = _handler_by_name(bot.callback_handlers, "dev_cat_settings")
    users_handler = _handler_by_name(bot.callback_handlers, "dev_cat_users")
    texts_handler = _handler_by_name(bot.callback_handlers, "dev_cat_texts")

    credit_q = _pm_query(settings.DEVELOPER_ID, CB["DEV_CAT_CREDIT"])
    lists_q = _pm_query(settings.DEVELOPER_ID, CB["DEV_CAT_LISTS"])
    settings_q = _pm_query(settings.DEVELOPER_ID, CB["DEV_CAT_SETTINGS"])
    users_q = _pm_query(settings.DEVELOPER_ID, CB["DEV_CAT_USERS"])
    texts_q = _pm_query(settings.DEVELOPER_ID, CB["DEV_CAT_TEXTS"])

    with (
        patch(
            "app.handlers.dev_panel.AdminDashboardService.get_credit_summary",
            AsyncMock(return_value={"active_installs": 3, "expiring_24h": 1, "no_credit": 2, "invoices_24h": 4}),
        ),
        patch(
            "app.handlers.dev_panel.AdminDashboardService.get_lists_summary",
            AsyncMock(return_value={"groups": 7, "channels": 3, "no_credit": 2, "expiring_24h": 1}),
        ),
        patch(
            "app.handlers.dev_panel.AdminDashboardService.get_general_summary",
            AsyncMock(
                return_value={
                    "force_join_enabled": True,
                    "auto_leave_enabled": True,
                    "trial_enabled": True,
                    "required_channels": 2,
                }
            ),
        ),
        patch(
            "app.handlers.dev_panel.AdminDashboardService.get_users_summary",
            AsyncMock(return_value={"owners": 2, "sudos": 5}),
        ),
        patch("app.handlers.dev_panel.settings_repo.get_bot_setting", AsyncMock(return_value=None)),
    ):
        await credit_handler.__wrapped__(SimpleNamespace(), credit_q)
        await lists_handler.__wrapped__(SimpleNamespace(), lists_q)
        await settings_handler.__wrapped__(SimpleNamespace(), settings_q)
        await users_handler.__wrapped__(SimpleNamespace(), users_q)
        await texts_handler.__wrapped__(SimpleNamespace(), texts_q)

    texts = [
        credit_q.message.edit_text.call_args.args[0],
        lists_q.message.edit_text.call_args.args[0],
        settings_q.message.edit_text.call_args.args[0],
        users_q.message.edit_text.call_args.args[0],
        texts_q.message.edit_text.call_args.args[0],
    ]

    for body in texts:
        non_empty = [line for line in body.splitlines() if line.strip()]
        assert len(non_empty) >= 3

    for body in (texts[0], texts[1], texts[3], texts[4]):
        assert any(ch.isdigit() for ch in body)

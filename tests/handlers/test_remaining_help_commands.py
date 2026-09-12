from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.utils.i18n import AUTO_LANG, t
from app.utils.ui import CB


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []
        self.message_registrations: list[dict] = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            self.message_registrations.append({"fn": fn, "args": args, "kwargs": kwargs})
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _registered_group_panel():
    from app.handlers import group_panel

    bot = _RecorderBot()
    group_panel.register(bot, MagicMock())
    return bot


def _message(
    text: str,
    *,
    chat_id: int = -7001,
    user_id: int = 42,
    reply_user_id: int | None = None,
    chat_type: str = "supergroup",
):
    reply_to = None
    if reply_user_id is not None:
        reply_to = SimpleNamespace(from_user=SimpleNamespace(id=reply_user_id))
    return SimpleNamespace(
        text=text,
        caption=None,
        chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value=chat_type)),
        from_user=SimpleNamespace(id=user_id, first_name="Tester"),
        reply_to_message=reply_to,
        reply=AsyncMock(),
    )


def _query(data: str, *, chat_id: int = -7001, user_id: int = 42):
    return SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup")),
            edit_text=AsyncMock(),
        ),
        answer=AsyncMock(),
    )


def _handler(handlers: list, name: str):
    return next(fn for fn in handlers if fn.__name__ == name)


def _callback_data(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]


def test_group_panel_public_help_handlers_register_before_playback_module():
    from app.handlers import _MODULES, group_panel, playback
    from app.handlers.priority import PRIORITY_COMMAND_GROUP

    assert _MODULES.index(group_panel) < _MODULES.index(playback)
    bot = _registered_group_panel()
    groups = {
        registration["fn"].__name__: registration["kwargs"].get("group")
        for registration in bot.message_registrations
    }
    for name in {
        "group_id_command",
        "channel_id_command",
        "player_status_command",
        "user_id_command",
        "show_id_photo_command",
        "show_id_simple_command",
        "vip_list_command",
        "vip_clear_command",
    }:
        assert groups[name] == PRIORITY_COMMAND_GROUP


@pytest.mark.asyncio
async def test_public_id_commands_return_group_sender_and_reply_ids():
    bot = _registered_group_panel()
    group_id = _handler(bot.message_handlers, "group_id_command")
    channel_id = _handler(bot.message_handlers, "channel_id_command")
    user_id = _handler(bot.message_handlers, "user_id_command")

    group_message = _message("Group id")
    await group_id(AsyncMock(), group_message)
    # ROLE-07: the reply now carries a datacenter line as well. An AsyncMock
    # client yields no real integer dc_id, so the unknown label is expected.
    group_message.reply.assert_awaited_once_with(
        t("fa", "public_cmd.group_id", chat_id=group_message.chat.id)
        + "\n"
        + t("fa", "public_cmd.group_id_dc", dc_id=t("fa", "public_cmd.dc_unknown"))
    )

    channel_message = _message("Channel id", chat_id=-10055, chat_type="channel")
    await channel_id(AsyncMock(), channel_message)
    channel_message.reply.assert_awaited_once_with(
        t("fa", "public_cmd.channel_id", chat_id=channel_message.chat.id)
    )

    persian_channel_message = _message("شناسه کانال", chat_id=-7001, chat_type="supergroup")
    await channel_id(AsyncMock(), persian_channel_message)
    persian_channel_message.reply.assert_awaited_once_with(
        t("fa", "public_cmd.channel_id_current_chat", chat_id=persian_channel_message.chat.id)
    )

    private_message = _message("Channel id", chat_id=100, chat_type="private")
    await channel_id(AsyncMock(), private_message)
    private_message.reply.assert_awaited_once_with(t("fa", "public_cmd.channel_id_group_only"))

    user_message = _message("Id", user_id=100, reply_user_id=200)
    client = AsyncMock()
    with (
        patch(
            "app.handlers.group_panel.id_command_settings_repo.get_output_mode",
            AsyncMock(return_value="simple"),
        ),
        patch(
            "app.handlers.group_panel.call_stats_settings_repo.is_effective_id_call_stats",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_panel.user_info_formatter_service.reply_user_info",
            AsyncMock(),
        ) as reply_info,
    ):
        await user_id(client, user_message)

    reply_info.assert_awaited_once_with(
        client,
        user_message,
        output_mode="simple",
        include_call_stats=True,
        lang=AUTO_LANG,
    )


@pytest.mark.asyncio
async def test_player_status_sanitizes_source_and_reports_safe_active_state():
    bot = _registered_group_panel()
    handler = _handler(bot.message_handlers, "player_status_command")
    message = _message("Status Player")

    active = {
        message.chat.id: {
            "source": r"C:\\secret\\track.ogg",
            "title": r"C:\\secret\\track.ogg",
            "media_type": "audio",
            "is_paused": False,
        }
    }
    with (
        patch("app.services.CallService.get_active_calls", return_value=active),
        patch("app.services.CallService.get_playback_speed", return_value=125),
        patch("app.repositories.playlist_repo.get_queue_length", AsyncMock(return_value=3)),
        patch("app.handlers.callbacks._current_volume", return_value=80),
    ):
        await handler(AsyncMock(), message)

    text = message.reply.await_args.args[0]
    assert "1.25x" in text
    assert "80" in text
    assert "3" in text
    assert "secret" not in text
    assert "C:" not in text


@pytest.mark.asyncio
async def test_show_id_text_commands_set_dedicated_output_mode():
    bot = _registered_group_panel()
    photo = _handler(bot.message_handlers, "show_id_photo_command")
    simple = _handler(bot.message_handlers, "show_id_simple_command")

    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch("app.handlers.group_panel._guard_group_chat_settings", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_panel.id_command_settings_repo.set_output_mode",
            AsyncMock(),
        ) as set_mode,
        patch(
            "app.handlers.group_panel.settings_repo.update_setting",
            AsyncMock(),
        ) as update_setting,
    ):
        photo_message = _message("Show Id Status Photo")
        await photo(AsyncMock(), photo_message)
        simple_message = _message("Show Id Status Simple")
        await simple(AsyncMock(), simple_message)

    assert set_mode.await_args_list[0].args == (photo_message.chat.id, "photo")
    assert set_mode.await_args_list[0].kwargs["updated_by"] == photo_message.from_user.id
    assert set_mode.await_args_list[1].args == (simple_message.chat.id, "simple")
    update_setting.assert_not_awaited()
    photo_message.reply.assert_awaited_once_with(t("fa", "public_cmd.show_id_mode_photo_set"))
    simple_message.reply.assert_awaited_once_with(t("fa", "public_cmd.show_id_mode_simple_set"))


@pytest.mark.asyncio
async def test_vip_list_text_command_uses_existing_vip_menu_callbacks():
    bot = _registered_group_panel()
    handler = _handler(bot.message_handlers, "vip_list_command")
    message = _message("VIP List")
    row = SimpleNamespace(user_id=55, username="vip")

    with (
        patch("app.utils.decorators.is_developer", return_value=True),
        patch(
            "app.handlers.group_panel.admin_repo.get_player_vips_page",
            AsyncMock(return_value=([row], 1, 0)),
        ),
    ):
        await handler(AsyncMock(), message)

    kwargs = message.reply.await_args.kwargs
    callbacks = _callback_data(kwargs["reply_markup"])
    assert CB["GRP_VIP_LIST"] in callbacks
    assert f"{CB['GRP_VIP_DEMOTE_PREFIX']}55:0" in callbacks


@pytest.mark.asyncio
async def test_clear_vip_list_text_opens_confirmation_without_clearing():
    bot = _registered_group_panel()
    handler = _handler(bot.message_handlers, "vip_clear_command")
    message = _message("Clear VIP List")

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_player_vips", AsyncMock()) as clear_vips,
        patch("app.handlers.group_panel.time.time", return_value=123456),
    ):
        await handler(AsyncMock(), message)

    clear_vips.assert_not_awaited()
    callbacks = _callback_data(message.reply.await_args.kwargs["reply_markup"])
    assert f"{CB['GRP_VIP_CLEAR_CONFIRM_PREFIX']}{message.chat.id}:{message.from_user.id}:123456" in callbacks
    assert f"{CB['GRP_VIP_CLEAR_CANCEL_PREFIX']}{message.chat.id}:{message.from_user.id}:123456" in callbacks


@pytest.mark.asyncio
async def test_clear_vip_confirm_is_user_bound_and_clears_only_vips():
    bot = _registered_group_panel()
    handler = _handler(bot.callback_handlers, "grp_vip_clear_confirm")
    chat_id = -7001
    user_id = 42
    data = f"{CB['GRP_VIP_CLEAR_CONFIRM_PREFIX']}{chat_id}:{user_id}:{int(time.time())}"
    query = _query(data, chat_id=chat_id, user_id=user_id)

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_player_vips", AsyncMock(return_value=2)) as clear_vips,
        patch("app.handlers.group_panel._build_group_management_summary", AsyncMock(return_value="summary")),
    ):
        await handler(AsyncMock(), query)

    clear_vips.assert_awaited_once_with(chat_id)
    query.answer.assert_awaited_once_with(
        t("fa", "panels.group.management.vip_clear_done", count=2),
        show_alert=True,
    )
    query.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_vip_confirm_wrong_user_is_rejected_without_clear():
    bot = _registered_group_panel()
    handler = _handler(bot.callback_handlers, "grp_vip_clear_confirm")
    chat_id = -7001
    data = f"{CB['GRP_VIP_CLEAR_CONFIRM_PREFIX']}{chat_id}:42:{int(time.time())}"
    query = _query(data, chat_id=chat_id, user_id=99)

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_player_vips", AsyncMock()) as clear_vips,
    ):
        await handler(AsyncMock(), query)

    clear_vips.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.no_access"), show_alert=True)


@pytest.mark.asyncio
async def test_clear_vip_stale_token_is_rejected_without_clear():
    bot = _registered_group_panel()
    handler = _handler(bot.callback_handlers, "grp_vip_clear_confirm")
    chat_id = -7001
    user_id = 42
    stale_at = int(time.time()) - 301
    data = f"{CB['GRP_VIP_CLEAR_CONFIRM_PREFIX']}{chat_id}:{user_id}:{stale_at}"
    query = _query(data, chat_id=chat_id, user_id=user_id)

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_player_vips", AsyncMock()) as clear_vips,
    ):
        await handler(AsyncMock(), query)

    clear_vips.assert_not_awaited()
    query.answer.assert_awaited_once_with(
        t("fa", "common.errors.invalid_callback"),
        show_alert=True,
    )


@pytest.mark.asyncio
async def test_clear_vip_forged_chat_id_is_rejected_without_clear():
    bot = _registered_group_panel()
    handler = _handler(bot.callback_handlers, "grp_vip_clear_confirm")
    chat_id = -7001
    user_id = 42
    data = f"{CB['GRP_VIP_CLEAR_CONFIRM_PREFIX']}-9999:{user_id}:{int(time.time())}"
    query = _query(data, chat_id=chat_id, user_id=user_id)

    with (
        patch("app.utils.decorators.deny_if_bot_disabled", AsyncMock(return_value=False)),
        patch("app.utils.player_permissions.can_manage_vip", AsyncMock(return_value=True)),
        patch("app.handlers.group_panel.admin_repo.clear_player_vips", AsyncMock()) as clear_vips,
    ):
        await handler(AsyncMock(), query)

    clear_vips.assert_not_awaited()
    query.answer.assert_awaited_once_with(t("fa", "common.errors.no_access"), show_alert=True)


def test_vip_clear_callback_data_stays_within_telegram_limit():
    from app.utils.ui import KeyboardFactory

    kb = KeyboardFactory.group_vip_clear_confirm(
        "fa",
        -1003740677405,
        6909288370,
        int(time.time()),
    )
    for callback in _callback_data(kb):
        assert len(callback.encode("utf-8")) <= 64


@pytest.mark.parametrize(
    "text",
    [
        "Group id now",
        "Channel id now",
        "شناسه کانال الان",
        "Status Player now",
        "Id now",
        "Show Id Status Photo now",
        "Show Id Status Simple now",
        "VIP List now",
        "Clear VIP List now",
    ],
)
def test_public_exact_commands_reject_trailing_arguments(text: str):
    import re

    from app.handlers.group_panel import (
        _GROUP_ID_TEXT_CMDS,
        _CHANNEL_ID_TEXT_CMDS,
        _PLAYER_STATUS_TEXT_CMDS,
        _SHOW_ID_PHOTO_TEXT_CMDS,
        _SHOW_ID_SIMPLE_TEXT_CMDS,
        _USER_ID_TEXT_CMDS,
        _VIP_CLEAR_TEXT_CMDS,
        _VIP_LIST_TEXT_CMDS,
    )
    from app.utils.text_commands import normalize_command_text

    alias_groups = (
        _GROUP_ID_TEXT_CMDS,
        _CHANNEL_ID_TEXT_CMDS,
        _PLAYER_STATUS_TEXT_CMDS,
        _USER_ID_TEXT_CMDS,
        _SHOW_ID_PHOTO_TEXT_CMDS,
        _SHOW_ID_SIMPLE_TEXT_CMDS,
        _VIP_LIST_TEXT_CMDS,
        _VIP_CLEAR_TEXT_CMDS,
    )
    normalized = normalize_command_text(_message(text))
    matched = any(
        re.compile(
            rf"^(?:{'|'.join(re.escape(alias) for alias in aliases)})\s*$",
            re.IGNORECASE,
        ).fullmatch(normalized)
        for aliases in alias_groups
    )
    assert matched is False

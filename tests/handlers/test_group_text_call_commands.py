from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.group_text_commands import GroupTextCommandType, parse_group_text_command


class _RecorderBot:
    def __init__(self) -> None:
        self.message_handlers: list = []
        self.callback_handlers: list = []

    def on_message(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.message_handlers.append(fn)
            return fn

        return _decorator

    def on_callback_query(self, *args, **kwargs):  # noqa: ANN001, ANN002
        def _decorator(fn):
            self.callback_handlers.append(fn)
            return fn

        return _decorator


def _message(
    text: str,
    *,
    chat_id: int = -10055001,
    user_id: int = 123456789,
    chat_type: str = "supergroup",
    reply_to_message=None,
):
    msg = SimpleNamespace()
    msg.chat = SimpleNamespace(id=chat_id, type=SimpleNamespace(value=chat_type))
    msg.from_user = SimpleNamespace(id=user_id, username="tester", first_name="Tester")
    msg.text = text
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = reply_to_message
    msg.reply = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.continue_propagation = lambda: None
    return msg


def _handler(call_py=None):
    from app.handlers import group_text_call_commands

    bot = _RecorderBot()
    group_text_call_commands.register(bot, call_py)
    return bot.message_handlers[0]


@pytest.fixture(autouse=True)
def _active_managed_group_gate():
    with patch(
        "app.handlers.group_text_call_commands.group_runtime_state_service.require_active_group",
        AsyncMock(return_value=True),
    ):
        yield


@pytest.mark.parametrize(
    "raw,command,attrs",
    [
        ("دریافت پنل", GroupTextCommandType.GET_PANEL, {}),
        ("GetPanel", GroupTextCommandType.GET_PANEL, {}),
        ("پایان کال 25", GroupTextCommandType.END_CALL, {"minutes": 25}),
        ("پایان کال", GroupTextCommandType.END_CALL, {"minutes": None}),
        ("پایان کال ۲۵", GroupTextCommandType.END_CALL, {"minutes": 25}),
        ("بستن کال 25", GroupTextCommandType.END_CALL, {"minutes": 25}),
        ("بستن کال", GroupTextCommandType.END_CALL, {"minutes": None}),
        ("EndCall 25", GroupTextCommandType.END_CALL, {"minutes": 25}),
        ("EndCall", GroupTextCommandType.END_CALL, {"minutes": None}),
        ("End Call 25", GroupTextCommandType.END_CALL, {"minutes": 25}),
        ("End Call", GroupTextCommandType.END_CALL, {"minutes": None}),
        ("DiscardCall 25", GroupTextCommandType.END_CALL, {"minutes": 25}),
        ("DiscardCall", GroupTextCommandType.END_CALL, {"minutes": None}),
        ("شروع ویس چت", GroupTextCommandType.START_CALL, {}),
        ("شروع کال", GroupTextCommandType.START_CALL, {}),
        ("StartCall", GroupTextCommandType.START_CALL, {}),
        ("Start Call", GroupTextCommandType.START_CALL, {}),
        ("بیصدا کال 123", GroupTextCommandType.MUTE_CALL, {"target_text": "123"}),
        ("حذف بیصدا کال 123", GroupTextCommandType.UNMUTE_CALL, {"target_text": "123"}),
        ("MuteCall 123", GroupTextCommandType.MUTE_CALL, {"target_text": "123"}),
        ("UnmuteCall 123", GroupTextCommandType.UNMUTE_CALL, {"target_text": "123"}),
        ("دعوت کال 123", GroupTextCommandType.INVITE_CALL, {"invite_scope": "user", "target_text": "123"}),
        ("دعوت کال مدیران", GroupTextCommandType.INVITE_CALL, {"invite_scope": "admins"}),
        ("دعوت کال اخیر", GroupTextCommandType.INVITE_CALL, {"invite_scope": "recent"}),
        ("دعوت کال ویژه", GroupTextCommandType.INVITE_CALL, {"invite_scope": "special"}),
        ("InviteCall 123", GroupTextCommandType.INVITE_CALL, {"invite_scope": "user", "target_text": "123"}),
        ("InviteCall Admins", GroupTextCommandType.INVITE_CALL, {"invite_scope": "admins"}),
        ("InviteCall Recent", GroupTextCommandType.INVITE_CALL, {"invite_scope": "recent"}),
        ("InviteCall Special", GroupTextCommandType.INVITE_CALL, {"invite_scope": "special"}),
        ("آمار خودکار کال فعال", GroupTextCommandType.AUTO_CALL_STATS, {"mode": True}),
        ("آمار خودکار کال غیرفعال", GroupTextCommandType.AUTO_CALL_STATS, {"mode": False}),
        ("AutoCallStatis Active", GroupTextCommandType.AUTO_CALL_STATS, {"mode": True}),
        ("AutoCallStatis Inactive", GroupTextCommandType.AUTO_CALL_STATS, {"mode": False}),
        ("آمار کال", GroupTextCommandType.CALL_STATS_PANEL, {}),
        ("امار کال", GroupTextCommandType.CALL_STATS_PANEL, {}),
        ("Call Stats", GroupTextCommandType.CALL_STATS_PANEL, {}),
        ("Voice Call Stats", GroupTextCommandType.CALL_STATS_PANEL, {}),
        ("CallStatis", GroupTextCommandType.CALL_STATS_PANEL, {}),
        ("سکوت کال فعال", GroupTextCommandType.CALL_MUTE, {"mode": True}),
        ("سکوت کال غیرفعال", GroupTextCommandType.CALL_MUTE, {"mode": False}),
        ("CallMute Active", GroupTextCommandType.CALL_MUTE, {"mode": True}),
        ("CallMute Inactive", GroupTextCommandType.CALL_MUTE, {"mode": False}),
        ("کامنت کال فعال", GroupTextCommandType.CALL_COMMENT, {"mode": True}),
        ("کامنت کال غیرفعال", GroupTextCommandType.CALL_COMMENT, {"mode": False}),
        ("CallComment Active", GroupTextCommandType.CALL_COMMENT, {"mode": True}),
        ("CallComment Inactive", GroupTextCommandType.CALL_COMMENT, {"mode": False}),
        ("عنوان کال شب سرد", GroupTextCommandType.SET_TITLE, {"title": "شب سرد"}),
        ("تنظیم تایتل شب سرد", GroupTextCommandType.SET_TITLE, {"title": "شب سرد"}),
        ("تنظیم عنوان کال شب سرد", GroupTextCommandType.SET_TITLE, {"title": "شب سرد"}),
        ("SetTitleCall Cold Night", GroupTextCommandType.SET_TITLE, {"title": "Cold Night"}),
        ("SetTitle Cold Night", GroupTextCommandType.SET_TITLE, {"title": "Cold Night"}),
        ("لینک کال", GroupTextCommandType.GET_CALL_LINK, {}),
        ("دریافت لینک کال", GroupTextCommandType.GET_CALL_LINK, {}),
        ("GetCallLink", GroupTextCommandType.GET_CALL_LINK, {}),
        ("Link Call", GroupTextCommandType.GET_CALL_LINK, {}),
    ],
)
def test_parser_accepts_documented_aliases(raw: str, command: GroupTextCommandType, attrs: dict):
    parsed = parse_group_text_command(raw)
    assert parsed is not None
    assert parsed.command == command
    assert parsed.error is None
    for key, value in attrs.items():
        assert getattr(parsed, key) == value


@pytest.mark.parametrize(
    "raw",
    [
        "آمار کال شنبه",
        "CallStatis Saturday",
    ],
)
def test_parser_ignores_call_stats_day_suffix(raw: str):
    assert parse_group_text_command(raw) is None


@pytest.mark.parametrize(
    "raw,error",
    [
        ("پایان کال abc", "invalid_duration"),
        ("پایان کال الان", "invalid_duration"),
        ("End Call now", "invalid_duration"),
        ("EndCall now", "invalid_duration"),
        ("DiscardCall x", "invalid_duration"),
        ("بستن کال الان", "invalid_duration"),
        ("پایان کال -1", "invalid_duration"),
        ("EndCall 1441", "duration_too_large"),
        ("عنوان کال", "missing_title"),
        ("InviteCall", "missing_target"),
    ],
)
def test_parser_returns_visible_error_for_invalid_matched_commands(raw: str, error: str):
    parsed = parse_group_text_command(raw)
    assert parsed is not None
    assert parsed.error == error


@pytest.mark.parametrize(
    "raw",
    [
        "متن عادی",
        "آپدیت شارژ ویدیو 5",
        "آپدیت شارژ ویدیو ۵",
        "پخش آهنگ",
        "play test",
        "لینک کال x",
        "Link Call x",
    ],
)
def test_parser_ignores_unrelated_and_collision_commands(raw: str):
    assert parse_group_text_command(raw) is None


@pytest.mark.asyncio
async def test_private_chat_rejected_visibly():
    handler = _handler()
    message = _message("GetPanel", chat_type="private")
    await handler(AsyncMock(), message)
    message.reply.assert_awaited_once()
    assert "گروه" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_missing_target_rejected_visibly_for_mute():
    handler = _handler()
    message = _message("MuteCall")
    client = AsyncMock()
    with patch(
        "app.handlers.group_text_call_commands._can_manage_call_command",
        AsyncMock(return_value=True),
    ):
        await handler(client, message)
    message.reply.assert_awaited_once()
    assert "کاربر" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_invalid_duration_rejected_visibly():
    handler = _handler()
    message = _message("EndCall 1441")
    with patch(
        "app.handlers.group_text_call_commands._can_manage_call_command",
        AsyncMock(return_value=True),
    ):
        await handler(AsyncMock(), message)
    message.reply.assert_awaited_once()
    assert "1440" in message.reply.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw,expected_text",
    [
        ("پایان کال الان", "عدد"),
        ("End Call now", "عدد"),
        ("EndCall now", "عدد"),
        ("DiscardCall x", "عدد"),
        ("بستن کال الان", "عدد"),
    ],
)
async def test_end_call_invalid_suffix_is_visible_and_does_not_mutate(
    raw: str,
    expected_text: str,
):
    handler = _handler()
    message = _message(raw)
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock()) as can_manage,
        patch("app.handlers.group_text_call_commands.call_cmd_service.end_call_now", AsyncMock()) as end_now,
        patch("app.handlers.group_text_call_commands.call_cmd_service.schedule_call_end", AsyncMock()) as svc,
    ):
        await handler(AsyncMock(), message)

    can_manage.assert_not_awaited()
    end_now.assert_not_awaited()
    svc.assert_not_awaited()
    message.reply.assert_awaited_once()
    assert expected_text in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_developer_can_run_admin_command():
    handler = _handler()
    message = _message("آمار خودکار کال فعال", user_id=123456789)
    await handler(AsyncMock(), message)
    message.reply.assert_awaited_once()
    assert "آمار خودکار" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_sudo_can_run_admin_command():
    handler = _handler()
    message = _message("CallMute Active", user_id=90001)
    with (
        patch("app.handlers.group_text_call_commands.is_developer", return_value=False),
        patch("app.handlers.group_text_call_commands.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands.sudo_has_permission", AsyncMock(return_value=True)),
    ):
        await handler(AsyncMock(), message)
    message.reply.assert_awaited_once()
    assert "سکوت کال" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_group_admin_can_run_admin_command():
    handler = _handler()
    message = _message("CallComment Active", user_id=90002)
    with (
        patch("app.handlers.group_text_call_commands.is_developer", return_value=False),
        patch("app.handlers.group_text_call_commands.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands.sudo_has_permission", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands.admin_repo.is_music_admin_or_above", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands.admin_repo.is_video_admin", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands._is_telegram_group_admin", AsyncMock(return_value=True)),
    ):
        await handler(AsyncMock(), message)
    message.reply.assert_awaited_once()
    assert "کامنت کال" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_unauthorized_user_denied_visibly():
    handler = _handler()
    message = _message("CallMute Active", user_id=90003)
    with (
        patch("app.handlers.group_text_call_commands.is_developer", return_value=False),
        patch("app.handlers.group_text_call_commands.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands.sudo_has_permission", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands.admin_repo.is_music_admin_or_above", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands.admin_repo.is_video_admin", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands._is_telegram_group_admin", AsyncMock(return_value=False)),
    ):
        await handler(AsyncMock(), message)
    message.reply.assert_awaited_once()
    assert "دسترسی" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_mute_command_calls_service_with_resolved_id():
    handler = _handler()
    message = _message("MuteCall 123")
    result = SimpleNamespace(ok=True, reason="muted")
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch("app.handlers.group_text_call_commands.call_cmd_service.mute_participant", AsyncMock(return_value=result)) as svc,
    ):
        await handler(AsyncMock(), message)
    svc.assert_awaited_once_with(None, message.chat.id, 123)
    assert "بی‌صدا" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_unmute_command_calls_service_with_reply_target():
    handler = _handler()
    reply = SimpleNamespace(from_user=SimpleNamespace(id=777))
    message = _message("UnmuteCall", reply_to_message=reply)
    result = SimpleNamespace(ok=True, reason="unmuted")
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch("app.handlers.group_text_call_commands.call_cmd_service.unmute_participant", AsyncMock(return_value=result)) as svc,
    ):
        await handler(AsyncMock(), message)
    svc.assert_awaited_once_with(None, message.chat.id, 777)
    assert "حذف" in message.reply.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,method",
    [
        ("InviteCall Admins", "invite_admins"),
        ("دعوت کال اخیر", "invite_recent"),
        ("دعوت کال ویژه", "invite_special"),
    ],
)
async def test_invite_scopes_route_to_correct_service(text: str, method: str):
    handler = _handler()
    message = _message(text)
    result = SimpleNamespace(ok=True, reason="invited", count=2)
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch(f"app.handlers.group_text_call_commands.call_cmd_service.{method}", AsyncMock(return_value=result)) as svc,
    ):
        await handler(AsyncMock(), message)
    svc.assert_awaited_once()
    assert "2" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_invite_user_routes_to_specific_service():
    handler = _handler()
    message = _message("InviteCall 444")
    client = AsyncMock()
    result = SimpleNamespace(ok=True, reason="invited", count=1)
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch("app.handlers.group_text_call_commands.call_cmd_service.invite_user", AsyncMock(return_value=result)) as svc,
    ):
        await handler(client, message)
    svc.assert_awaited_once_with(client, None, message.chat.id, 444)


@pytest.mark.asyncio
async def test_get_call_link_success_and_error_paths():
    handler = _handler()
    ok_msg = _message("GetCallLink")
    ok = SimpleNamespace(ok=True, reason="ok", link="https://t.me/public?videochat")
    with patch(
        "app.handlers.group_text_call_commands.call_cmd_service.get_call_link",
        AsyncMock(return_value=ok),
    ):
        await handler(AsyncMock(), ok_msg)
    assert "https://t.me/public?videochat" in ok_msg.reply.await_args.args[0]

    err_msg = _message("GetCallLink")
    err = SimpleNamespace(ok=False, reason="no_active_call", link=None)
    with patch(
        "app.handlers.group_text_call_commands.call_cmd_service.get_call_link",
        AsyncMock(return_value=err),
    ):
        await handler(AsyncMock(), err_msg)
    assert "فعالی" in err_msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_get_call_link_is_public_but_still_uses_group_and_active_call_service_gates():
    handler = _handler()
    message = _message("Link Call", user_id=777)
    result = SimpleNamespace(ok=True, reason="ok", link="https://t.me/public?videochat")

    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=False)) as can_manage,
        patch("app.handlers.group_text_call_commands.call_cmd_service.get_call_link", AsyncMock(return_value=result)) as svc,
    ):
        await handler(AsyncMock(), message)

    can_manage.assert_not_awaited()
    svc.assert_awaited_once()
    assert "https://t.me/public?videochat" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_get_call_link_public_request_reports_unavailable_link_safely():
    handler = _handler()
    message = _message("Link Call")
    result = SimpleNamespace(ok=False, reason="raw_api_unavailable", link=None)

    with patch(
        "app.handlers.group_text_call_commands.call_cmd_service.get_call_link",
        AsyncMock(return_value=result),
    ) as svc:
        await handler(AsyncMock(), message)

    svc.assert_awaited_once()
    message.reply.assert_awaited_once()
    assert message.reply.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["شروع کال", "Start Call"])
async def test_start_call_exact_help_commands_invoke_service(raw: str):
    handler = _handler()
    message = _message(raw)
    result = SimpleNamespace(ok=True, reason="started")
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.group_text_call_commands.call_cmd_service.start_call", AsyncMock(return_value=result)) as svc,
    ):
        await handler(AsyncMock(), message)

    gate.assert_awaited_once()
    svc.assert_awaited_once()
    assert "شروع" in message.reply.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["پایان کال 25", "End Call 25"])
async def test_end_call_exact_help_commands_schedule_minutes(raw: str):
    handler = _handler()
    message = _message(raw)
    result = SimpleNamespace(ok=True, reason="scheduled", end_at=None)
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.group_text_call_commands.call_cmd_service.schedule_call_end", AsyncMock(return_value=result)) as svc,
    ):
        await handler(AsyncMock(), message)

    gate.assert_awaited_once()
    svc.assert_awaited_once()
    assert svc.await_args.args[1] == message.chat.id
    assert svc.await_args.args[2] == 25
    assert "زمان‌بندی" in message.reply.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["پایان کال", "End Call"])
async def test_end_call_exact_help_commands_end_immediately(raw: str):
    handler = _handler()
    message = _message(raw)
    result = SimpleNamespace(ok=True, reason="ended")
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)) as gate,
        patch("app.handlers.group_text_call_commands.call_cmd_service.end_call_now", AsyncMock(return_value=result)) as svc,
        patch("app.handlers.group_text_call_commands.call_cmd_service.schedule_call_end", AsyncMock()) as schedule,
    ):
        await handler(AsyncMock(), message)

    gate.assert_awaited_once()
    svc.assert_awaited_once_with(None, message.chat.id)
    schedule.assert_not_awaited()
    assert "پایان" in message.reply.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["EndCall", "DiscardCall", "بستن کال"])
async def test_legacy_bare_end_call_aliases_end_immediately(raw: str):
    handler = _handler()
    message = _message(raw)
    result = SimpleNamespace(ok=True, reason="ended")
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch("app.handlers.group_text_call_commands.call_cmd_service.end_call_now", AsyncMock(return_value=result)) as svc,
    ):
        await handler(AsyncMock(), message)

    svc.assert_awaited_once_with(None, message.chat.id)
    assert message.reply.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["End Call 25", "پایان کال 25", "EndCall 25", "DiscardCall 25", "بستن کال 25"])
async def test_numeric_end_call_aliases_preserve_scheduled_end(raw: str):
    handler = _handler()
    message = _message(raw)
    result = SimpleNamespace(ok=True, reason="scheduled", end_at=None)
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch("app.handlers.group_text_call_commands.call_cmd_service.end_call_now", AsyncMock()) as end_now,
        patch("app.handlers.group_text_call_commands.call_cmd_service.schedule_call_end", AsyncMock(return_value=result)) as schedule,
    ):
        await handler(AsyncMock(), message)

    end_now.assert_not_awaited()
    schedule.assert_awaited_once()
    assert schedule.await_args.args[2] == 25


@pytest.mark.asyncio
async def test_bare_end_call_no_active_and_failure_feedback():
    handler = _handler()
    no_active = _message("End Call")
    failed = _message("پایان کال")

    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_text_call_commands.call_cmd_service.end_call_now",
            AsyncMock(return_value=SimpleNamespace(ok=False, reason="no_active_call")),
        ) as svc,
    ):
        await handler(AsyncMock(), no_active)
    svc.assert_awaited_once()
    assert "فعالی" in no_active.reply.await_args.args[0]

    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch(
            "app.handlers.group_text_call_commands.call_cmd_service.end_call_now",
            AsyncMock(return_value=SimpleNamespace(ok=False, reason="api_error")),
        ) as svc,
    ):
        await handler(AsyncMock(), failed)
    svc.assert_awaited_once()
    assert failed.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_bare_end_call_permission_denied_blocks_service():
    handler = _handler()
    message = _message("End Call", user_id=90003)
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=False)),
        patch("app.handlers.group_text_call_commands.call_cmd_service.end_call_now", AsyncMock()) as svc,
    ):
        await handler(AsyncMock(), message)

    svc.assert_not_awaited()
    assert "دسترسی" in message.reply.await_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["لینک کال", "Link Call"])
async def test_link_call_exact_help_commands_invoke_public_link_service(raw: str):
    handler = _handler()
    message = _message(raw)
    result = SimpleNamespace(ok=True, reason="ok", link="https://t.me/public?videochat")
    with patch(
        "app.handlers.group_text_call_commands.call_cmd_service.get_call_link",
        AsyncMock(return_value=result),
    ) as svc:
        await handler(AsyncMock(), message)

    svc.assert_awaited_once()
    assert "https://t.me/public?videochat" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_start_call_invokes_service():
    handler = _handler()
    message = _message("StartCall")
    result = SimpleNamespace(ok=True, reason="started")
    with (
        patch("app.handlers.group_text_call_commands._can_manage_call_command", AsyncMock(return_value=True)),
        patch("app.handlers.group_text_call_commands.call_cmd_service.start_call", AsyncMock(return_value=result)) as svc,
    ):
        await handler(AsyncMock(), message)
    svc.assert_awaited_once()
    assert "شروع" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_end_call_now_service_uses_discard_and_leave_paths():
    from app.services import group_text_call_command_service as service

    chat_id = -10055123
    with (
        patch("app.services.group_text_call_command_service.is_call_active", AsyncMock(return_value=True)),
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.raw_discard_group_call_available",
            return_value=True,
        ),
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.try_discard_group_call",
            AsyncMock(return_value=SimpleNamespace(ok=True, reason="discarded")),
        ) as discard,
        patch("app.services.group_text_call_command_service.CallService.leave_voice_chat", AsyncMock(return_value=True)) as leave,
        patch("app.services.group_text_call_command_service.repo.clear_scheduled_end", AsyncMock()) as clear,
    ):
        result = await service.end_call_now(SimpleNamespace(), chat_id)

    assert result.ok is True
    discard.assert_awaited_once_with(chat_id, reason="group_text_command_immediate_end")
    leave.assert_awaited_once()
    clear.assert_awaited_once_with(chat_id)


@pytest.mark.asyncio
async def test_end_call_now_no_active_skips_mutation():
    from app.services import group_text_call_command_service as service

    chat_id = -10055124
    with (
        patch("app.services.group_text_call_command_service.is_call_active", AsyncMock(return_value=False)),
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.try_discard_group_call",
            AsyncMock(),
        ) as discard,
        patch("app.services.group_text_call_command_service.CallService.leave_voice_chat", AsyncMock()) as leave,
    ):
        result = await service.end_call_now(SimpleNamespace(), chat_id)

    assert result.ok is False
    assert result.reason == "no_active_call"
    discard.assert_not_awaited()
    leave.assert_not_awaited()


@pytest.mark.asyncio
async def test_end_call_now_leave_failure_reports_failure_without_clearing_schedule():
    from app.services import group_text_call_command_service as service

    chat_id = -10055125
    with (
        patch("app.services.group_text_call_command_service.is_call_active", AsyncMock(return_value=True)),
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.raw_discard_group_call_available",
            return_value=False,
        ),
        patch("app.services.group_text_call_command_service.CallService.leave_voice_chat", AsyncMock(return_value=False)) as leave,
        patch("app.services.group_text_call_command_service.CallService.pop_leave_failure_key", return_value="playback_cmd.failed"),
        patch("app.services.group_text_call_command_service.repo.clear_scheduled_end", AsyncMock()) as clear,
    ):
        result = await service.end_call_now(SimpleNamespace(), chat_id)

    assert result.ok is False
    assert result.reason == "api_error"
    leave.assert_awaited_once()
    clear.assert_not_awaited()

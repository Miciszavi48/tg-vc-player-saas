from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import MusicAdmin, PlayerVip
from app.utils.manager_text_commands import parse_manager_text_command


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


def _handler():
    from app.handlers import manager_text_commands

    bot = _RecorderBot()
    manager_text_commands.register(bot, None)
    return bot.message_handlers[0]


def _message(
    text: str,
    *,
    chat_id: int,
    user_id: int = 123456789,
    chat_type: str = "supergroup",
):
    msg = SimpleNamespace()
    msg.chat = SimpleNamespace(id=chat_id, title=f"Group {chat_id}", type=SimpleNamespace(value=chat_type))
    msg.from_user = SimpleNamespace(id=user_id, username=f"user{user_id}", first_name=f"User{user_id}")
    msg.text = text
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = None
    msg.reply = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.continue_propagation = lambda: None
    return msg


async def _install(chat_id: int) -> None:
    await _handler()(AsyncMock(), _message("AddMusic", chat_id=chat_id))


@pytest.mark.asyncio
async def test_normal_user_denied_for_install_charge_and_role_management():
    chat_id = -10066401
    await _install(chat_id)
    handler = _handler()

    for text in ("RemMusic", "ChargeMusic +10", "AddOwnerM 8801", "PromoteM 8802", "SetVip Player 8804"):
        msg = _message(text, chat_id=chat_id, user_id=99001)
        await handler(AsyncMock(), msg)
        assert "دسترسی" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_expire_status_is_public_read_only_for_managed_group():
    chat_id = -10066402
    await _install(chat_id)
    msg = _message("اعتبار موزیک", chat_id=chat_id, user_id=99002)

    await _handler()(AsyncMock(), msg)

    assert "وضعیت اعتبار" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_wrong_scopes_are_denied_visibly():
    handler = _handler()

    private_install = _message("AddMusic", chat_id=99003, chat_type="private")
    await handler(AsyncMock(), private_install)
    assert "فقط داخل گروه" in private_install.reply.await_args.args[0]

    private_charge_no_target = _message("ChargeMusic +10", chat_id=99003, chat_type="private")
    await handler(AsyncMock(), private_charge_no_target)
    assert "شناسه عددی گروه" in private_charge_no_target.reply.await_args.args[0]

    group_charge_with_target = _message("ChargeMusic -10066402 +10", chat_id=-10066403)
    await handler(AsyncMock(), group_charge_with_target)
    assert "فقط در پیوی" in group_charge_with_target.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_deputy_can_manage_mods_but_not_owners():
    chat_id = -10066404
    deputy_id = 99004
    await _install(chat_id)
    handler = _handler()
    await handler(AsyncMock(), _message(f"AddDeputyM {deputy_id}", chat_id=chat_id))

    promote = _message("PromoteM 8802", chat_id=chat_id, user_id=deputy_id)
    await handler(AsyncMock(), promote)
    async with async_session() as session:
        mod = (
            await session.execute(
                select(MusicAdmin).where(
                    MusicAdmin.chat_id == chat_id,
                    MusicAdmin.user_id == 8802,
                )
            )
        ).scalar_one_or_none()
    assert mod is not None

    owner_add = _message("AddOwnerM 8803", chat_id=chat_id, user_id=deputy_id)
    await handler(AsyncMock(), owner_add)
    assert "دسترسی" in owner_add.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_music_admin_can_manage_vip_but_vip_cannot_manage_mods():
    chat_id = -10066405
    admin_id = 99005
    vip_id = 99006
    await _install(chat_id)
    handler = _handler()
    await handler(AsyncMock(), _message(f"PromoteM {admin_id}", chat_id=chat_id))

    add_vip = _message(f"SetVip Player {vip_id}", chat_id=chat_id, user_id=admin_id)
    await handler(AsyncMock(), add_vip)
    async with async_session() as session:
        vip = (
            await session.execute(
                select(PlayerVip).where(
                    PlayerVip.chat_id == chat_id,
                    PlayerVip.user_id == vip_id,
                )
            )
        ).scalar_one_or_none()
    assert vip is not None

    denied = _message("PromoteM 8805", chat_id=chat_id, user_id=vip_id)
    await handler(AsyncMock(), denied)
    assert "دسترسی" in denied.reply.await_args.args[0]


def test_parser_does_not_collide_with_group_call_or_playback_or_cancel():
    from app.utils.group_text_commands import parse_group_text_command
    from app.utils.playback_commands import parse_playback_command
    from app.utils.text_commands import is_escape_command, normalize_command_text

    assert parse_manager_text_command("StartCall") is None
    assert parse_group_text_command("StartCall") is not None
    assert parse_manager_text_command("play test") is None
    assert parse_playback_command(normalize_command_text("play test")) is not None
    assert parse_manager_text_command("/cancel") is None
    assert is_escape_command("/cancel") is True

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, update

from app.database.engine import async_session
from app.database.models import MusicAdmin, PlayerDeputy, PlayerOwner, PlayerVip, Sudo
from app.repositories import admin_repo, user_repo


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
    msg.from_user = SimpleNamespace(id=user_id, username=f"user{user_id}", first_name=f"User {user_id}")
    msg.text = text
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = None
    msg.reply = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.continue_propagation = lambda: None
    return msg


async def _install(chat_id: int, user_id: int = 123456789) -> None:
    await _handler()(AsyncMock(), _message("AddMusic", chat_id=chat_id, user_id=user_id))


async def _set_sudo_permissions(user_id: int, **perms: bool) -> None:
    async with async_session() as session:
        await session.execute(update(Sudo).where(Sudo.user_id == user_id).values(**perms))
        await session.commit()


async def _exists(model, chat_id: int, user_id: int) -> bool:
    async with async_session() as session:
        result = await session.execute(
            select(model).where(model.chat_id == chat_id, model.user_id == user_id)
        )
        return result.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_real_sudo_permission_rows_control_install_charge_and_remove():
    chat_id = -10067501
    sudo_id = 675001
    await user_repo.add_sudo(sudo_id, username="sudo675001")
    await _set_sudo_permissions(
        sudo_id,
        can_manage_groups=True,
        can_manage_credit=False,
        can_remove_bot=False,
    )
    handler = _handler()

    install = _message("AddMusic", chat_id=chat_id, user_id=sudo_id)
    await handler(AsyncMock(), install)
    assert "راه‌اندازی پلیر" in install.reply.await_args.args[0]
    assert install.reply.await_args.kwargs.get("reply_markup") is not None

    denied_charge = _message("ChargeMusic +10", chat_id=chat_id, user_id=sudo_id)
    await handler(AsyncMock(), denied_charge)
    assert "دسترسی" in denied_charge.reply.await_args.args[0]

    await _set_sudo_permissions(sudo_id, can_manage_credit=True, can_remove_bot=True)
    still_denied_charge = _message("ChargeMusic +10", chat_id=chat_id, user_id=sudo_id)
    await handler(AsyncMock(), still_denied_charge)
    assert "دسترسی" in still_denied_charge.reply.await_args.args[0]

    uninstall = _message("RemMusic", chat_id=chat_id, user_id=sudo_id)
    await handler(AsyncMock(), uninstall)
    assert "حذف شد" in uninstall.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_player_owner_deputy_and_mod_permissions_are_backed_by_real_role_rows():
    chat_id = -10067502
    owner_id = 675201
    deputy_id = 675202
    mod_id = 675203
    await _install(chat_id)
    async with async_session() as session:
        session.add(PlayerOwner(chat_id=chat_id, user_id=owner_id))
        session.add(PlayerDeputy(chat_id=chat_id, user_id=deputy_id))
        session.add(MusicAdmin(chat_id=chat_id, user_id=mod_id))
        await session.commit()
    assert await admin_repo.is_player_owner(owner_id, chat_id) is True
    assert await admin_repo.is_player_deputy(deputy_id, chat_id) is True
    assert await admin_repo.is_music_admin_or_above(mod_id, chat_id) is True

    handler = _handler()
    owner_add_deputy = _message("AddDeputyM 675299", chat_id=chat_id, user_id=owner_id)
    await handler(AsyncMock(), owner_add_deputy)
    assert await _exists(PlayerDeputy, chat_id, 675299)

    deputy_promote = _message("PromoteM 675298", chat_id=chat_id, user_id=deputy_id)
    await handler(AsyncMock(), deputy_promote)
    assert await _exists(MusicAdmin, chat_id, 675298)

    mod_add_helper = _message("AddhelperM", chat_id=chat_id, user_id=mod_id)
    with patch(
        "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
        AsyncMock(return_value=None),
    ):
        await handler(AsyncMock(), mod_add_helper)
    assert "دسترسی" not in mod_add_helper.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_normal_users_denied_for_mutations_and_do_not_create_rows():
    chat_id = -10067503
    normal_id = 675301
    await _install(chat_id)
    handler = _handler()

    for text, model, target_id in (
        ("AddOwnerM 675391", PlayerOwner, 675391),
        ("AddDeputyM 675392", PlayerDeputy, 675392),
        ("PromoteM 675393", MusicAdmin, 675393),
        ("SetVip Player 675394", PlayerVip, 675394),
    ):
        msg = _message(text, chat_id=chat_id, user_id=normal_id)
        await handler(AsyncMock(), msg)
        assert "دسترسی" in msg.reply.await_args.args[0]
        assert await _exists(model, chat_id, target_id) is False

    charge = _message("ChargeMusic +10", chat_id=chat_id, user_id=normal_id)
    await handler(AsyncMock(), charge)
    assert "دسترسی" in charge.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_expire_status_is_public_but_mutations_still_require_permissions():
    chat_id = -10067504
    user_id = 675401
    await _install(chat_id)

    expire = _message("اعتبار موزیک", chat_id=chat_id, user_id=user_id)
    await _handler()(AsyncMock(), expire)
    assert "وضعیت اعتبار" in expire.reply.await_args.args[0]

    uninstall = _message("RemMusic", chat_id=chat_id, user_id=user_id)
    await _handler()(AsyncMock(), uninstall)
    assert "دسترسی" in uninstall.reply.await_args.args[0]

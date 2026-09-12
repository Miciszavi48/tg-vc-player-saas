from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import MusicAdmin, PlayerDeputy, PlayerOwner
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
    reply_user: SimpleNamespace | None = None,
    entities: list[SimpleNamespace] | None = None,
):
    msg = SimpleNamespace()
    msg.chat = SimpleNamespace(id=chat_id, title=f"Group {chat_id}", type=SimpleNamespace(value="supergroup"))
    msg.from_user = SimpleNamespace(id=user_id, username=f"user{user_id}", first_name=f"User {user_id}")
    msg.text = text
    msg.caption = None
    msg.entities = entities or []
    msg.reply_to_message = SimpleNamespace(from_user=reply_user) if reply_user is not None else None
    msg.reply = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.continue_propagation = lambda: None
    return msg


async def _install(chat_id: int) -> None:
    await _handler()(AsyncMock(), _message("AddMusic", chat_id=chat_id))


async def _ids(model, chat_id: int) -> set[int]:
    async with async_session() as session:
        return set(
            (
                await session.execute(select(model.user_id).where(model.chat_id == chat_id))
            ).scalars().all()
        )


@pytest.mark.asyncio
async def test_owner_add_remove_list_clear_persist_and_affect_permission_helper():
    chat_id = -10067401
    owner_id = 674101
    await _install(chat_id)
    handler = _handler()
    reply_user = SimpleNamespace(id=owner_id, username="owner674101", first_name="Owner")

    add = _message("ارتقا مالک موزیک", chat_id=chat_id, reply_user=reply_user)
    await handler(AsyncMock(), add)
    assert owner_id in await _ids(PlayerOwner, chat_id)
    assert await admin_repo.is_player_owner(owner_id, chat_id) is True
    assert await admin_repo.is_music_admin_or_above(owner_id, chat_id) is True

    listed = _message("OwnerListM", chat_id=chat_id)
    await handler(AsyncMock(), listed)
    assert str(owner_id) in listed.reply.await_args.args[0]

    remove = _message(f"RemOwnerMusic {owner_id}", chat_id=chat_id)
    await handler(AsyncMock(), remove)
    assert await admin_repo.is_player_owner(owner_id, chat_id) is False
    assert await admin_repo.is_music_admin_or_above(owner_id, chat_id) is False

    await handler(AsyncMock(), _message("AddOwnerM 674102", chat_id=chat_id))
    clear = _message("ClearOwnerListMusic", chat_id=chat_id)
    await handler(AsyncMock(), clear)
    assert await _ids(PlayerOwner, chat_id) == set()


@pytest.mark.asyncio
async def test_deputy_add_remove_clear_invalidate_permission_cache_and_can_manage_mods():
    chat_id = -10067402
    deputy_id = 674201
    await _install(chat_id)
    handler = _handler()

    assert await admin_repo.is_player_deputy(deputy_id, chat_id) is False
    assert await admin_repo.is_vip(deputy_id, chat_id) is False
    add = _message(f"AddDeputyM {deputy_id}", chat_id=chat_id)
    await handler(AsyncMock(), add)
    assert deputy_id in await _ids(PlayerDeputy, chat_id)
    assert await admin_repo.is_player_deputy(deputy_id, chat_id) is True
    assert await admin_repo.is_vip(deputy_id, chat_id) is False

    promote = _message("PromoteM 674299", chat_id=chat_id, user_id=deputy_id)
    await handler(AsyncMock(), promote)
    assert 674299 in await _ids(MusicAdmin, chat_id)

    remove = _message(f"DemDeputyPlayer {deputy_id}", chat_id=chat_id)
    await handler(AsyncMock(), remove)
    assert await admin_repo.is_player_deputy(deputy_id, chat_id) is False

    await handler(AsyncMock(), _message("AddDeputyM 674202", chat_id=chat_id))
    assert await admin_repo.is_player_deputy(674202, chat_id) is True
    clear = _message("ClearDeputyListM", chat_id=chat_id)
    await handler(AsyncMock(), clear)
    assert await _ids(PlayerDeputy, chat_id) == set()
    assert await admin_repo.is_player_deputy(674202, chat_id) is False


@pytest.mark.asyncio
async def test_vip_cannot_manage_mods_and_admin_can_manage_vip():
    chat_id = -10067407
    vip_id = 674701
    admin_id = 674702
    await _install(chat_id)
    handler = _handler()

    await handler(AsyncMock(), _message(f"SetVip Player {vip_id}", chat_id=chat_id))
    assert await admin_repo.is_vip(vip_id, chat_id) is True

    denied = _message("PromoteM 674799", chat_id=chat_id, user_id=vip_id)
    await handler(AsyncMock(), denied)
    assert 674799 not in await _ids(MusicAdmin, chat_id)
    assert "دسترسی" in denied.reply.await_args.args[0]

    await handler(AsyncMock(), _message(f"PromoteM {admin_id}", chat_id=chat_id))
    promote_vip = _message("SetVip Player 674703", chat_id=chat_id, user_id=admin_id)
    await handler(AsyncMock(), promote_vip)
    assert await admin_repo.is_vip(674703, chat_id) is True


@pytest.mark.asyncio
async def test_mod_promote_demote_list_clear_persist_and_affect_permission_helper():
    chat_id = -10067403
    mod_id = 674301
    await _install(chat_id)
    handler = _handler()

    promote = _message(f"PromotePlayer {mod_id}", chat_id=chat_id)
    await handler(AsyncMock(), promote)
    assert mod_id in await _ids(MusicAdmin, chat_id)
    assert await admin_repo.is_music_admin_or_above(mod_id, chat_id) is True

    listed = _message("ModListMusic", chat_id=chat_id)
    await handler(AsyncMock(), listed)
    assert str(mod_id) in listed.reply.await_args.args[0]

    demote = _message(f"DemoteM {mod_id}", chat_id=chat_id)
    await handler(AsyncMock(), demote)
    assert await admin_repo.is_music_admin_or_above(mod_id, chat_id) is False

    await handler(AsyncMock(), _message("PromoteM 674302", chat_id=chat_id))
    clear = _message("ClearModListPlayer", chat_id=chat_id)
    await handler(AsyncMock(), clear)
    assert await _ids(MusicAdmin, chat_id) == set()


@pytest.mark.asyncio
async def test_role_tables_remain_separate_and_clear_preserves_higher_roles():
    chat_id = -10067404
    await _install(chat_id)
    async with async_session() as session:
        session.add(PlayerOwner(chat_id=chat_id, user_id=674401))
        session.add(PlayerDeputy(chat_id=chat_id, user_id=674402))
        session.add_all(
            [
                MusicAdmin(chat_id=chat_id, user_id=674401),
                MusicAdmin(chat_id=chat_id, user_id=674402),
                MusicAdmin(chat_id=chat_id, user_id=674403),
            ]
        )
        await session.commit()

    clear_mod = _message("پاکسازی لیست مدیران موزیک", chat_id=chat_id)
    await _handler()(AsyncMock(), clear_mod)

    assert await _ids(PlayerOwner, chat_id) == {674401}
    assert await _ids(PlayerDeputy, chat_id) == {674402}
    assert await _ids(MusicAdmin, chat_id) == {674401, 674402}


@pytest.mark.asyncio
async def test_protected_roles_cannot_be_removed_or_cleared():
    chat_id = -10067405
    protected_id = 674501
    await _install(chat_id)
    await user_repo.add_owner(protected_id, username="global674501")
    async with async_session() as session:
        session.add(PlayerOwner(chat_id=chat_id, user_id=protected_id))
        session.add(PlayerOwner(chat_id=chat_id, user_id=674502))
        await session.commit()

    remove = _message(f"RemOwnerMusic {protected_id}", chat_id=chat_id)
    await _handler()(AsyncMock(), remove)
    assert protected_id in await _ids(PlayerOwner, chat_id)
    assert "محافظت" in remove.reply.await_args.args[0]

    clear = _message("ClearOwnerListM", chat_id=chat_id)
    await _handler()(AsyncMock(), clear)
    assert await _ids(PlayerOwner, chat_id) == {protected_id}


@pytest.mark.asyncio
async def test_target_resolution_by_reply_id_username_and_text_mention():
    chat_id = -10067406
    await _install(chat_id)
    handler = _handler()

    await handler(
        AsyncMock(),
        _message(
            "AddOwnerM",
            chat_id=chat_id,
            reply_user=SimpleNamespace(id=674601, username="replyuser", first_name="Reply"),
        ),
    )
    await handler(AsyncMock(), _message("AddDeputyM 674602", chat_id=chat_id))

    client = AsyncMock()
    client.get_users = AsyncMock(return_value=SimpleNamespace(id=674603, username="nameduser", first_name="Named"))
    await handler(client, _message("PromoteM @nameduser", chat_id=chat_id))

    mention_text = "AddOwnerM Alice"
    entity = SimpleNamespace(
        type=SimpleNamespace(value="text_mention"),
        offset=10,
        length=5,
        user=SimpleNamespace(id=674604, username="alice", first_name="Alice"),
    )
    await handler(AsyncMock(), _message(mention_text, chat_id=chat_id, entities=[entity]))

    assert await _ids(PlayerOwner, chat_id) == {674601, 674604}
    assert await _ids(PlayerDeputy, chat_id) == {674602}
    assert await _ids(MusicAdmin, chat_id) == {674603}

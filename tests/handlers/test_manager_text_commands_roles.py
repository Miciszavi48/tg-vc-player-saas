from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import MusicAdmin, PlayerDeputy, PlayerOwner, PlayerVip
from app.repositories import user_repo


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
):
    msg = SimpleNamespace()
    msg.chat = SimpleNamespace(id=chat_id, title=f"Group {chat_id}", type=SimpleNamespace(value="supergroup"))
    msg.from_user = SimpleNamespace(id=user_id, username="dev", first_name="Dev")
    msg.text = text
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = (
        SimpleNamespace(from_user=reply_user) if reply_user is not None else None
    )
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
async def test_owner_add_remove_list_clear_persist():
    chat_id = -10066301
    await _install(chat_id)
    handler = _handler()
    reply_user = SimpleNamespace(id=7301, username="owner7301", first_name="Owner")

    add = _message("ارتقا مالک موزیک", chat_id=chat_id, reply_user=reply_user)
    await handler(AsyncMock(), add)
    assert await _ids(PlayerOwner, chat_id) == {7301}
    assert "اضافه شد" in add.reply.await_args.args[0]

    duplicate = _message("AddOwnerM 7301", chat_id=chat_id)
    await handler(AsyncMock(), duplicate)
    assert "از قبل" in duplicate.reply.await_args.args[0]

    listed = _message("OwnerListMusic", chat_id=chat_id)
    await handler(AsyncMock(), listed)
    assert "7301" in listed.reply.await_args.args[0]

    remove = _message("RemOwnerMusic 7301", chat_id=chat_id)
    await handler(AsyncMock(), remove)
    assert await _ids(PlayerOwner, chat_id) == set()
    assert "حذف شد" in remove.reply.await_args.args[0]

    await handler(AsyncMock(), _message("AddOwnerM 7302", chat_id=chat_id))
    clear = _message("ClearOwnerListM", chat_id=chat_id)
    await handler(AsyncMock(), clear)
    assert await _ids(PlayerOwner, chat_id) == set()
    assert "پاکسازی" in clear.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_deputy_add_remove_list_clear_persist_separately():
    chat_id = -10066302
    await _install(chat_id)
    handler = _handler()

    await handler(AsyncMock(), _message("AddDeputyM 7401", chat_id=chat_id))
    assert await _ids(PlayerDeputy, chat_id) == {7401}
    assert await _ids(PlayerVip, chat_id) == set()
    assert await _ids(PlayerOwner, chat_id) == set()

    listed = _message("لیست معاونین موزیک", chat_id=chat_id)
    await handler(AsyncMock(), listed)
    assert "7401" in listed.reply.await_args.args[0]

    remove = _message("DemDeputypPlayer 7401", chat_id=chat_id)
    await handler(AsyncMock(), remove)
    assert await _ids(PlayerDeputy, chat_id) == set()

    await handler(AsyncMock(), _message("SetDeputyMusic 7402", chat_id=chat_id))
    clear = _message("ClearDeputyListPlayer", chat_id=chat_id)
    await handler(AsyncMock(), clear)
    assert await _ids(PlayerDeputy, chat_id) == set()


@pytest.mark.asyncio
async def test_vip_add_remove_list_clear_persist_separately():
    chat_id = -10066307
    await _install(chat_id)
    handler = _handler()

    await handler(AsyncMock(), _message("SetVip Player 7901", chat_id=chat_id))
    assert await _ids(PlayerVip, chat_id) == {7901}
    assert await _ids(PlayerDeputy, chat_id) == set()

    listed = _message("ListVip Player", chat_id=chat_id)
    await handler(AsyncMock(), listed)
    assert "7901" in listed.reply.await_args.args[0]

    remove = _message("RemVip Player 7901", chat_id=chat_id)
    await handler(AsyncMock(), remove)
    assert await _ids(PlayerVip, chat_id) == set()

    await handler(AsyncMock(), _message("SetVip Player 7902", chat_id=chat_id))
    clear = _message("ClearListVip Player", chat_id=chat_id)
    await handler(AsyncMock(), clear)
    assert await _ids(PlayerVip, chat_id) == set()


@pytest.mark.asyncio
async def test_mod_promote_by_username_demote_list_and_clear():
    chat_id = -10066303
    await _install(chat_id)
    handler = _handler()
    client = AsyncMock()
    client.get_users = AsyncMock(
        return_value=SimpleNamespace(id=7501, username="moduser", first_name="Mod")
    )

    promote = _message("PromoteM @moduser", chat_id=chat_id)
    await handler(client, promote)
    assert await _ids(MusicAdmin, chat_id) == {7501}

    listed = _message("ModListPlayer", chat_id=chat_id)
    await handler(client, listed)
    assert "7501" in listed.reply.await_args.args[0]

    demote = _message("DemoteMusic 7501", chat_id=chat_id)
    await handler(client, demote)
    assert await _ids(MusicAdmin, chat_id) == set()

    await handler(client, _message("PromoteMusic 7502", chat_id=chat_id))
    clear = _message("ClearModListM", chat_id=chat_id)
    await handler(client, clear)
    assert await _ids(MusicAdmin, chat_id) == set()


@pytest.mark.asyncio
async def test_role_lists_do_not_collide():
    chat_id = -10066304
    await _install(chat_id)
    handler = _handler()

    await handler(AsyncMock(), _message("AddOwnerM 7601", chat_id=chat_id))
    await handler(AsyncMock(), _message("AddDeputyM 7602", chat_id=chat_id))
    await handler(AsyncMock(), _message("SetVip Player 7604", chat_id=chat_id))
    await handler(AsyncMock(), _message("PromoteM 7603", chat_id=chat_id))

    assert await _ids(PlayerOwner, chat_id) == {7601}
    assert await _ids(PlayerDeputy, chat_id) == {7602}
    assert await _ids(MusicAdmin, chat_id) == {7603}
    assert await _ids(PlayerVip, chat_id) == {7604}


@pytest.mark.asyncio
async def test_protected_global_owner_cannot_be_removed_from_player_owner_list():
    chat_id = -10066305
    protected_id = 7701
    await _install(chat_id)
    await user_repo.add_owner(protected_id, username="global_owner")
    async with async_session() as session:
        session.add(PlayerOwner(chat_id=chat_id, user_id=protected_id, username="global_owner"))
        await session.commit()

    remove = _message(f"RemOwnerMusic {protected_id}", chat_id=chat_id)
    await _handler()(AsyncMock(), remove)

    assert protected_id in await _ids(PlayerOwner, chat_id)
    assert "محافظت" in remove.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_clear_mods_preserves_owner_and_deputy_mod_rows():
    chat_id = -10066306
    await _install(chat_id)
    async with async_session() as session:
        session.add(PlayerOwner(chat_id=chat_id, user_id=7801))
        session.add(PlayerDeputy(chat_id=chat_id, user_id=7802))
        session.add_all(
            [
                MusicAdmin(chat_id=chat_id, user_id=7801),
                MusicAdmin(chat_id=chat_id, user_id=7802),
                MusicAdmin(chat_id=chat_id, user_id=7803),
            ]
        )
        await session.commit()

    clear = _message("پاکسازی لیست مدیران موزیک", chat_id=chat_id)
    await _handler()(AsyncMock(), clear)

    assert await _ids(MusicAdmin, chat_id) == {7801, 7802}

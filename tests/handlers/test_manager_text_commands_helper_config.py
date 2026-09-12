from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import Group, HelperAccount, HelperChatBinding, MusicAdmin, PlayerDeputy, PlayerOwner, PlayerVip


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


def _message(text: str, *, chat_id: int, user_id: int = 123456789):
    msg = SimpleNamespace()
    msg.chat = SimpleNamespace(id=chat_id, title=f"Group {chat_id}", type=SimpleNamespace(value="supergroup"))
    msg.from_user = SimpleNamespace(id=user_id, username="dev", first_name="Dev")
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


async def _helper(chat_id: int, suffix: int = 1) -> HelperAccount:
    async with async_session() as session:
        helper = HelperAccount(phone=f"+1777{chat_id % 100000}{suffix}", status="active")
        session.add(helper)
        await session.flush()
        await session.commit()
        await session.refresh(helper)
        return helper


async def _binding_helper_id(chat_id: int) -> int | None:
    async with async_session() as session:
        value = (
            await session.execute(
                select(HelperChatBinding.helper_account_id).where(HelperChatBinding.chat_id == chat_id)
            )
        ).scalar_one_or_none()
    return int(value) if value is not None else None


async def _bind(chat_id: int, helper_id: int) -> None:
    async with async_session() as session:
        session.add(HelperChatBinding(chat_id=chat_id, helper_account_id=helper_id))
        await session.commit()


async def _admin_members(users: list[SimpleNamespace]):
    for user in users:
        yield SimpleNamespace(user=user)


@pytest.mark.asyncio
async def test_addhelper_joins_and_persists_binding():
    chat_id = -10066201
    await _install(chat_id)
    helper = await _helper(chat_id)

    async def _fake_ensure(chat_id_arg: int, reason: str = "manual") -> str:
        await _bind(chat_id_arg, helper.id)
        return "success"

    message = _message("افزودن کمکی موزیک", chat_id=chat_id)
    with patch("app.services.call_service.ensure_helper_present_for_group", AsyncMock(side_effect=_fake_ensure)):
        await _handler()(AsyncMock(), message)

    assert await _binding_helper_id(chat_id) == helper.id
    assert "متصل" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_addhelper_idempotent_existing_binding_reused():
    chat_id = -10066202
    await _install(chat_id)
    helper = await _helper(chat_id)
    await _bind(chat_id, helper.id)

    message = _message("AddhelperMusic", chat_id=chat_id)
    with patch(
        "app.services.call_service.ensure_helper_present_for_group",
        AsyncMock(return_value="already_present"),
    ) as ensure:
        await _handler()(AsyncMock(), message)

    ensure.assert_awaited_once()
    assert await _binding_helper_id(chat_id) == helper.id
    assert "از قبل" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_addhelper_no_helper_available_and_join_failure_are_visible():
    chat_id = -10066203
    await _install(chat_id)
    handler = _handler()

    unavailable = _message("AddhelperM", chat_id=chat_id)
    with patch("app.services.call_service.ensure_helper_present_for_group", AsyncMock(return_value="unavailable")):
        await handler(AsyncMock(), unavailable)
    assert "در دسترس نیست" in unavailable.reply.await_args.args[0]

    failed = _message("افزودن کمکی پلیر", chat_id=chat_id)
    with patch("app.services.call_service.ensure_helper_present_for_group", AsyncMock(return_value="failed")):
        await handler(AsyncMock(), failed)
    assert "ناموفق" in failed.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_config_imports_telegram_admins_and_preserves_owners():
    chat_id = -10066204
    await _install(chat_id)
    async with async_session() as session:
        session.add(PlayerOwner(chat_id=chat_id, user_id=8001, username="owner"))
        session.add(PlayerDeputy(chat_id=chat_id, user_id=8004, username="deputy"))
        session.add(PlayerVip(chat_id=chat_id, user_id=8005, username="vip"))
        session.add(MusicAdmin(chat_id=chat_id, user_id=9999, username="old"))
        await session.commit()

    users = [
        SimpleNamespace(id=8001, username="owner", first_name="Owner", is_bot=False),
        SimpleNamespace(id=8002, username="admin2", first_name="Admin2", is_bot=False),
        SimpleNamespace(id=8003, username="bot", first_name="Bot", is_bot=True),
    ]
    client = AsyncMock()
    client.get_chat_members = lambda chat_id_arg, filter=None: _admin_members(users)
    message = _message("ConfigMusic", chat_id=chat_id)

    await _handler()(client, message)

    async with async_session() as session:
        admins = list(
            (
                await session.execute(select(MusicAdmin).where(MusicAdmin.chat_id == chat_id))
            ).scalars().all()
        )
        owners = list(
            (
                await session.execute(select(PlayerOwner).where(PlayerOwner.chat_id == chat_id))
            ).scalars().all()
        )
    assert {a.user_id for a in admins} == {8001, 8002}
    assert {o.user_id for o in owners} == {8001}
    text = message.reply.await_args.args[0]
    assert "⊹ مدیران:" in text
    assert "⊹ مالک‌ها:" in text
    assert "⊹ معاون‌ها:" in text
    assert "⊹ ویژه‌ها:" in text
    assert "@owner" in text
    assert "@deputy" in text
    assert "@vip" in text
    assert "@admin2" in text
    assert "⊹ واردشده: 2" in text


@pytest.mark.asyncio
async def test_config_empty_admin_list_returns_visible_error():
    chat_id = -10066205
    await _install(chat_id)
    client = AsyncMock()
    client.get_chat_members = lambda chat_id_arg, filter=None: _admin_members([])
    message = _message("پیکربندی پلیر", chat_id=chat_id)

    await _handler()(client, message)

    assert "پیدا نشد" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_config_unmanaged_group_returns_error():
    chat_id = -10066206
    message = _message("ConfigPlayer", chat_id=chat_id)

    await _handler()(AsyncMock(), message)

    assert "فعال نیست" in message.reply.await_args.args[0]
    async with async_session() as session:
        group = (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()
    assert group is None

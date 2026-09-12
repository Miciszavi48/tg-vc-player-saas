from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import ChatSettings, Group, GroupCredit, HelperAccount, HelperChatBinding, MusicAdmin


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
    chat_id: int = -10066001,
    user_id: int = 123456789,
    chat_type: str = "supergroup",
):
    msg = SimpleNamespace()
    msg.chat = SimpleNamespace(id=chat_id, title=f"Group {chat_id}", type=SimpleNamespace(value=chat_type))
    msg.from_user = SimpleNamespace(id=user_id, username="dev", first_name="Dev")
    msg.text = text
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = None
    msg.reply = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.continue_propagation = lambda: None
    return msg


async def _get_group(chat_id: int) -> Group | None:
    async with async_session() as session:
        return (await session.execute(select(Group).where(Group.chat_id == chat_id))).scalar_one_or_none()


async def _get_credit(chat_id: int) -> GroupCredit | None:
    async with async_session() as session:
        return (
            await session.execute(select(GroupCredit).where(GroupCredit.chat_id == chat_id))
        ).scalar_one_or_none()


async def _install(chat_id: int) -> None:
    handler = _handler()
    await handler(AsyncMock(), _message("افزودن موزیک", chat_id=chat_id))


@pytest.mark.asyncio
async def test_install_group_persists_and_is_idempotent():
    chat_id = -10066101
    handler = _handler()
    first = _message("نصب پلیر", chat_id=chat_id)
    second = _message("AddMusic", chat_id=chat_id)

    await handler(AsyncMock(), first)
    await handler(AsyncMock(), second)

    group = await _get_group(chat_id)
    credit = await _get_credit(chat_id)
    async with async_session() as session:
        settings = (
            await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        ).scalar_one_or_none()

    assert group is not None
    assert group.status == "active"
    assert settings is not None
    assert credit is not None
    assert credit.credit_days >= 0
    assert "راه‌اندازی پلیر" in second.reply.await_args.args[0]
    assert second.reply.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_uninstall_cleans_db_without_bot_leave():
    chat_id = -10066102
    await _install(chat_id)
    async with async_session() as session:
        session.add(MusicAdmin(chat_id=chat_id, user_id=991))
        await session.commit()

    client = AsyncMock()
    message = _message("RemMusic", chat_id=chat_id)
    await _handler()(client, message)

    group = await _get_group(chat_id)
    async with async_session() as session:
        settings = (
            await session.execute(select(ChatSettings).where(ChatSettings.chat_id == chat_id))
        ).scalar_one_or_none()
        admin = (
            await session.execute(select(MusicAdmin).where(MusicAdmin.chat_id == chat_id))
        ).scalar_one_or_none()

    assert group is not None
    assert group.status == "inactive"
    assert settings is None
    assert admin is None
    client.leave_chat.assert_not_awaited()
    assert "باقی" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_leave_cleans_db_and_calls_bot_and_helper_leave():
    chat_id = -10066103
    await _install(chat_id)
    async with async_session() as session:
        helper = HelperAccount(phone="+166103", status="active")
        session.add(helper)
        await session.flush()
        session.add(HelperChatBinding(chat_id=chat_id, helper_account_id=helper.id))
        await session.commit()
        helper_id = helper.id

    client = AsyncMock()
    message = _message("LeaveMusic", chat_id=chat_id)
    with patch(
        "app.services.helper_pool_service.HelperPoolService.leave_chat_as_helper",
        AsyncMock(return_value=True),
    ) as helper_leave:
        await _handler()(client, message)

    group = await _get_group(chat_id)
    assert group is not None
    assert group.status == "inactive"
    client.leave_chat.assert_awaited_once_with(chat_id)
    helper_leave.assert_awaited_once_with(helper_id, chat_id)
    assert "خروج" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_leave_reports_bot_leave_failure_without_fake_success():
    chat_id = -10066104
    await _install(chat_id)
    client = AsyncMock()
    client.leave_chat = AsyncMock(side_effect=RuntimeError("telegram down"))
    message = _message("خروج موزیک", chat_id=chat_id)

    await _handler()(client, message)

    group = await _get_group(chat_id)
    assert group is not None
    assert group.status == "inactive"
    assert "ناموفق" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_charge_group_increase_decrease_and_unlimited_persist():
    chat_id = -10066105
    await _install(chat_id)
    handler = _handler()

    await handler(AsyncMock(), _message("شارژ موزیک 100+", chat_id=chat_id))
    credit = await _get_credit(chat_id)
    assert credit is not None
    after_increase = credit.credit_days

    await handler(AsyncMock(), _message("ChargeMusic -20", chat_id=chat_id))
    credit = await _get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days == max(0, after_increase - 20)

    await handler(AsyncMock(), _message("شارژ پلیر نامحدود", chat_id=chat_id))
    credit = await _get_credit(chat_id)
    assert credit is not None
    assert credit.status == "unlimited"
    assert credit.credit_days > 1000


@pytest.mark.asyncio
async def test_private_target_chat_charge_persists():
    chat_id = -10066106
    await _install(chat_id)
    message = _message(
        f"ChargeMusic {chat_id} +20",
        chat_id=123456789,
        chat_type="private",
    )

    await _handler()(AsyncMock(), message)

    credit = await _get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days >= 20
    assert str(chat_id) in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_charge_unmanaged_group_returns_visible_error():
    chat_id = -10066107
    message = _message(
        f"ChargeM {chat_id} +20",
        chat_id=123456789,
        chat_type="private",
    )

    await _handler()(AsyncMock(), message)

    assert "فعال نیست" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_unauthorized_install_denied_visibly():
    message = _message("AddMusic", chat_id=-10066108, user_id=99108)

    with (
        patch("app.handlers.manager_text_commands.is_developer", return_value=False),
        patch("app.handlers.manager_text_commands.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.handlers.manager_text_commands.sudo_has_permission", AsyncMock(return_value=False)),
    ):
        await _handler()(AsyncMock(), message)

    assert "دسترسی" in message.reply.await_args.args[0]
    assert await _get_group(-10066108) is None

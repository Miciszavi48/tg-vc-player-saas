from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.database.engine import async_session
from app.database.models import CreditHistory, GroupCredit
from app.repositories import credit_repo
from app.utils.playback_auth import authorize_playback_action


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


async def _install(chat_id: int) -> None:
    await _handler()(AsyncMock(), _message("AddMusic", chat_id=chat_id))


async def _history_count(chat_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(CreditHistory).where(CreditHistory.chat_id == chat_id)
        )
        return int(result.scalar() or 0)


async def _runtime_playback_allowed(chat_id: int) -> bool:
    client = AsyncMock()
    client.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=SimpleNamespace(value="administrator")))
    return await authorize_playback_action(
        client,
        _message("پخش", chat_id=chat_id, user_id=672001),
    )


@pytest.mark.asyncio
async def test_group_increase_persists_history_and_runtime_reader_allows_playback():
    chat_id = -10067201
    await _install(chat_id)
    before_history = await _history_count(chat_id)

    msg = _message("شارژ موزیک 100+", chat_id=chat_id)
    await _handler()(AsyncMock(), msg)

    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days >= 100
    assert credit.status == "active"
    assert await _history_count(chat_id) == before_history + 1
    assert await _runtime_playback_allowed(chat_id) is True


@pytest.mark.asyncio
async def test_group_decrease_to_zero_persists_and_runtime_reader_blocks_playback():
    chat_id = -10067202
    await _install(chat_id)
    await _handler()(AsyncMock(), _message("ChargeMusic +20", chat_id=chat_id))

    await _handler()(AsyncMock(), _message("ChargeMusic -36500", chat_id=chat_id))

    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days == 0
    assert credit.status == "expired"
    assert await _runtime_playback_allowed(chat_id) is False


@pytest.mark.asyncio
async def test_unlimited_persists_and_runtime_reader_treats_as_valid_credit():
    chat_id = -10067203
    await _install(chat_id)

    await _handler()(AsyncMock(), _message("شارژ پلیر نامحدود", chat_id=chat_id))

    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.status == "unlimited"
    assert credit.credit_days > 1000
    assert await _runtime_playback_allowed(chat_id) is True


@pytest.mark.asyncio
async def test_private_target_trailing_minus_persian_syntax_updates_real_group_and_history():
    chat_id = -10067204
    await _install(chat_id)

    msg = _message(
        "شارژ موزیک 10067204- 20",
        chat_id=123456789,
        chat_type="private",
    )
    await _handler()(AsyncMock(), msg)

    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days >= 20
    async with async_session() as session:
        history = (
            await session.execute(
                select(CreditHistory)
                .where(CreditHistory.chat_id == chat_id, CreditHistory.note == "manager_text_command")
                .order_by(CreditHistory.id.desc())
            )
        ).scalars().first()
    assert history is not None
    assert history.operation == "charge"
    assert history.amount_days == 20
    assert history.operated_by == 123456789


@pytest.mark.asyncio
async def test_expire_status_reads_current_credit_without_mutation():
    chat_id = -10067205
    await _install(chat_id)
    await _handler()(AsyncMock(), _message("ChargeM +12", chat_id=chat_id))
    before = await credit_repo.get_credit(chat_id)
    before_history = await _history_count(chat_id)

    msg = _message("MusicExpire", chat_id=chat_id, user_id=672005)
    await _handler()(AsyncMock(), msg)

    after = await credit_repo.get_credit(chat_id)
    assert before is not None and after is not None
    assert after.credit_days == before.credit_days
    assert await _history_count(chat_id) == before_history
    assert "وضعیت اعتبار" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_charge_unmanaged_group_and_db_failure_do_not_fake_success():
    unmanaged = _message("ChargeM -10067206 +20", chat_id=123456789, chat_type="private")
    await _handler()(AsyncMock(), unmanaged)
    assert "فعال نیست" in unmanaged.reply.await_args.args[0]

    chat_id = -10067207
    await _install(chat_id)
    failed = _message("ChargeMusic +20", chat_id=chat_id)
    with patch(
        "app.services.manager_command_service.repo.apply_credit_update",
        AsyncMock(side_effect=RuntimeError("history insert failed")),
    ):
        await _handler()(AsyncMock(), failed)

    text = failed.reply.await_args.args[0]
    assert "عملیات انجام نشد" in text
    assert "به‌روزرسانی شد" not in text


@pytest.mark.asyncio
async def test_persian_digits_and_trailing_minus_decrease_parse_and_persist():
    chat_id = -10067208
    await _install(chat_id)
    await _handler()(AsyncMock(), _message("شارژ موزیک ۵۰+", chat_id=chat_id))
    before = await credit_repo.get_credit(chat_id)

    await _handler()(AsyncMock(), _message("شارژ موزیک ۲۰-", chat_id=chat_id))

    after = await credit_repo.get_credit(chat_id)
    assert before is not None and after is not None
    assert after.credit_days == max(0, before.credit_days - 20)

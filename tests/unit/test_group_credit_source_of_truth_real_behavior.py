from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from app.database.engine import async_session
from app.database.models import ChatSettings, CreditHistory, Group, GroupCredit
from app.handlers import credit_commands
from app.handlers import group_panel
from app.repositories import credit_repo, manager_command_repo
from app.services import CreditService, manager_command_service
from app.utils.cache import (
    get_chat_settings_cached,
    get_credit_cached,
    set_credit_cached,
)
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


def _manager_handler():
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
    msg.chat = SimpleNamespace(
        id=chat_id,
        title=f"Group {chat_id}",
        type=SimpleNamespace(value=chat_type),
    )
    msg.from_user = SimpleNamespace(
        id=user_id,
        username=f"user{user_id}",
        first_name=f"User {user_id}",
    )
    msg.text = text
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = None
    msg.reply = AsyncMock(return_value=None)
    msg.reply_text = AsyncMock(return_value=None)
    msg.continue_propagation = lambda: None
    return msg


async def _seed_group(
    chat_id: int,
    *,
    group_status: str | None,
    credit_days: int | None,
    credit_status: str = "active",
    settings: bool = True,
    auto_leave_enabled: bool = False,
    is_trial: bool = False,
    trial_started_at=None,
    trial_expire_at=None,
) -> None:
    async with async_session() as session:
        if group_status is not None:
            session.add(Group(chat_id=chat_id, chat_title=f"Group {chat_id}", status=group_status))
        if credit_days is not None:
            session.add(
                GroupCredit(
                    chat_id=chat_id,
                    chat_type="group",
                    credit_days=credit_days,
                    status=credit_status,
                    total_charged=max(0, credit_days),
                    is_trial=is_trial,
                    trial_started_at=trial_started_at,
                    trial_expire_at=trial_expire_at,
                )
            )
        if settings:
            session.add(
                ChatSettings(
                    chat_id=chat_id,
                    chat_type="group",
                    auto_leave_enabled=auto_leave_enabled,
                )
            )
        await session.commit()


async def _history_count(chat_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(CreditHistory).where(CreditHistory.chat_id == chat_id)
        )
        return int(result.scalar() or 0)


async def _run_old_update_charge(message) -> None:
    with (
        patch("app.handlers.credit_commands.user_repo.is_sudo_or_above", AsyncMock(return_value=True)),
        patch("app.handlers.credit_commands.user_repo.is_owner", AsyncMock(return_value=False)),
        patch("app.utils.bot_guards.is_developer", return_value=True),
    ):
        await credit_commands._handle_charge(AsyncMock(), message, is_video=False)


async def _runtime_playback_allowed(chat_id: int) -> bool:
    client = AsyncMock()
    client.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=SimpleNamespace(value="administrator"))
    )
    return await authorize_playback_action(
        client,
        _message("پخش", chat_id=chat_id, user_id=370045),
    )


@pytest.mark.asyncio
async def test_credit_warning_and_expire_command_use_same_active_group_source():
    chat_id = -1003700458073
    await _seed_group(chat_id, group_status="inactive", credit_days=1, settings=True)

    warnings = await CreditService.get_expiring_chats(hours=24, chat_type="group")
    assert all(row.chat_id != chat_id for row in warnings)

    result = await manager_command_service.expire_status(chat_id)
    assert result.ok is False
    assert result.reason == "not_managed"


@pytest.mark.asyncio
async def test_panel_expire_and_charge_share_same_group_install_state():
    chat_id = -1003700458074
    await _seed_group(chat_id, group_status="inactive", credit_days=1, settings=True)

    expire = _message("اعتبار پلیر", chat_id=chat_id)
    await _manager_handler()(AsyncMock(), expire)
    assert "فعال نیست" in expire.reply.await_args.args[0]

    panel_msg = _message("پنل", chat_id=chat_id)
    await group_panel._reply_group_panel(panel_msg)
    assert "فعال نیست" in panel_msg.reply.await_args.args[0]
    assert "پنل گروه" not in panel_msg.reply.await_args.args[0]

    before_history = await _history_count(chat_id)
    charge = _message("آپدیت شارژ 20", chat_id=chat_id)
    await _run_old_update_charge(charge)
    assert "فعال نیست" in charge.reply_text.await_args.args[0]
    assert await _history_count(chat_id) == before_history

    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days == 1


@pytest.mark.asyncio
async def test_old_update_charge_activates_or_reconciles_managed_group_state():
    chat_id = -1003700458075
    await _seed_group(chat_id, group_status="active", credit_days=1, settings=True)
    before_history = await _history_count(chat_id)

    charge = _message("آپدیت شارژ 20", chat_id=chat_id)
    await _run_old_update_charge(charge)

    assert "موفقیت" in charge.reply_text.await_args.args[0]
    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days == 21
    assert credit.status == "active"
    assert await _history_count(chat_id) == before_history + 1

    expire = _message("اعتبار پلیر", chat_id=chat_id)
    await _manager_handler()(AsyncMock(), expire)
    assert "21" in expire.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_new_charge_commands_update_runtime_credit_source():
    chat_id = -1003700458076
    await _seed_group(chat_id, group_status="active", credit_days=5, settings=True)

    charge = _message("شارژ موزیک 100+", chat_id=chat_id)
    await _manager_handler()(AsyncMock(), charge)

    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days == 105
    assert await _runtime_playback_allowed(chat_id) is True

    warnings = await CreditService.get_expiring_chats(hours=24, chat_type="group")
    assert all(row.chat_id != chat_id for row in warnings)


@pytest.mark.asyncio
async def test_inactive_group_with_credit_is_reported_consistently():
    chat_id = -1003700458077
    await _seed_group(chat_id, group_status="inactive", credit_days=2, settings=True)

    assert await manager_command_repo.get_active_group(chat_id) is None
    assert await _runtime_playback_allowed(chat_id) is False
    warnings = await CreditService.get_expiring_chats(hours=24, chat_type="group")
    assert all(row.chat_id != chat_id for row in warnings)


@pytest.mark.asyncio
async def test_no_fake_charge_success_when_group_is_not_managed_and_not_reconciled():
    chat_id = -1003700458078
    await _seed_group(chat_id, group_status=None, credit_days=2, settings=True)
    before_history = await _history_count(chat_id)

    old_charge = _message("آپدیت شارژ 20", chat_id=chat_id)
    await _run_old_update_charge(old_charge)
    assert "موفقیت" not in old_charge.reply_text.await_args.args[0]
    assert "فعال نیست" in old_charge.reply_text.await_args.args[0]

    new_charge = _message("ChargeMusic +20", chat_id=chat_id)
    await _manager_handler()(AsyncMock(), new_charge)
    assert "موفقیت" not in new_charge.reply.await_args.args[0]
    assert "فعال نیست" in new_charge.reply.await_args.args[0]
    assert await _history_count(chat_id) == before_history


@pytest.mark.asyncio
async def test_unlimited_group_is_interpreted_consistently():
    chat_id = -1003700458079
    await _seed_group(
        chat_id,
        group_status="active",
        credit_days=36500,
        credit_status="unlimited",
        settings=True,
    )

    warnings = await CreditService.get_expiring_chats(hours=24, chat_type="group")
    assert all(row.chat_id != chat_id for row in warnings)

    expire = _message("اعتبار موزیک", chat_id=chat_id)
    await _manager_handler()(AsyncMock(), expire)
    assert "نامحدود" in expire.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_active_group_with_missing_credit_is_visible_error():
    chat_id = -1003700458080
    await _seed_group(chat_id, group_status="active", credit_days=None, settings=True)

    result = await manager_command_service.expire_status(chat_id)
    assert result.ok is False
    assert result.reason == "credit_missing"

    expire = _message("MusicExpire", chat_id=chat_id)
    await _manager_handler()(AsyncMock(), expire)
    assert "اعتبار" in expire.reply.await_args.args[0]
    assert "مقداردهی" in expire.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_cache_invalidation_after_charge_and_uninstall():
    chat_id = -1003700458081
    await _seed_group(chat_id, group_status="active", credit_days=3, settings=True)

    await set_credit_cached(chat_id, 999)
    assert await get_credit_cached(chat_id) == 999
    await CreditService.charge_managed_chat(chat_id, "group", 2, operated_by=123456789)
    assert await get_credit_cached(chat_id) is None

    from app.repositories import settings_repo

    assert await settings_repo.get_chat_settings(chat_id) is not None
    assert await get_chat_settings_cached(chat_id) is not None
    await manager_command_repo.cleanup_group_management(chat_id)
    assert await get_chat_settings_cached(chat_id) is None
    warnings = await CreditService.get_expiring_chats(hours=24, chat_type="group")
    assert all(row.chat_id != chat_id for row in warnings)


@pytest.mark.asyncio
async def test_paid_charge_clears_trial_expiry_so_scheduler_does_not_expire():
    chat_id = -1003700458082
    expired_trial_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await _seed_group(
        chat_id,
        group_status="active",
        credit_days=0,
        credit_status="expired",
        settings=True,
        is_trial=True,
        trial_started_at=expired_trial_at - timedelta(days=1),
        trial_expire_at=expired_trial_at,
    )

    await CreditService.charge_managed_chat(chat_id, "group", 10, operated_by=123456789)

    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.credit_days == 10
    assert credit.status == "active"
    assert credit.is_trial is False
    assert credit.trial_started_at is None
    assert credit.trial_expire_at is None

    from app.scheduler import check_trial_expiry

    with patch("app.scheduler.NotificationService.notify_credit_expired", AsyncMock()) as notify:
        await check_trial_expiry()

    after = await credit_repo.get_credit(chat_id)
    assert after is not None
    assert after.credit_days == 10
    assert after.status == "active"
    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_pending_auto_leave_skips_currently_active_credit():
    chat_id = -1003700458083
    await _seed_group(
        chat_id,
        group_status="active",
        credit_days=10,
        credit_status="active",
        settings=True,
        auto_leave_enabled=True,
    )

    bot = AsyncMock()
    bot.send_message = AsyncMock()
    call_py = AsyncMock()
    with patch("app.services.call_service.CallService.leave_voice_chat", AsyncMock()) as leave:
        result = await CreditService.auto_leave_check(chat_id, "group", call_py=call_py, bot=bot)

    assert result is False
    leave.assert_not_awaited()
    bot.send_message.assert_not_awaited()

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, or_, select

from app.database.engine import async_session
from app.database.models import (
    BotSetting,
    CallSecuritySettings,
    ChatSettings,
    Group,
    GroupCredit,
    HelperAccount,
    HelperChatBinding,
    InstallLog,
    MusicAdmin,
    PlaybackState,
    PlayerDeputy,
    PlayerOwner,
    PlayerVip,
    Playlist,
    VideoAdmin,
)
from app.repositories import (
    admin_repo,
    call_stats_settings_repo,
    credit_repo,
    group_text_call_command_repo,
    id_command_settings_repo,
    manager_command_repo,
    settings_repo,
)


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


async def _count(model, chat_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(model).where(model.chat_id == chat_id)
        )
        return int(result.scalar() or 0)


@pytest.mark.asyncio
async def test_install_creates_runtime_readable_defaults_and_log():
    chat_id = -10067101
    msg = _message("افزودن موزیک", chat_id=chat_id)

    await _handler()(AsyncMock(), msg)

    assert await manager_command_repo.get_active_group(chat_id) is not None
    assert await settings_repo.get_chat_settings(chat_id) is not None
    credit = await credit_repo.get_credit(chat_id)
    assert credit is not None
    assert credit.chat_type == "group"
    assert credit.credit_days >= 0
    if credit.credit_days > 0:
        assert credit.is_trial is True
        assert credit.total_charged == 0
        assert credit.trial_started_at is not None
        assert credit.trial_expire_at is not None
        assert credit.trial_expire_at > credit.trial_started_at
    async with async_session() as session:
        log = (
            await session.execute(
                select(InstallLog).where(
                    InstallLog.chat_id == chat_id,
                    InstallLog.action == "install",
                )
            )
        ).scalar_one_or_none()
    assert log is not None

    expire = _message("اعتبار موزیک", chat_id=chat_id, user_id=671010)
    await _handler()(AsyncMock(), expire)
    assert "وضعیت اعتبار" in expire.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_install_idempotent_does_not_duplicate_runtime_rows():
    chat_id = -10067102

    await _install(chat_id)
    already = _message("AddMusic", chat_id=chat_id)
    await _handler()(AsyncMock(), already)

    assert await _count(Group, chat_id) == 1
    assert await _count(ChatSettings, chat_id) == 1
    assert await _count(GroupCredit, chat_id) == 1
    assert await _count(InstallLog, chat_id) == 1
    assert "راه‌اندازی پلیر" in already.reply.await_args.args[0]
    assert already.reply.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_install_repairs_missing_credit_for_active_group():
    chat_id = -10067109
    async with async_session() as session:
        async with session.begin():
            session.add(Group(chat_id=chat_id, chat_title="Partial Group", status="active"))

    message = _message("نصب پلیر", chat_id=chat_id)
    await _handler()(AsyncMock(), message)

    assert await _count(Group, chat_id) == 1
    assert await _count(ChatSettings, chat_id) == 1
    assert await _count(GroupCredit, chat_id) == 1
    credit = await credit_repo.get_credit(chat_id, "group")
    assert credit is not None
    assert credit.chat_type == "group"
    assert "راه‌اندازی پلیر" in message.reply.await_args.args[0]
    assert message.reply.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_install_default_creation_failure_is_visible_not_success():
    chat_id = -10067103
    msg = _message("AddMusic", chat_id=chat_id)

    with patch(
        "app.services.manager_command_service.repo.ensure_credit_row",
        AsyncMock(side_effect=RuntimeError("credit write failed")),
    ):
        await _handler()(AsyncMock(), msg)

    text = msg.reply.await_args.args[0]
    assert "عملیات انجام نشد" in text
    assert "نصب و فعال شد" not in text


@pytest.mark.asyncio
async def test_uninstall_cleans_runtime_state_and_blocks_managed_paths():
    chat_id = -10067104
    await _install(chat_id)
    async with async_session() as session:
        helper = HelperAccount(phone="+167104", status="active")
        session.add(helper)
        await session.flush()
        session.add_all(
            [
                MusicAdmin(chat_id=chat_id, user_id=71041),
                VideoAdmin(chat_id=chat_id, user_id=71042),
                PlayerDeputy(chat_id=chat_id, user_id=71045),
                PlayerOwner(chat_id=chat_id, user_id=71043),
                PlayerVip(chat_id=chat_id, user_id=71044),
                HelperChatBinding(chat_id=chat_id, helper_account_id=helper.id),
                PlaybackState(chat_id=chat_id, media_type="audio", source="x"),
                Playlist(chat_id=chat_id, position=1, title="queued", media_type="audio"),
                BotSetting(key=group_text_call_command_repo.setting_key(chat_id, "title"), value="old title"),
                BotSetting(
                    key=call_stats_settings_repo.setting_key(
                        chat_id,
                        call_stats_settings_repo.SETTING_ENABLED,
                    ),
                    value="0",
                ),
                BotSetting(
                    key=id_command_settings_repo.setting_key(
                        chat_id,
                        id_command_settings_repo.SETTING_OUTPUT_MODE,
                    ),
                    value="photo",
                ),
                BotSetting(
                    key=id_command_settings_repo.setting_key(
                        chat_id,
                        id_command_settings_repo.SETTING_SHOW_CALL_STATS,
                    ),
                    value="0",
                ),
                CallSecuritySettings(chat_id=chat_id, enabled=True),
            ]
        )
        await session.commit()
    assert await admin_repo.is_vip(71044, chat_id) is True

    client = AsyncMock()
    msg = _message("RemMusic", chat_id=chat_id)
    await _handler()(client, msg)

    group = await manager_command_repo.get_group_any(chat_id)
    assert group is not None
    assert group.status == "inactive"
    client.leave_chat.assert_not_awaited()
    for model in (
        ChatSettings,
        MusicAdmin,
        VideoAdmin,
        PlayerDeputy,
        PlayerOwner,
        PlayerVip,
        HelperChatBinding,
        PlaybackState,
        Playlist,
        CallSecuritySettings,
    ):
        assert await _count(model, chat_id) == 0
    async with async_session() as session:
        bot_settings = (
            await session.execute(
                select(BotSetting).where(
                    or_(
                        BotSetting.key.like(f"group_text_call:{chat_id}:%"),
                        BotSetting.key.like(f"call_stats:{chat_id}:%"),
                        BotSetting.key.like(f"id_command:{chat_id}:%"),
                    )
                )
            )
        ).scalars().all()
    assert bot_settings == []
    assert await admin_repo.is_vip(71044, chat_id) is False

    expire = _message("اعتبار موزیک", chat_id=chat_id)
    await _handler()(AsyncMock(), expire)
    assert "فعال نیست" in expire.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_uninstall_cleanup_failure_is_visible_not_success():
    chat_id = -10067105
    await _install(chat_id)
    msg = _message("حذف نصب موزیک", chat_id=chat_id)

    with patch(
        "app.services.manager_command_service.repo.cleanup_group_management",
        AsyncMock(side_effect=RuntimeError("cleanup failed")),
    ):
        await _handler()(AsyncMock(), msg)

    text = msg.reply.await_args.args[0]
    assert "عملیات انجام نشد" in text
    assert "حذف شد" not in text


@pytest.mark.asyncio
async def test_leave_cleans_db_calls_main_leave_and_reports_helper_partial():
    chat_id = -10067106
    await _install(chat_id)
    async with async_session() as session:
        helper = HelperAccount(phone="+167106", status="active")
        session.add(helper)
        await session.flush()
        session.add(HelperChatBinding(chat_id=chat_id, helper_account_id=helper.id))
        await session.commit()
        helper_id = helper.id

    client = AsyncMock()
    msg = _message("LeaveMusic", chat_id=chat_id)
    with patch(
        "app.services.helper_pool_service.HelperPoolService.leave_chat_as_helper",
        AsyncMock(return_value=False),
    ) as helper_leave:
        await _handler()(client, msg)

    group = await manager_command_repo.get_group_any(chat_id)
    assert group is not None
    assert group.status == "inactive"
    client.leave_chat.assert_awaited_once_with(chat_id)
    helper_leave.assert_awaited_once_with(helper_id, chat_id)
    assert "خروج کمکی کامل نشد" in msg.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_leave_main_bot_failure_is_visible_but_cleanup_persists():
    chat_id = -10067107
    await _install(chat_id)
    client = AsyncMock()
    client.leave_chat = AsyncMock(side_effect=RuntimeError("telegram failed"))
    msg = _message("خروج موزیک", chat_id=chat_id)

    await _handler()(client, msg)

    group = await manager_command_repo.get_group_any(chat_id)
    assert group is not None
    assert group.status == "inactive"
    assert "خروج ربات اصلی" in msg.reply.await_args.args[0]
    assert "ناموفق" in msg.reply.await_args.args[0]

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import (
    BotSetting,
    CallReport,
    Group,
    GroupMemberMembership,
    PlaybackState,
    User,
)
from app.repositories import group_text_call_command_repo as repo


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


def _handler(call_py=None):
    from app.handlers import group_text_call_commands

    bot = _RecorderBot()
    group_text_call_commands.register(bot, call_py)
    return bot.message_handlers[0]


def _message(text: str, *, chat_id: int, user_id: int = 123456789):
    from unittest.mock import AsyncMock

    msg = SimpleNamespace()
    msg.chat = SimpleNamespace(id=chat_id, type=SimpleNamespace(value="supergroup"))
    msg.from_user = SimpleNamespace(id=user_id, username="dev", first_name="Dev")
    msg.text = text
    msg.caption = None
    msg.entities = []
    msg.reply_to_message = None
    msg.reply = AsyncMock()
    msg.reply_text = AsyncMock()
    msg.continue_propagation = lambda: None
    return msg


async def _read_bot_setting(key: str) -> str | None:
    async with async_session() as session:
        result = await session.execute(select(BotSetting.value).where(BotSetting.key == key))
        return result.scalar_one_or_none()


async def _seed_active_group(chat_id: int) -> None:
    async with async_session() as session:
        group = await session.scalar(select(Group).where(Group.chat_id == chat_id))
        if group is None:
            session.add(Group(chat_id=chat_id, chat_title="Call Test Group", status="active"))
        else:
            group.status = "active"
        await session.commit()


async def _seed_playback(chat_id: int) -> None:
    await _seed_active_group(chat_id)
    async with async_session() as session:
        session.add(
            PlaybackState(
                chat_id=chat_id,
                media_type="audio",
                source="test-source",
                title="Test Track",
                is_paused=False,
            )
        )
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,setting_name,expected",
    [
        ("آمار خودکار کال فعال", repo.SETTING_AUTO_STATS, "1"),
        ("آمار خودکار کال غیرفعال", repo.SETTING_AUTO_STATS, "0"),
        ("سکوت کال فعال", repo.SETTING_CALL_MUTE, "1"),
        ("سکوت کال غیرفعال", repo.SETTING_CALL_MUTE, "0"),
        ("کامنت کال فعال", repo.SETTING_CALL_COMMENT, "1"),
        ("کامنت کال غیرفعال", repo.SETTING_CALL_COMMENT, "0"),
    ],
)
async def test_toggle_commands_persist_real_bot_setting(text: str, setting_name: str, expected: str):
    chat_id = -10077000 - abs(hash(text)) % 10000
    await _seed_active_group(chat_id)
    handler = _handler()
    message = _message(text, chat_id=chat_id)

    await handler(SimpleNamespace(), message)

    key = repo.setting_key(chat_id, setting_name)
    assert await _read_bot_setting(key) == expected
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_end_call_persists_scheduled_end_config_after_handler_success():
    chat_id = -10088001
    await _seed_playback(chat_id)
    handler = _handler()
    message = _message("پایان کال 25", chat_id=chat_id)

    resolution = SimpleNamespace(ok=True, status="ok")
    with (
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.resolve_group_call_helper",
            AsyncMock(return_value=resolution),
        ),
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.close_group_call_helper",
            AsyncMock(),
        ),
    ):
        await handler(SimpleNamespace(), message)

    raw = await _read_bot_setting(repo.setting_key(chat_id, repo.SETTING_SCHEDULED_END))
    assert raw is not None
    data = json.loads(raw)
    assert data["chat_id"] == chat_id
    assert data["minutes"] == 25
    assert data["requested_by"] == 123456789
    assert datetime.fromisoformat(data["end_at"]).tzinfo is not None
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_title_persists_title_after_handler_success():
    chat_id = -10088002
    await _seed_playback(chat_id)
    handler = _handler()
    message = _message("عنوان کال تست", chat_id=chat_id)

    live_result = SimpleNamespace(ok=True, reason="title_applied")
    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.try_edit_group_call_title",
        AsyncMock(return_value=live_result),
    ):
        await handler(SimpleNamespace(), message)

    assert await _read_bot_setting(repo.setting_key(chat_id, repo.SETTING_TITLE)) == "تست"
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_english_set_title_persists_title_after_handler_success():
    chat_id = -10088003
    await _seed_playback(chat_id)
    handler = _handler()
    message = _message("SetTitle Cold Night", chat_id=chat_id)

    live_result = SimpleNamespace(ok=True, reason="title_applied")
    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.try_edit_group_call_title",
        AsyncMock(return_value=live_result),
    ):
        await handler(SimpleNamespace(), message)

    assert await _read_bot_setting(repo.setting_key(chat_id, repo.SETTING_TITLE)) == "Cold Night"
    message.reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_title_without_active_call_does_not_persist_fake_success():
    chat_id = -10088006
    await _seed_active_group(chat_id)
    handler = _handler()
    message = _message("عنوان کال بدون کال", chat_id=chat_id)

    await handler(SimpleNamespace(), message)

    assert await _read_bot_setting(repo.setting_key(chat_id, repo.SETTING_TITLE)) is None
    message.reply.assert_awaited_once()
    assert "کال فعالی" in message.reply.await_args.args[0]


@pytest.mark.asyncio
async def test_call_stats_opens_selection_panel():
    chat_id = -10088004
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        session.add_all(
            [
                Group(chat_id=chat_id, chat_title="Stats Group", status="active"),
                User(user_id=88101, username="alice", first_name="Alice"),
                User(user_id=88102, username="bob", first_name="Bob"),
                CallReport(
                    chat_id=chat_id,
                    chat_title="Stats Group",
                    title="A",
                    media_type="audio",
                    played_by=88101,
                    duration_seconds=75,
                    started_at=now,
                ),
                CallReport(
                    chat_id=chat_id,
                    chat_title="Stats Group",
                    title="B",
                    media_type="audio",
                    played_by=88102,
                    duration_seconds=30,
                    started_at=now,
                ),
            ]
        )
        await session.commit()

    handler = _handler()
    message = _message("آمار کال", chat_id=chat_id)
    with (
        patch(
            "app.handlers.group_text_call_commands._can_manage_call_command",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.handlers.group_text_call_commands.call_stats_settings_repo.get_enabled",
            AsyncMock(return_value=True),
        ),
    ):
        await handler(SimpleNamespace(), message)

    message.reply.assert_awaited_once()
    assert "بازه" in message.reply.await_args.args[0] or "انتخاب" in message.reply.await_args.args[0]
    assert message.reply.await_args.kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_get_panel_uses_persisted_playback_state_real_handler_routing():
    chat_id = -10088005
    await _seed_playback(chat_id)
    handler = _handler()
    message = _message("دریافت پنل", chat_id=chat_id)

    await handler(SimpleNamespace(), message)

    message.reply.assert_awaited_once()
    rendered = message.reply.await_args.args[0]
    assert "Test Track" in rendered or "در حال پخش" in rendered


@pytest.mark.asyncio
async def test_get_recent_user_ids_returns_current_chat_members_only_ordered():
    chat_id = -10088007
    other_chat_id = -10088008
    now = datetime.now(timezone.utc)
    earlier = now - timedelta(seconds=1)
    async with async_session() as session:
        session.add_all(
            [
                GroupMemberMembership(
                    chat_id=chat_id,
                    user_id=88201,
                    first_seen_at=now,
                    last_seen_at=now,
                    joined_at=now,
                ),
                GroupMemberMembership(
                    chat_id=chat_id,
                    user_id=88202,
                    first_seen_at=now,
                    last_seen_at=earlier,
                    joined_at=now,
                ),
                GroupMemberMembership(
                    chat_id=chat_id,
                    user_id=88203,
                    first_seen_at=now,
                    last_seen_at=now,
                    joined_at=now,
                    left_at=now,
                ),
                GroupMemberMembership(
                    chat_id=other_chat_id,
                    user_id=88204,
                    first_seen_at=now,
                    last_seen_at=now,
                    joined_at=now,
                ),
            ]
        )
        await session.commit()

    assert await repo.get_recent_user_ids(chat_id, limit=10) == [88201, 88202]


@pytest.mark.asyncio
async def test_get_recent_user_ids_does_not_fallback_to_global_users_without_memberships():
    chat_id = -10088009
    other_chat_id = -10088010
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        session.add_all(
            [
                User(user_id=88211, username="global", first_name="Global"),
                User(user_id=88212, username="other", first_name="Other"),
                GroupMemberMembership(
                    chat_id=other_chat_id,
                    user_id=88212,
                    first_seen_at=now,
                    last_seen_at=now,
                    joined_at=now,
                ),
            ]
        )
        await session.commit()

    assert await repo.get_recent_user_ids(chat_id, limit=10) == []

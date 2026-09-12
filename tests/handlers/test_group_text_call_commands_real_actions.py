from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import BotSetting, CallReport, PlaybackState, User
from app.repositories import group_text_call_command_repo as repo
from app.services import group_call_moderation_service as raw_svc
from app.services import group_text_call_command_service as cmd_svc


class _RawClient:
    def __init__(self, *, call=None, export_link="https://t.me/+realcall"):
        from pyrogram import raw

        self.raw = raw
        self.call = call if call is not None else raw.types.InputGroupCall(id=11, access_hash=22)
        self.export_link = export_link
        self.requests = []

    async def resolve_peer(self, peer_id):
        if int(peer_id) == 777:
            return self.raw.types.InputPeerUser(user_id=777, access_hash=888)
        return self.raw.types.InputPeerChannel(channel_id=123, access_hash=456)

    async def invoke(self, request):
        self.requests.append(request)
        name = type(request).__name__
        if name == "GetFullChannel":
            return SimpleNamespace(full_chat=SimpleNamespace(call=self.call))
        if name == "ExportGroupCallInvite":
            return SimpleNamespace(link=self.export_link)
        return SimpleNamespace(ok=True)


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs = []

    def add_job(self, func, trigger, **kwargs):  # noqa: ANN001
        self.jobs.append((func, trigger, kwargs))


async def _seed_playback(chat_id: int) -> None:
    async with async_session() as session:
        session.add(
            PlaybackState(
                chat_id=chat_id,
                media_type="audio",
                source="test-source",
                title="Active",
                is_paused=False,
            )
        )
        await session.commit()


async def _read_setting(chat_id: int, name: str) -> str | None:
    async with async_session() as session:
        result = await session.execute(
            select(BotSetting.value).where(BotSetting.key == repo.setting_key(chat_id, name))
        )
        return result.scalar_one_or_none()


def test_installed_kurigram_raw_group_call_capabilities_are_present():
    assert raw_svc.raw_create_group_call_available() is True
    assert raw_svc.raw_discard_group_call_available() is True
    assert raw_svc.raw_invite_group_call_available() is True
    assert raw_svc.raw_edit_group_call_title_available() is True
    assert raw_svc.raw_export_group_call_invite_available() is True
    assert raw_svc.raw_toggle_group_call_settings_available() is True


@pytest.mark.asyncio
async def test_raw_create_group_call_invokes_create_group_call_with_title():
    client = _RawClient()

    result = await raw_svc.create_group_call(client, -100123, title="Cold Night")

    assert result.ok is True
    request = client.requests[-1]
    assert type(request).__name__ == "CreateGroupCall"
    assert request.title == "Cold Night"


@pytest.mark.asyncio
async def test_raw_discard_title_invite_link_and_settings_use_phone_functions():
    client = _RawClient()

    discard = await raw_svc.discard_group_call(client, -100123)
    assert discard.ok is True
    assert type(client.requests[-1]).__name__ == "DiscardGroupCall"

    title = await raw_svc.edit_group_call_title(client, -100123, "شب سرد")
    assert title.ok is True
    assert type(client.requests[-1]).__name__ == "EditGroupCallTitle"
    assert client.requests[-1].title == "شب سرد"

    invite = await raw_svc.invite_to_group_call(client, -100123, [777])
    assert invite.ok is True
    assert type(client.requests[-1]).__name__ == "InviteToGroupCall"
    assert type(client.requests[-1].users[0]).__name__ == "InputUser"

    link_result, link = await raw_svc.export_group_call_invite_link(client, -100123)
    assert link_result.ok is True
    assert link == "https://t.me/+realcall"
    assert type(client.requests[-1]).__name__ == "ExportGroupCallInvite"

    settings = await raw_svc.toggle_group_call_settings(
        client,
        -100123,
        join_muted=True,
        messages_enabled=False,
    )
    assert settings.ok is True
    assert type(client.requests[-1]).__name__ == "ToggleGroupCallSettings"
    assert client.requests[-1].join_muted is True
    assert client.requests[-1].messages_enabled is False


@pytest.mark.asyncio
async def test_schedule_end_persists_and_adds_scheduler_job():
    chat_id = -10099001
    await _seed_playback(chat_id)
    scheduler = _FakeScheduler()

    resolution = SimpleNamespace(ok=True, status="ok")
    with (
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.resolve_group_call_helper",
            AsyncMock(return_value=resolution),
        ) as resolver,
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.close_group_call_helper",
            AsyncMock(),
        ) as close_helper,
    ):
        result = await cmd_svc.schedule_call_end(
            None,
            chat_id,
            25,
            requested_by=123456789,
            scheduler_obj=scheduler,
        )

    assert result.ok is True
    resolver.assert_awaited_once_with(
        chat_id,
        require_admin=True,
        reason="group_text_command_schedule_end",
    )
    close_helper.assert_awaited_once_with(resolution)
    assert await repo.get_scheduled_end(chat_id) is not None
    assert len(scheduler.jobs) == 1
    func, trigger, kwargs = scheduler.jobs[0]
    assert func is cmd_svc._run_scheduled_call_end
    assert trigger == "date"
    assert kwargs["id"] == f"group_text_call_end_{chat_id}"


@pytest.mark.asyncio
async def test_scheduled_end_execution_discards_group_call_and_clears_config():
    chat_id = -10099002
    await repo.set_scheduled_end(
        chat_id,
        1,
        datetime.now(timezone.utc) + timedelta(minutes=1),
        requested_by=1,
    )
    result = SimpleNamespace(ok=True, reason="discarded")

    with (
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.try_discard_group_call",
            AsyncMock(return_value=result),
        ) as discard,
        patch(
            "app.services.group_text_call_command_service.CallService.leave_voice_chat",
            AsyncMock(return_value=True),
        ) as leave,
    ):
        await cmd_svc._run_scheduled_call_end(None, chat_id)

    discard.assert_awaited_once_with(chat_id, reason="scheduled_group_text_command")
    leave.assert_awaited_once_with(None, chat_id)
    assert await repo.get_scheduled_end(chat_id) is None


@pytest.mark.asyncio
async def test_restore_pending_scheduled_end_jobs_adds_runtime_jobs():
    chat_id = -10099003
    scheduler = _FakeScheduler()
    await repo.set_scheduled_end(
        chat_id,
        10,
        datetime.now(timezone.utc) + timedelta(minutes=10),
        requested_by=1,
    )

    restored = await cmd_svc.restore_scheduled_call_end_jobs(
        None,
        scheduler_obj=scheduler,
    )

    assert restored >= 1
    assert any(job[2]["id"] == f"group_text_call_end_{chat_id}" for job in scheduler.jobs)


@pytest.mark.asyncio
async def test_start_call_uses_raw_create_and_reads_future_settings():
    chat_id = -10099004
    await repo.set_call_mute_enabled(chat_id, True, updated_by=1)
    await repo.set_call_comment_enabled(chat_id, True, updated_by=1)
    await repo.set_call_title(chat_id, "Future Title", updated_by=1)
    create_result = SimpleNamespace(ok=True, reason="started")
    settings_result = SimpleNamespace(ok=True, reason="settings_applied")

    with (
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.try_create_group_call",
            AsyncMock(return_value=create_result),
        ) as create,
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.try_toggle_group_call_settings",
            AsyncMock(return_value=settings_result),
        ) as toggle,
    ):
        result = await cmd_svc.start_call(SimpleNamespace(), None, chat_id)

    assert result.ok is True
    create.assert_awaited_once_with(chat_id, title="Future Title", reason="group_text_command")
    toggle.assert_awaited_once_with(
        chat_id,
        join_muted=True,
        messages_enabled=True,
        reason="group_text_command_start",
    )


@pytest.mark.asyncio
async def test_start_call_returns_unsupported_without_raw_create_api():
    with (
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.raw_create_group_call_available",
            return_value=False,
        ),
        patch(
            "app.services.group_text_call_command_service.group_call_moderation_service.try_create_group_call",
            AsyncMock(),
        ) as create,
    ):
        result = await cmd_svc.start_call(SimpleNamespace(), None, -10099005)

    assert result.ok is False
    assert result.reason == "raw_api_unavailable"
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_call_maps_raw_already_started_to_visible_already_active():
    raw_result = SimpleNamespace(ok=False, reason="already_active")

    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.try_create_group_call",
        AsyncMock(return_value=raw_result),
    ):
        result = await cmd_svc.start_call(SimpleNamespace(), None, -10099015)

    assert result.ok is True
    assert result.reason == "already_active"


@pytest.mark.asyncio
async def test_invite_command_path_calls_raw_invite_service():
    chat_id = -10099006
    await _seed_playback(chat_id)
    invite_result = SimpleNamespace(ok=True, reason="invited")

    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.try_invite_to_group_call",
        AsyncMock(return_value=invite_result),
    ) as invite:
        result = await cmd_svc.invite_user(SimpleNamespace(), None, chat_id, 777)

    assert result.ok is True
    assert result.count == 1
    invite.assert_awaited_once_with(chat_id, [777], reason="group_text_command")


@pytest.mark.asyncio
async def test_set_title_requires_active_call_and_live_api_before_persisting():
    chat_id = -10099007
    no_active = await cmd_svc.set_call_title(None, chat_id, "Fake", updated_by=1)
    assert no_active.ok is False
    assert no_active.reason == "no_active_call"
    assert await _read_setting(chat_id, repo.SETTING_TITLE) is None

    await _seed_playback(chat_id)
    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.raw_edit_group_call_title_available",
        return_value=False,
    ):
        unsupported = await cmd_svc.set_call_title(None, chat_id, "Fake", updated_by=1)
    assert unsupported.ok is False
    assert unsupported.reason == "raw_api_unavailable"
    assert await _read_setting(chat_id, repo.SETTING_TITLE) is None

    live_result = SimpleNamespace(ok=True, reason="title_applied")
    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.try_edit_group_call_title",
        AsyncMock(return_value=live_result),
    ) as title_api:
        applied = await cmd_svc.set_call_title(None, chat_id, "Real Title", updated_by=1)

    assert applied.ok is True
    assert applied.applied_live is True
    title_api.assert_awaited_once_with(chat_id, "Real Title", reason="group_text_command")
    assert await _read_setting(chat_id, repo.SETTING_TITLE) == "Real Title"


@pytest.mark.asyncio
async def test_get_call_link_uses_exported_group_call_invite_not_synthetic_url():
    chat_id = -10099008
    await _seed_playback(chat_id)
    raw_result = SimpleNamespace(ok=True, reason="ok")

    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.try_export_group_call_invite_link",
        AsyncMock(return_value=(raw_result, "https://t.me/+actualcall")),
    ) as export:
        result = await cmd_svc.get_call_link(SimpleNamespace(), None, SimpleNamespace(id=chat_id))

    assert result.ok is True
    assert result.link == "https://t.me/+actualcall"
    export.assert_awaited_once_with(chat_id, reason="group_text_command")

    missing = await cmd_svc.get_call_link(SimpleNamespace(), None, SimpleNamespace(id=-10099009))
    assert missing.ok is False
    assert missing.reason == "no_active_call"


@pytest.mark.asyncio
async def test_call_mute_and_comment_try_live_toggle_when_call_is_active():
    chat_id = -10099010
    await _seed_playback(chat_id)
    live_result = SimpleNamespace(ok=True, reason="settings_applied")

    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.try_toggle_group_call_settings",
        AsyncMock(return_value=live_result),
    ) as toggle:
        mute = await cmd_svc.set_call_mute(chat_id, True, updated_by=1)
    assert mute.ok is True
    assert mute.persisted is True
    assert mute.applied_live is True
    toggle.assert_awaited_once_with(chat_id, join_muted=True, reason="group_text_command")
    assert await repo.is_call_mute_enabled(chat_id) is True

    with patch(
        "app.services.group_text_call_command_service.group_call_moderation_service.try_toggle_group_call_settings",
        AsyncMock(return_value=live_result),
    ) as toggle_comment:
        comment = await cmd_svc.set_call_comment(chat_id, False, updated_by=1)
    assert comment.ok is True
    assert comment.persisted is True
    assert comment.applied_live is True
    toggle_comment.assert_awaited_once_with(
        chat_id,
        messages_enabled=False,
        reason="group_text_command",
    )
    assert await repo.is_call_comment_enabled(chat_id) is False


@pytest.mark.asyncio
async def test_future_call_settings_reader_uses_persisted_bot_settings():
    chat_id = -10099011
    await repo.set_auto_stats_enabled(chat_id, True, updated_by=1)
    await repo.set_call_mute_enabled(chat_id, True, updated_by=1)
    await repo.set_call_comment_enabled(chat_id, False, updated_by=1)
    await repo.set_call_title(chat_id, "Stored Title", updated_by=1)

    settings = await cmd_svc.get_future_call_settings(chat_id)

    assert settings.auto_stats_enabled is True
    assert settings.call_mute_enabled is True
    assert settings.call_comment_enabled is False
    assert settings.title == "Stored Title"


@pytest.mark.asyncio
async def test_auto_call_stats_report_reads_enabled_key_and_sends_seeded_stats():
    enabled_chat = -10099012
    disabled_chat = -10099013
    now = datetime.now(timezone.utc)
    await repo.set_auto_stats_enabled(enabled_chat, True, updated_by=1)
    await repo.set_auto_stats_enabled(disabled_chat, False, updated_by=1)
    async with async_session() as session:
        session.add_all(
            [
                User(user_id=990121, username="statuser", first_name="Stat User"),
                CallReport(
                    chat_id=enabled_chat,
                    chat_title="Enabled",
                    title="Track",
                    media_type="audio",
                    played_by=990121,
                    duration_seconds=125,
                    started_at=now,
                ),
                CallReport(
                    chat_id=disabled_chat,
                    chat_title="Disabled",
                    title="Track",
                    media_type="audio",
                    played_by=990121,
                    duration_seconds=125,
                    started_at=now,
                ),
            ]
        )
        await session.commit()
    bot = SimpleNamespace(send_message=AsyncMock())

    sent = await cmd_svc.run_auto_call_stats_report(bot)

    assert sent == 1
    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.args[0] == enabled_chat
    assert "Stat User" in bot.send_message.await_args.args[1]


@pytest.mark.asyncio
async def test_weekly_auto_call_stats_uses_seven_day_window():
    chat_id = -10099014
    now = datetime.now(timezone.utc)
    await repo.set_auto_stats_enabled(chat_id, True, updated_by=1)
    async with async_session() as session:
        session.add_all(
            [
                User(user_id=990141, username="recent", first_name="Recent User"),
                User(user_id=990142, username="old", first_name="Old User"),
                CallReport(
                    chat_id=chat_id,
                    chat_title="Weekly",
                    title="Recent",
                    media_type="audio",
                    played_by=990141,
                    duration_seconds=90,
                    started_at=now - timedelta(days=6),
                ),
                CallReport(
                    chat_id=chat_id,
                    chat_title="Weekly",
                    title="Old",
                    media_type="audio",
                    played_by=990142,
                    duration_seconds=900,
                    started_at=now - timedelta(days=8),
                ),
            ]
        )
        await session.commit()
    bot = SimpleNamespace(send_message=AsyncMock())

    result = await cmd_svc.send_auto_call_stats(bot, chat_id, days=7)

    assert result.ok is True
    body = bot.send_message.await_args.args[1]
    assert "Recent User" in body
    assert "Old User" not in body

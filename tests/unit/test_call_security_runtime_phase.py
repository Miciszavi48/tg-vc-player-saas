"""Call Security runtime: detectors, mute probe, summary file, Redis helpers."""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def test_probe_mute_support_false_without_apis():
    from app.services import call_security_service

    with patch(
        "app.services.call_security_service.group_call_moderation_service.raw_mute_api_available",
        return_value=False,
    ):
        assert call_security_service.probe_mute_support(None) is False


def test_probe_mute_support_does_not_count_unmute_as_mute_api():
    from app.services import call_security_service

    call_py = SimpleNamespace(unmute=AsyncMock())
    assert call_security_service.probe_mute_support(call_py) is False


def test_detect_multiple_join_without_leave():
    from app.services.call_security_service import detect_suspicious_events

    participant = {
        "just_joined": True,
        "left": False,
        "timestamp": 200.0,
        "muted": True,
        "mic_known": True,
    }
    prior = {"join_count": 2, "left_since_last_join": False, "timestamp": 100.0}
    events = detect_suspicious_events(
        1,
        55,
        participant,
        prior,
        privileged=False,
        mute_incoming=True,
    )
    reasons = {e.reason for e in events}
    assert "multiple_join" in reasons


def test_first_join_does_not_trigger_multiple_join():
    from app.services.call_security_service import detect_suspicious_events

    participant = {
        "just_joined": True,
        "left": False,
        "timestamp": 100.0,
        "muted": True,
        "mic_known": True,
    }
    events = detect_suspicious_events(
        1,
        55,
        participant,
        None,
        privileged=False,
        mute_incoming=True,
    )
    assert all(e.reason != "multiple_join" for e in events)


def test_rejoin_after_leave_does_not_trigger_multiple_join():
    from app.services.call_security_service import detect_suspicious_events

    participant = {
        "just_joined": True,
        "left": False,
        "timestamp": 200.0,
        "muted": True,
        "mic_known": True,
    }
    prior = {"join_count": 1, "left_since_last_join": True, "timestamp": 100.0}
    events = detect_suspicious_events(
        1,
        55,
        participant,
        prior,
        privileged=False,
        mute_incoming=True,
    )
    assert all(e.reason != "multiple_join" for e in events)


def test_multiple_video_join_uses_prior_state_when_payload_has_video_active_only():
    from app.services.call_security_service import detect_suspicious_events

    participant = {
        "just_joined": False,
        "left": False,
        "timestamp": 200.0,
        "video_active": True,
    }
    events = detect_suspicious_events(
        1,
        55,
        participant,
        {"video_join_count": 1, "timestamp": 100.0},
        privileged=False,
        mute_incoming=False,
    )
    assert any(e.reason == "multiple_video_joins" for e in events)


def test_detect_mic_active_on_entry():
    from app.services.call_security_service import detect_suspicious_events

    participant = {
        "just_joined": True,
        "left": False,
        "muted": False,
        "mic_known": True,
        "timestamp": 10.0,
    }
    events = detect_suspicious_events(
        1,
        77,
        participant,
        {"join_count": 1, "left_since_last_join": True},
        privileged=False,
        mute_incoming=True,
    )
    assert any(e.reason == "mic_active_on_entry" for e in events)


def test_write_summary_tempfile_content():
    from app.services.call_security_service import SuspiciousEvent, write_summary_tempfile

    events = [
        SuspiciousEvent(reason="multiple_join", user_id=42, detail=None),
    ]
    path, filename = write_summary_tempfile(
        1,
        "Test Chat",
        events,
        started_at="2026-06-09T00:00:00+00:00",
        ended_at="2026-06-09T01:00:00+00:00",
    )
    try:
        text = path.read_text(encoding="utf-8")
        assert "chat_id=1" in text
        assert "multiple_join" in text
        assert filename.endswith(".txt")
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_try_mute_participant_returns_false_when_unsupported():
    from app.services import call_security_service

    with patch(
        "app.services.call_security_service.group_call_moderation_service.try_mute_participant",
        AsyncMock(
            return_value=SimpleNamespace(ok=False, supported=False, reason="raw_api_unavailable")
        ),
    ):
        result = await call_security_service.try_mute_participant(None, 1, 2)
    assert result.ok is False
    assert result.supported is False


def test_peer_to_chat_id_channel():
    from app.handlers.call_security_runtime import _peer_to_chat_id

    class PeerChannel:
        channel_id = 12345

    assert _peer_to_chat_id(PeerChannel()) == -10012345


@pytest.mark.asyncio
async def test_raw_observer_keeps_call_security_dispatch_in_dedicated_group():
    from app.handlers import call_security_runtime as csr
    from app.handlers.priority import CALL_SECURITY_RAW_GROUP

    class RecorderBot:
        def on_raw_update(self, filters=None, group=0):  # noqa: ANN001
            def decorator(fn):
                self.callback = fn
                self.group = group
                return fn

            return decorator

    class GroupCallUpdate:
        pass

    class ParticipantUpdate:
        pass

    bot = RecorderBot()
    call_py = object()
    csr.register(bot, call_py)

    assert bot.group == CALL_SECURITY_RAW_GROUP
    assert bot.group != 0

    group_update = GroupCallUpdate()
    participant_update = ParticipantUpdate()
    with (
        patch.object(csr.raw.types, "UpdateGroupCall", GroupCallUpdate),
        patch.object(
            csr.raw.types,
            "UpdateGroupCallParticipants",
            ParticipantUpdate,
        ),
        patch.object(csr, "_handle_group_call_update", AsyncMock()) as group_handler,
        patch.object(
            csr,
            "_handle_participants_update",
            AsyncMock(),
        ) as participant_handler,
    ):
        await bot.callback(bot, group_update, {}, {})
        await bot.callback(bot, participant_update, {}, {})

    group_handler.assert_awaited_once_with(bot, call_py, group_update)
    participant_handler.assert_awaited_once_with(bot, call_py, participant_update)


@pytest.mark.asyncio
async def test_register_and_resolve_call_mapping():
    from app.handlers import call_security_runtime as csr

    fake_redis = AsyncMock()
    fake_redis.set = AsyncMock(return_value=True)
    with patch("app.handlers.call_security_runtime.get_redis", AsyncMock(return_value=fake_redis)):
        await csr.register_call_mapping(-1001, 555)
        fake_redis.get = AsyncMock(return_value="-1001")
        chat_id = await csr.resolve_chat_id_for_call(555)
    assert chat_id == -1001


@pytest.mark.asyncio
async def test_auto_unmute_decision_uses_membership_age():
    from app.services import call_security_service

    settings = SimpleNamespace(membership_age_days=7)
    with patch(
        "app.services.call_security_service.group_membership_age_service.is_membership_old_enough",
        AsyncMock(return_value=True),
    ) as old_enough:
        result = await call_security_service.should_auto_unmute(
            42,
            -100123,
            settings,
            privileged=False,
        )

    assert result is True
    old_enough.assert_awaited_once_with(-100123, 42, 7)


@pytest.mark.asyncio
async def test_auto_unmute_unknown_membership_age_fails_closed():
    from app.services import call_security_service

    settings = SimpleNamespace(membership_age_days=7)
    with patch(
        "app.services.call_security_service.group_membership_age_service.is_membership_old_enough",
        AsyncMock(return_value=False),
    ):
        result = await call_security_service.should_auto_unmute(
            42,
            -100123,
            settings,
            privileged=False,
        )

    assert result is False


@pytest.mark.asyncio
async def test_runtime_records_call_event_first_seen_membership():
    from app.handlers import call_security_runtime as csr

    participant = SimpleNamespace(
        peer=SimpleNamespace(user_id=1234),
        muted=True,
        left=False,
        just_joined=True,
        video=None,
    )
    settings = SimpleNamespace(
        enabled=True,
        mute_incoming_enabled=False,
        report_enabled=False,
        summary_enabled=False,
        owner_access_enabled=False,
        membership_age_days=7,
    )
    with (
        patch(
            "app.handlers.call_security_runtime.call_security_repo.get_call_security_settings",
            AsyncMock(return_value=settings),
        ),
        patch(
            "app.handlers.call_security_runtime.group_membership_age_service.record_member_seen",
            AsyncMock(),
        ) as seen_mock,
        patch(
            "app.handlers.call_security_runtime.call_security_service.is_privileged_member",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.handlers.call_security_runtime._load_user_state",
            AsyncMock(return_value={}),
        ),
        patch(
            "app.handlers.call_security_runtime._save_user_state",
            AsyncMock(),
        ),
    ):
        await csr._process_participant(SimpleNamespace(), None, -100123, participant)

    seen_mock.assert_awaited_once()
    assert seen_mock.await_args.args[:2] == (-100123, 1234)
    assert seen_mock.await_args.kwargs["source"] == "call_event"

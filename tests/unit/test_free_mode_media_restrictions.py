"""Free-mode media restrictions for free-install chats."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("TEST_MODE", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

from app.database.models import GroupCredit, InstallPolicySetting
from app.repositories.group_runtime_repo import RuntimeChatState
from app.services.media_capability_service import (
    MediaCapabilities,
    denial_message_key,
    get_chat_media_capabilities,
    is_chat_free_media_restricted,
    is_media_feature_allowed,
)
from app.utils.ui import CB, KeyboardFactory


def _menu_callbacks(kb) -> set[str]:
    """Return callback_data values from an inline keyboard."""
    return {
        btn.callback_data
        for row in kb.inline_keyboard
        for btn in row
        if btn.callback_data
    }


_FULL_CAPS = MediaCapabilities(is_restricted=False)
_FREE_CAPS = MediaCapabilities(
    is_restricted=True,
    audio=True,
    download=True,
    video=False,
    tv=False,
    radio=True,
    satellite=False,
)


def _credit(
    *,
    chat_id: int = -1001,
    chat_type: str = "group",
    credit_days: int = 5,
    total_charged: int = 0,
    status: str = "active",
) -> GroupCredit:
    return GroupCredit(
        chat_id=chat_id,
        chat_type=chat_type,
        credit_days=credit_days,
        total_charged=total_charged,
        is_trial=True,
        status=status,
    )


def _runtime_state(credit: GroupCredit | None) -> RuntimeChatState:
    chat_id = int(getattr(credit, "chat_id", -1001) or -1001)
    chat_type = str(getattr(credit, "chat_type", "group") or "group")
    return RuntimeChatState(
        chat_id=chat_id,
        chat_type=chat_type,
        installed=SimpleNamespace(status="active"),
        credit=credit,
        settings=None,
    )


def _policy(mode: str) -> InstallPolicySetting:
    return InstallPolicySetting(
        id=1,
        policy_mode=mode,
        trial_days=3,
        charge_on_install=True,
        group_install_fee_irr=1000,
        chan_install_fee_irr=2000,
    )


@pytest.mark.asyncio
async def test_free_install_group_capabilities():
    credit = _credit()
    with (
        patch(
            "app.services.media_capability_service.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=_runtime_state(credit)),
        ),
        patch(
            "app.services.media_capability_service.InstallPolicyService.is_free_install",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.media_capability_service.InstallPolicyService.get_policy",
            AsyncMock(return_value=_policy("free")),
        ),
    ):
        caps = await get_chat_media_capabilities(-1001)

    assert caps.is_restricted is True
    assert caps.audio is True
    assert caps.download is True
    assert caps.video is False
    assert caps.tv is False
    assert caps.satellite is False
    assert caps.radio is True


@pytest.mark.asyncio
async def test_paid_credit_unlocks_full_access():
    credit = _credit(total_charged=10)
    with (
        patch(
            "app.services.media_capability_service.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=_runtime_state(credit)),
        ),
        patch(
            "app.services.media_capability_service.InstallPolicyService.is_free_install",
            AsyncMock(return_value=True),
        ),
    ):
        caps = await get_chat_media_capabilities(-1001)

    assert caps.is_restricted is False
    assert caps.video is True
    assert caps.tv is True


@pytest.mark.asyncio
async def test_hybrid_group_restricted_channel_not():
    group_credit = _credit(chat_type="group")
    channel_credit = _credit(chat_id=-1002, chat_type="channel")
    with (
        patch(
            "app.services.media_capability_service.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(side_effect=[_runtime_state(group_credit), _runtime_state(channel_credit)]),
        ),
        patch(
            "app.services.media_capability_service.InstallPolicyService.is_free_install",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.media_capability_service.InstallPolicyService.get_policy",
            AsyncMock(return_value=_policy("hybrid")),
        ),
    ):
        group_caps = await get_chat_media_capabilities(-1001)
        channel_caps = await get_chat_media_capabilities(-1002)

    assert group_caps.is_restricted is True
    assert channel_caps.is_restricted is False


@pytest.mark.asyncio
async def test_no_credit_preserves_full_access():
    with patch(
        "app.services.media_capability_service.group_runtime_state_service.get_runtime_credit_state",
        AsyncMock(return_value=_runtime_state(None)),
    ):
        caps = await get_chat_media_capabilities(-1001)

    assert caps.is_restricted is False
    assert caps.video is True


@pytest.mark.asyncio
async def test_paid_policy_trial_not_restricted():
    credit = _credit()
    with (
        patch(
            "app.services.media_capability_service.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=_runtime_state(credit)),
        ),
        patch(
            "app.services.media_capability_service.InstallPolicyService.is_free_install",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.media_capability_service.InstallPolicyService.get_policy",
            AsyncMock(return_value=_policy("paid")),
        ),
    ):
        restricted = await is_chat_free_media_restricted(-1001)

    assert restricted is False
    assert await is_media_feature_allowed(-1001, "video") is True


@pytest.mark.asyncio
async def test_unlimited_credit_unlocks_full_access_even_without_total_charged():
    credit = _credit(total_charged=0, status="unlimited", credit_days=36500)
    with (
        patch(
            "app.services.media_capability_service.group_runtime_state_service.get_runtime_credit_state",
            AsyncMock(return_value=_runtime_state(credit)),
        ),
        patch(
            "app.services.media_capability_service.InstallPolicyService.is_free_install",
            AsyncMock(return_value=True),
        ),
    ):
        caps = await get_chat_media_capabilities(-1001)

    assert caps.is_restricted is False
    assert caps.video is True
    assert caps.tv is True


@pytest.mark.asyncio
async def test_join_voice_chat_blocks_video_in_free_mode():
    from app.services.call_service import CallService

    call_py = MagicMock()
    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="https://example.com/a.mp3"),
        ),
    ):
        ok = await CallService.join_voice_chat(call_py, -1001, "https://example.com/a.mp3", "video")

    assert ok is False
    call_py.join_group_call.assert_not_called()


@pytest.mark.asyncio
async def test_join_voice_chat_allows_audio_in_free_mode():
    from app.services.call_service import CallService

    call_py = MagicMock()
    call_py.join_group_call = AsyncMock()
    with (
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch("app.services.call_service._active_calls", {}),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="/tmp/a.mp3"),
        ),
        patch("app.services.call_service._ensure_helper_in_chat", AsyncMock(return_value=1)),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch(
            "app.services.transcode_pool.pre_transcode",
            AsyncMock(return_value="/tmp/a.mp3"),
        ),
        patch("app.services.call_service.normalize_media_source", return_value="/tmp/a.mp3"),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.utils.voice_stack.vc_join", AsyncMock()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.seek_tracker.start_seek_tracker", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.media_event_service.track_media_play", AsyncMock()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.increment_active_calls",
            AsyncMock(),
        ),
    ):
        ok = await CallService.join_voice_chat(call_py, -1001, "/tmp/a.mp3", "audio")

    assert ok is True


@pytest.mark.asyncio
async def test_play_next_skips_queued_video_in_free_mode():
    from app.services.call_service import CallService

    video_item = SimpleNamespace(
        stream_url="https://example.com/v.mp4",
        file_path=None,
        media_type="video",
        title="Video track",
    )
    audio_item = SimpleNamespace(
        stream_url="https://example.com/a.mp3",
        file_path=None,
        media_type="audio",
        title="Audio track",
    )

    call_py = MagicMock()
    call_py.change_stream = AsyncMock()

    allowed_results = [False, True]

    async def _allowed(_chat_id: int, feature: str) -> bool:
        return allowed_results.pop(0)

    with (
        patch("app.services.call_service._repeat_states", {}),
        patch("app.services.call_service._active_calls", {}),
        patch(
            "app.services.call_service.playlist_repo.advance_queue",
            AsyncMock(side_effect=[video_item, audio_item]),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="https://example.com/a.mp3"),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(side_effect=_allowed),
        ),
        patch(
            "app.services.transcode_pool.pre_transcode",
            AsyncMock(return_value="/tmp/a.mp3"),
        ),
        patch("app.services.call_service.normalize_media_source", return_value="/tmp/a.mp3"),
        patch("app.utils.voice_stack.build_audio_stream", return_value=object()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.media_event_service.track_media_play", AsyncMock()),
    ):
        ok = await CallService.play_next(call_py, -1001)

    assert ok is True
    assert call_py.change_stream.await_count == 1


def test_denial_message_keys():
    assert denial_message_key("video") == "free_mode.blocked_video"
    assert denial_message_key("tv") == "free_mode.blocked_tv_satellite"
    assert denial_message_key("satellite") == "free_mode.blocked_tv_satellite"


def test_playback_type_menu_full_capabilities_shows_all_buttons():
    kb = KeyboardFactory.playback_type_menu("fa", capabilities=_FULL_CAPS)
    callbacks = _menu_callbacks(kb)
    assert CB["PB_AUDIO"] in callbacks
    assert CB["PB_VIDEO"] in callbacks
    assert CB["PB_TV"] in callbacks
    assert CB["PB_SAT"] in callbacks
    assert CB["PB_RADIO"] in callbacks
    assert CB["PB_DOWNLOAD"] in callbacks
    assert CB["NAV_BACK"] in callbacks


def test_playback_type_menu_without_capabilities_shows_all_buttons():
    kb = KeyboardFactory.playback_type_menu("fa")
    callbacks = _menu_callbacks(kb)
    assert CB["PB_VIDEO"] in callbacks
    assert CB["PB_TV"] in callbacks
    assert CB["PB_SAT"] in callbacks


def test_playback_type_menu_free_mode_hides_video_tv_satellite():
    kb = KeyboardFactory.playback_type_menu("fa", capabilities=_FREE_CAPS)
    callbacks = _menu_callbacks(kb)
    assert CB["PB_AUDIO"] in callbacks
    assert CB["PB_DOWNLOAD"] in callbacks
    assert CB["PB_RADIO"] in callbacks
    assert CB["PB_VIDEO"] not in callbacks
    assert CB["PB_TV"] not in callbacks
    assert CB["PB_SAT"] not in callbacks
    assert CB["NAV_BACK"] in callbacks

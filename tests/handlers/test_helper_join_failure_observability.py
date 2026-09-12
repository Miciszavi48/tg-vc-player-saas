from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.helper_pool_service import HelperJoinResult, ReservedHelper


@pytest.mark.asyncio
async def test_prestream_join_failure_logs_safe_reason_and_metadata(caplog):
    from app.services.call_service import _ensure_helper_in_chat

    caplog.set_level(logging.WARNING, logger="app.services.call_service")
    reserved = ReservedHelper(
        id=7,
        phone="+100000000",
        status="active",
        current_active_calls=1,
        max_concurrent_calls=50,
    )
    join_result = HelperJoinResult(
        ok=False,
        reason="join_failed",
        exception_type="UserBannedInChannel",
        safe_message="join failed via socks5://user:secret@proxy.local:1080",
    )

    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
            AsyncMock(return_value=reserved),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_with_client",
            AsyncMock(return_value=join_result),
        ),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_helper", AsyncMock(return_value=MagicMock())),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_client_for_helper", AsyncMock(return_value=MagicMock())),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.quarantine_helper",
            AsyncMock(),
        ) as quarantine_mock,
        patch(
            "app.services.helper_pool_service.HelperPoolService.bind_chat_to_helper",
            AsyncMock(),
        ) as bind_mock,
        patch(
            "app.services.helper_pool_service.HelperPoolService.get_all_helpers",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService._get_helper_row",
            AsyncMock(return_value=MagicMock(cooldown_until=None, quarantine_count=3)),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()) as event_mock,
    ):
        helper_id = await _ensure_helper_in_chat(-1003740677405, max_retries=1)

    assert helper_id.helper_id is None
    bind_mock.assert_not_awaited()
    quarantine_mock.assert_awaited_once_with(7, "pre_stream_join_failed", 1800)
    quarantine_event = next(
        call for call in event_mock.await_args_list
        if call.args and call.args[0] == "helper.quarantine"
    )
    metadata = quarantine_event.kwargs["metadata"]
    assert metadata["reason"] == "pre_stream_join_failed"
    assert metadata["join_reason"] == "join_failed"
    assert metadata["exception_type"] == "UserBannedInChannel"
    assert metadata["chat_id"] == -1003740677405
    assert "secret" not in metadata["safe_message"]
    assert "exception_type=UserBannedInChannel" in caplog.text
    assert "secret" not in caplog.text


@pytest.mark.asyncio
async def test_prestream_invite_link_failure_skips_quarantine():
    from app.services.call_service import _ensure_helper_in_chat

    reserved = ReservedHelper(
        id=9,
        phone="+100000009",
        status="active",
        current_active_calls=1,
        max_concurrent_calls=50,
    )
    join_result = HelperJoinResult(ok=False, reason="invite_link_failed")

    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
            AsyncMock(return_value=reserved),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_with_client",
            AsyncMock(return_value=join_result),
        ),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_helper", AsyncMock(return_value=MagicMock())),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_client_for_helper", AsyncMock(return_value=MagicMock())),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.quarantine_helper",
            AsyncMock(),
        ) as quarantine_mock,
        patch(
            "app.services.helper_pool_service.HelperPoolService.get_all_helpers",
            AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService._get_helper_row",
            AsyncMock(return_value=MagicMock(cooldown_until=None, quarantine_count=0)),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        result = await _ensure_helper_in_chat(-1001111, max_retries=2)

    assert result.helper_id is None
    assert result.last_join_result.reason == "invite_link_failed"
    quarantine_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_helper_joined_uses_bot_invite_link_sleep_and_join():
    from app.services.helper_pool_service import HelperPoolService

    chat_id = -1002222
    invite_link = "https://t.me/+TestInviteHash"
    mock_member = MagicMock()
    mock_member.status.value = "left"

    mock_client = MagicMock()
    mock_client.get_chat_member = AsyncMock(return_value=mock_member)
    mock_client.join_chat = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    joined_member = MagicMock()
    joined_member.status.value = "member"
    join_side_effects = [mock_member, joined_member]

    async def _member_side_effect(*_args, **_kwargs):
        return join_side_effects.pop(0)

    mock_client.get_chat_member = AsyncMock(side_effect=_member_side_effect)

    mock_bot = MagicMock()
    mock_bot.export_chat_invite_link = AsyncMock(return_value=invite_link)

    with (
        patch("app.utils.cache.get_redis", AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=None), set=AsyncMock()))),
        patch(
            "app.services.helper_pool_service.HelperPoolService.get_helper_session",
            AsyncMock(return_value="session"),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService._get_helper_row",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.build_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.helper_pool_service.resolve_group_invite_link",
            AsyncMock(return_value=invite_link),
        ),
        patch("asyncio.sleep", AsyncMock()) as sleep_mock,
        patch(
            "app.services.bot_update_service.get_runtime_bot",
            return_value=mock_bot,
        ),
    ):
        result = await HelperPoolService.ensure_helper_joined_detailed(3, chat_id)

    assert result.ok is True
    assert result.reason == "joined"
    sleep_mock.assert_awaited_once()
    assert sleep_mock.await_args.args[0] == 1.0
    mock_client.join_chat.assert_awaited_once_with(invite_link)


@pytest.mark.asyncio
async def test_user_already_participant_on_join_is_success():
    from pyrogram.errors import UserAlreadyParticipant

    from app.services.helper_pool_service import HelperPoolService

    chat_id = -1003333
    invite_link = "https://t.me/+AlreadyThere"

    mock_member = MagicMock()
    mock_member.status.value = "left"

    mock_client = MagicMock()
    mock_client.get_chat_member = AsyncMock(return_value=mock_member)
    mock_client.join_chat = AsyncMock(side_effect=UserAlreadyParticipant())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("app.utils.cache.get_redis", AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=None), set=AsyncMock()))),
        patch(
            "app.services.helper_pool_service.HelperPoolService.get_helper_session",
            AsyncMock(return_value="session"),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService._get_helper_row",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.build_client",
            return_value=mock_client,
        ),
        patch(
            "app.services.helper_pool_service.resolve_group_invite_link",
            AsyncMock(return_value=invite_link),
        ),
        patch("asyncio.sleep", AsyncMock()),
        patch(
            "app.services.bot_update_service.get_runtime_bot",
            return_value=MagicMock(),
        ),
    ):
        result = await HelperPoolService.ensure_helper_joined_detailed(4, chat_id)

    assert result.ok is True
    assert result.reason == "already_present"
    assert result.already_present is True

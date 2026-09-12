from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.helper_admin_service import (
    HelperAdminResult,
    build_helper_call_admin_privileges,
    ensure_helper_call_admin,
)
from app.services.helper_pool_service import HelperJoinResult, ReservedHelper
from app.utils.helper_admin_rights import helper_has_call_admin_rights


def _member(status: str, **privileges: bool | None):
    priv = SimpleNamespace(**privileges) if privileges else None
    return SimpleNamespace(
        status=SimpleNamespace(value=status),
        privileges=priv,
    )


def test_build_helper_call_admin_privileges_sets_video_chat_rights():
    privileges = build_helper_call_admin_privileges()
    assert privileges.can_manage_chat is True
    assert privileges.can_manage_video_chats is True
    assert privileges.can_promote_members is False


def test_helper_has_call_admin_rights_for_video_admin():
    member = _member(
        "administrator",
        can_manage_video_chats=True,
        can_manage_voice_chats=False,
        can_manage_chat=False,
    )
    assert helper_has_call_admin_rights(member) is True


def test_helper_has_call_admin_rights_for_plain_member():
    member = _member("member")
    assert helper_has_call_admin_rights(member) is False


@pytest.mark.asyncio
async def test_ensure_helper_call_admin_promotes_regular_member():
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(
        side_effect=[
            _member("administrator", can_promote_members=True),
            _member("member"),
            _member("administrator", can_manage_video_chats=True),
        ]
    )
    bot.promote_chat_member = AsyncMock(return_value=True)

    with patch(
        "app.services.helper_admin_service.resolve_helper_tg_user_id",
        AsyncMock(return_value=90001),
    ):
        result = await ensure_helper_call_admin(bot, -100123, 1)

    assert result == HelperAdminResult(ok=True, reason="promoted")
    bot.promote_chat_member.assert_awaited_once()
    privileges = bot.promote_chat_member.await_args.kwargs["privileges"]
    assert privileges.can_manage_video_chats is True


@pytest.mark.asyncio
async def test_ensure_helper_call_admin_skips_when_rights_already_present():
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(
        side_effect=[
            _member("administrator", can_promote_members=True),
            _member("administrator", can_manage_video_chats=True),
        ]
    )
    bot.promote_chat_member = AsyncMock(return_value=True)

    with patch(
        "app.services.helper_admin_service.resolve_helper_tg_user_id",
        AsyncMock(return_value=90001),
    ):
        result = await ensure_helper_call_admin(bot, -100123, 1)

    assert result == HelperAdminResult(ok=True, reason="already_admin")
    bot.promote_chat_member.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_helper_call_admin_upgrades_admin_missing_video_rights():
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(
        side_effect=[
            _member("administrator", can_promote_members=True),
            _member(
                "administrator",
                can_manage_chat=False,
                can_manage_video_chats=False,
                can_manage_voice_chats=False,
            ),
            _member("administrator", can_manage_video_chats=True),
        ]
    )
    bot.promote_chat_member = AsyncMock(return_value=True)

    with patch(
        "app.services.helper_admin_service.resolve_helper_tg_user_id",
        AsyncMock(return_value=90001),
    ):
        result = await ensure_helper_call_admin(bot, -100123, 1)

    assert result.ok is True
    bot.promote_chat_member.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_helper_call_admin_bot_without_promote_rights():
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(
        return_value=_member("administrator", can_promote_members=False),
    )

    with patch(
        "app.services.helper_admin_service.resolve_helper_tg_user_id",
        AsyncMock(return_value=90001),
    ):
        result = await ensure_helper_call_admin(bot, -100123, 1)

    assert result == HelperAdminResult(ok=False, reason="bot_no_promote_rights")


@pytest.mark.asyncio
async def test_ensure_helper_in_chat_returns_helper_when_promote_fails():
    reserved = ReservedHelper(
        id=3,
        phone="+1",
        status="active",
        current_active_calls=1,
        max_concurrent_calls=50,
    )

    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
            AsyncMock(return_value=reserved),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_with_client",
            AsyncMock(return_value=HelperJoinResult(ok=True, reason="joined")),
        ),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_helper", AsyncMock(return_value=MagicMock())),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_client_for_helper", AsyncMock(return_value=MagicMock())),
        patch(
            "app.services.helper_pool_service.HelperPoolService.bind_chat_to_helper",
            AsyncMock(),
        ),
        patch(
            "app.services.helper_admin_service.ensure_helper_call_admin",
            AsyncMock(return_value=HelperAdminResult(ok=False, reason="bot_no_promote_rights")),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        from app.services.call_service import _ensure_helper_in_chat

        result = await _ensure_helper_in_chat(-100555, max_retries=1)

    assert result.helper_id == 3


@pytest.mark.asyncio
async def test_ensure_helper_present_for_group_returns_promote_failed():
    binding = SimpleNamespace(helper_account_id=7)

    with (
        patch("app.services.call_service.async_session") as session_mock,
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.helper_admin_service.ensure_helper_call_admin",
            AsyncMock(return_value=HelperAdminResult(ok=False, reason="bot_no_promote_rights")),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        session = AsyncMock()
        session_mock.return_value.__aenter__.return_value = session
        session.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: binding),
        )

        from app.services.call_service import ensure_helper_present_for_group

        result = await ensure_helper_present_for_group(-100777, reason="test")

    assert result == "promote_failed"


@pytest.mark.asyncio
async def test_ensure_helper_present_for_group_already_present_success():
    binding = SimpleNamespace(helper_account_id=8)

    with (
        patch("app.services.call_service.async_session") as session_mock,
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.helper_admin_service.ensure_helper_call_admin",
            AsyncMock(return_value=HelperAdminResult(ok=True, reason="already_admin")),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        session = AsyncMock()
        session_mock.return_value.__aenter__.return_value = session
        session.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: binding),
        )

        from app.services.call_service import ensure_helper_present_for_group

        result = await ensure_helper_present_for_group(-100888, reason="test")

    assert result == "already_present"

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import HelperAccount, HelperChatBinding, PlaybackState
from app.services import group_call_moderation_service as raw_svc
from app.services import group_text_call_command_service as cmd_svc


class _FakeHelperClient:
    def __init__(
        self,
        *,
        helper_id: int,
        helper_user_id: int,
        member_status: str = "administrator",
        can_manage: bool | None = True,
        active_call: bool = True,
    ) -> None:
        from pyrogram import raw

        self.raw = raw
        self.helper_id = helper_id
        self.helper_user_id = helper_user_id
        self.member_status = member_status
        self.can_manage = can_manage
        self.active_call = active_call
        self.started = False
        self.stopped = False
        self.requests: list[object] = []

    async def start(self):
        self.started = True
        return self

    async def stop(self):
        self.stopped = True

    async def get_chat_member(self, chat_id, user_id):  # noqa: ANN001
        privileges = SimpleNamespace(
            can_manage_video_chats=self.can_manage,
            can_manage_voice_chats=self.can_manage,
            can_manage_chat=self.can_manage,
        )
        return SimpleNamespace(
            status=SimpleNamespace(value=self.member_status),
            privileges=privileges,
            user=SimpleNamespace(id=self.helper_user_id),
        )

    async def resolve_peer(self, peer_id):
        if int(peer_id) == 777:
            return self.raw.types.InputPeerUser(user_id=777, access_hash=888)
        return self.raw.types.InputPeerChannel(channel_id=123, access_hash=456)

    async def invoke(self, request):
        self.requests.append(request)
        name = type(request).__name__
        if name == "GetFullChannel":
            call = (
                self.raw.types.InputGroupCall(id=10, access_hash=20)
                if self.active_call
                else None
            )
            return SimpleNamespace(full_chat=SimpleNamespace(call=call))
        if name == "ExportGroupCallInvite":
            return SimpleNamespace(link="https://t.me/+helpercall")
        return SimpleNamespace(ok=True)


async def _create_helper(
    phone_suffix: int,
    *,
    status: str = "active",
    current_active_calls: int = 0,
    max_concurrent_calls: int = 50,
    cooldown_until=None,
) -> HelperAccount:
    phone = f"+100000{phone_suffix}"
    tg_user_id = 900000 + phone_suffix
    async with async_session() as session:
        helper = (
            await session.execute(select(HelperAccount).where(HelperAccount.phone == phone))
        ).scalar_one_or_none()
        if helper is None:
            helper = HelperAccount(
                phone=phone,
                tg_user_id=tg_user_id,
                display_name=f"Helper {phone_suffix}",
                username=f"helper{phone_suffix}",
                session_string_enc=f"encrypted-{phone_suffix}",
                status=status,
                current_active_calls=current_active_calls,
                max_concurrent_calls=max_concurrent_calls,
                cooldown_until=cooldown_until,
            )
            session.add(helper)
        else:
            helper.tg_user_id = tg_user_id
            helper.session_string_enc = f"encrypted-{phone_suffix}"
            helper.status = status
            helper.current_active_calls = current_active_calls
            helper.max_concurrent_calls = max_concurrent_calls
            helper.cooldown_until = cooldown_until
            helper.banned_until = None
        await session.commit()
        await session.refresh(helper)
    return helper


async def _bind(chat_id: int, helper_id: int) -> None:
    async with async_session() as session:
        session.add(HelperChatBinding(chat_id=chat_id, helper_account_id=helper_id))
        await session.commit()


async def _binding_helper_id(chat_id: int) -> int | None:
    async with async_session() as session:
        result = await session.execute(
            select(HelperChatBinding.helper_account_id).where(
                HelperChatBinding.chat_id == chat_id
            )
        )
        value = result.scalar_one_or_none()
    return int(value) if value is not None else None


async def _seed_playback(chat_id: int) -> None:
    async with async_session() as session:
        session.add(
            PlaybackState(
                chat_id=chat_id,
                media_type="audio",
                source="test",
                title="Active",
                is_paused=False,
            )
        )
        await session.commit()


def _patch_helper_clients(client_by_helper_id: dict[int, _FakeHelperClient]):
    built: list[_FakeHelperClient] = []

    def _build_client(name, session_string, helper):  # noqa: ANN001
        client = client_by_helper_id[int(helper.id)]
        built.append(client)
        return client

    return (
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.get_helper_session",
            AsyncMock(return_value="session"),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.build_client",
            side_effect=_build_client,
        ),
        built,
    )


@pytest.mark.asyncio
async def test_manual_addhelper_join_stores_binding_in_real_db():
    from app.services.call_service import ensure_helper_present_for_group
    from app.services.helper_pool_service import HelperJoinResult

    chat_id = -10099101
    helper = await _create_helper(99101)

    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
            AsyncMock(return_value=SimpleNamespace(id=helper.id)),
        ) as reserve_best_helper,
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_detailed",
            AsyncMock(return_value=HelperJoinResult(ok=True, reason="joined")),
        ),
        patch(
            "app.services.helper_admin_service.ensure_helper_call_admin",
            AsyncMock(return_value=SimpleNamespace(ok=True, reason="promoted")),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        result = await ensure_helper_present_for_group(chat_id, reason="test")

    assert result == "success"
    reserve_best_helper.assert_awaited_once_with(chat_id)
    assert await _binding_helper_id(chat_id) == helper.id


@pytest.mark.asyncio
async def test_manual_addhelper_is_idempotent_for_existing_binding():
    from app.services.call_service import ensure_helper_present_for_group

    chat_id = -10099102
    helper = await _create_helper(99102)
    await _bind(chat_id, helper.id)

    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=True),
        ) as ensure_joined,
        patch(
            "app.services.helper_admin_service.ensure_helper_call_admin",
            AsyncMock(return_value=SimpleNamespace(ok=True, reason="already_admin")),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        result = await ensure_helper_present_for_group(chat_id, reason="test")

    assert result == "already_present"
    ensure_joined.assert_awaited_once_with(helper.id, chat_id)
    assert await _binding_helper_id(chat_id) == helper.id


@pytest.mark.asyncio
async def test_resolver_reuses_existing_bound_helper_for_same_group():
    chat_id = -10099103
    bound = await _create_helper(99103)
    pool = await _create_helper(99104)
    await _bind(chat_id, bound.id)
    client_by_helper = {
        bound.id: _FakeHelperClient(helper_id=bound.id, helper_user_id=bound.tg_user_id),
        pool.id: _FakeHelperClient(helper_id=pool.id, helper_user_id=pool.tg_user_id),
    }
    p1, p2, p3, built = _patch_helper_clients(client_by_helper)

    with p1, p2, p3:
        resolution = await raw_svc.resolve_group_call_helper(
            chat_id,
            require_admin=True,
            reason="test",
        )
        await raw_svc.close_group_call_helper(resolution)

    assert resolution.ok is True
    assert resolution.helper_id == bound.id
    assert resolution.helper_user_id == bound.tg_user_id
    assert resolution.binding_source == "bound"
    assert built == [client_by_helper[bound.id]]


@pytest.mark.asyncio
async def test_group_b_uses_its_own_binding_not_group_a_binding():
    chat_a = -10099104
    chat_b = -10099105
    helper_a = await _create_helper(99105)
    helper_b = await _create_helper(99106)
    await _bind(chat_a, helper_a.id)
    await _bind(chat_b, helper_b.id)
    client_by_helper = {
        helper_a.id: _FakeHelperClient(helper_id=helper_a.id, helper_user_id=helper_a.tg_user_id),
        helper_b.id: _FakeHelperClient(helper_id=helper_b.id, helper_user_id=helper_b.tg_user_id),
    }
    p1, p2, p3, built = _patch_helper_clients(client_by_helper)

    with p1, p2, p3:
        resolution = await raw_svc.resolve_group_call_helper(
            chat_b,
            require_admin=True,
            reason="test",
        )
        await raw_svc.close_group_call_helper(resolution)

    assert resolution.ok is True
    assert resolution.helper_id == helper_b.id
    assert built == [client_by_helper[helper_b.id]]


@pytest.mark.asyncio
async def test_unavailable_bound_helper_falls_back_and_rebinds_explicitly():
    chat_id = -10099106
    bound = await _create_helper(
        99107,
        cooldown_until=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    fallback = await _create_helper(99108)
    await _bind(chat_id, bound.id)
    client_by_helper = {
        fallback.id: _FakeHelperClient(
            helper_id=fallback.id,
            helper_user_id=fallback.tg_user_id,
        ),
    }
    p1, p2, p3, built = _patch_helper_clients(client_by_helper)

    with (
        p1,
        p2,
        p3,
        patch(
            "app.services.group_call_moderation_service._pool_helper_excluding",
            AsyncMock(return_value=fallback),
        ),
    ):
        resolution = await raw_svc.resolve_group_call_helper(
            chat_id,
            require_admin=True,
            reason="test",
        )
        await raw_svc.close_group_call_helper(resolution)

    assert resolution.ok is True
    assert resolution.helper_id == fallback.id
    assert resolution.binding_source == "pool_fallback"
    assert await _binding_helper_id(chat_id) == fallback.id
    assert built == [client_by_helper[fallback.id]]


@pytest.mark.asyncio
async def test_resolver_no_helper_returns_visible_status():
    with patch(
        "app.services.group_call_moderation_service._helper_candidates_for_chat",
        AsyncMock(return_value=[]),
    ):
        resolution = await raw_svc.resolve_group_call_helper(
            -10099107,
            require_admin=True,
            reason="test",
        )

    assert resolution.ok is False
    assert resolution.status == "no_helper"


@pytest.mark.asyncio
async def test_resolver_joins_then_verifies_membership_and_admin():
    chat_id = -10099108
    helper = await _create_helper(99109)
    await _bind(chat_id, helper.id)
    client_by_helper = {
        helper.id: _FakeHelperClient(helper_id=helper.id, helper_user_id=helper.tg_user_id),
    }
    p1, p2, p3, _built = _patch_helper_clients(client_by_helper)

    with p1, p2 as ensure_joined_patch, p3:
        resolution = await raw_svc.resolve_group_call_helper(
            chat_id,
            require_admin=True,
            reason="test",
        )
        await raw_svc.close_group_call_helper(resolution)

    assert resolution.ok is True
    assert resolution.membership_status == "administrator"
    assert resolution.admin_status == "admin"
    ensure_joined_patch.assert_awaited_once_with(helper.id, chat_id)
    assert await _binding_helper_id(chat_id) == helper.id


@pytest.mark.asyncio
async def test_resolver_fails_when_join_fails_without_raw_call():
    chat_id = -10099109
    helper = await _create_helper(99110)
    await _bind(chat_id, helper.id)
    client_by_helper = {
        helper.id: _FakeHelperClient(helper_id=helper.id, helper_user_id=helper.tg_user_id),
    }

    def _build_client(name, session_string, helper):  # noqa: ANN001
        return client_by_helper[int(helper.id)]

    with (
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.get_helper_session",
            AsyncMock(return_value="session"),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=False),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.build_client",
            side_effect=_build_client,
        ) as build_client,
    ):
        resolution = await raw_svc.resolve_group_call_helper(
            chat_id,
            require_admin=True,
            reason="test",
        )

    assert resolution.ok is False
    assert resolution.status == "join_failed"
    build_client.assert_not_called()


@pytest.mark.asyncio
async def test_resolver_fails_when_helper_is_not_admin_for_admin_action():
    chat_id = -10099110
    helper = await _create_helper(99111)
    await _bind(chat_id, helper.id)
    client = _FakeHelperClient(
        helper_id=helper.id,
        helper_user_id=helper.tg_user_id,
        member_status="member",
        can_manage=False,
    )
    p1, p2, p3, _built = _patch_helper_clients({helper.id: client})

    with p1, p2, p3:
        resolution = await raw_svc.resolve_group_call_helper(
            chat_id,
            require_admin=True,
            reason="test",
        )

    assert resolution.ok is False
    assert resolution.status == "not_admin"
    assert client.requests == []


@pytest.mark.asyncio
async def test_resolver_allows_member_when_admin_not_required():
    chat_id = -10099111
    helper = await _create_helper(99112)
    await _bind(chat_id, helper.id)
    client = _FakeHelperClient(
        helper_id=helper.id,
        helper_user_id=helper.tg_user_id,
        member_status="member",
        can_manage=False,
    )
    p1, p2, p3, _built = _patch_helper_clients({helper.id: client})

    with p1, p2, p3:
        resolution = await raw_svc.resolve_group_call_helper(
            chat_id,
            require_admin=False,
            reason="test",
        )
        await raw_svc.close_group_call_helper(resolution)

    assert resolution.ok is True
    assert resolution.admin_status == "member"


@pytest.mark.asyncio
async def test_resolver_logs_no_session_secret(caplog):
    chat_id = -10099112
    helper = await _create_helper(99113)
    await _bind(chat_id, helper.id)
    caplog.set_level(logging.DEBUG)
    with (
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.get_helper_session",
            AsyncMock(return_value="SECRET_SESSION_STRING"),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=False),
        ),
    ):
        await raw_svc.resolve_group_call_helper(chat_id, require_admin=True, reason="test")

    assert str(helper.id) in caplog.text
    assert str(chat_id) in caplog.text
    assert "SECRET_SESSION_STRING" not in caplog.text


async def _call_with_bound_helper(chat_id: int, action):
    helper = await _create_helper(abs(chat_id) % 100000)
    await _bind(chat_id, helper.id)
    client = _FakeHelperClient(helper_id=helper.id, helper_user_id=helper.tg_user_id)
    p1, p2, p3, _built = _patch_helper_clients({helper.id: client})
    with p1, p2, p3:
        result = await action(client)
    return helper, client, result


@pytest.mark.asyncio
async def test_start_call_uses_resolved_helper_client_create_group_call():
    chat_id = -10099201

    async def _action(_client):
        return await cmd_svc.start_call(SimpleNamespace(name="bot-client"), None, chat_id)

    helper, client, result = await _call_with_bound_helper(chat_id, _action)

    assert result.ok is True
    assert type(client.requests[-1]).__name__ == "CreateGroupCall"
    assert result.reason == "started"
    assert client.helper_id == helper.id


@pytest.mark.asyncio
async def test_scheduled_end_job_uses_resolved_helper_client_discard_group_call():
    chat_id = -10099202

    async def _action(_client):
        with patch(
            "app.services.group_text_call_command_service.CallService.leave_voice_chat",
            AsyncMock(return_value=True),
        ):
            await cmd_svc._run_scheduled_call_end(None, chat_id)
        return SimpleNamespace(ok=True)

    helper, client, _result = await _call_with_bound_helper(chat_id, _action)

    assert type(client.requests[-1]).__name__ == "DiscardGroupCall"
    assert client.helper_id == helper.id


@pytest.mark.asyncio
async def test_title_link_invite_mute_unmute_and_toggles_use_helper_client():
    chat_id = -10099203
    await _seed_playback(chat_id)
    helper = await _create_helper(99203)
    await _bind(chat_id, helper.id)
    client = _FakeHelperClient(helper_id=helper.id, helper_user_id=helper.tg_user_id)
    p1, p2, p3, _built = _patch_helper_clients({helper.id: client})

    with p1, p2, p3:
        title = await cmd_svc.set_call_title(None, chat_id, "Title", updated_by=1)
        link = await cmd_svc.get_call_link(SimpleNamespace(name="bot-client"), None, SimpleNamespace(id=chat_id))
        invite = await cmd_svc.invite_user(SimpleNamespace(name="bot-client"), None, chat_id, 777)
        mute = await cmd_svc.mute_participant(None, chat_id, 777)
        unmute = await cmd_svc.unmute_participant(None, chat_id, 777)
        call_mute = await cmd_svc.set_call_mute(chat_id, True, updated_by=1)
        call_comment = await cmd_svc.set_call_comment(chat_id, False, updated_by=1)

    request_names = [type(request).__name__ for request in client.requests]
    assert title.ok is True
    assert link.ok is True
    assert invite.ok is True
    assert mute.ok is True
    assert unmute.ok is True
    assert call_mute.applied_live is True
    assert call_comment.applied_live is True
    assert "EditGroupCallTitle" in request_names
    assert "ExportGroupCallInvite" in request_names
    assert "InviteToGroupCall" in request_names
    assert request_names.count("EditGroupCallParticipant") == 2
    assert request_names.count("ToggleGroupCallSettings") == 2
    assert client.helper_id == helper.id


@pytest.mark.asyncio
async def test_raw_action_not_called_when_helper_not_admin():
    chat_id = -10099204
    await _seed_playback(chat_id)
    helper = await _create_helper(99204)
    await _bind(chat_id, helper.id)
    client = _FakeHelperClient(
        helper_id=helper.id,
        helper_user_id=helper.tg_user_id,
        member_status="member",
        can_manage=False,
    )
    p1, p2, p3, _built = _patch_helper_clients({helper.id: client})

    with p1, p2, p3:
        result = await cmd_svc.set_call_title(None, chat_id, "Nope", updated_by=1)

    assert result.ok is False
    assert result.reason == "helper_not_admin"
    assert client.requests == []

"""Call Security raw MTProto mute/unmute enforcement."""
from __future__ import annotations

import logging
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


class _AsyncContextClient:
    def __init__(self, inner):
        self.inner = inner

    async def __aenter__(self):
        return self.inner

    async def __aexit__(self, *_exc):
        return False


_DEFAULT_CALL = object()


class _RawClient:
    def __init__(
        self,
        *,
        call=_DEFAULT_CALL,
        invoke_error=None,
        peer_error=None,
        basic=False,
    ):
        from pyrogram import raw

        self.raw = raw
        self.call = (
            raw.types.InputGroupCall(id=1, access_hash=2)
            if call is _DEFAULT_CALL
            else call
        )
        self.invoke_error = invoke_error
        self.peer_error = peer_error
        self.basic = basic
        self.requests = []

    async def resolve_peer(self, peer_id):
        if peer_id == 777 and self.peer_error is not None:
            raise self.peer_error
        if peer_id == 777:
            return self.raw.types.InputPeerUser(user_id=777, access_hash=888)
        if self.basic:
            return self.raw.types.InputPeerChat(chat_id=123)
        return self.raw.types.InputPeerChannel(channel_id=123, access_hash=456)

    async def invoke(self, request):
        self.requests.append(request)
        if self.invoke_error is not None:
            raise self.invoke_error
        cls_name = type(request).__name__
        if cls_name in {"GetFullChannel", "GetFullChat"}:
            return SimpleNamespace(full_chat=SimpleNamespace(call=self.call))
        return SimpleNamespace(ok=True)


def test_raw_api_probe_detects_edit_group_call_participant():
    from app.services import group_call_moderation_service as svc

    assert svc.raw_mute_api_available() is True


@pytest.mark.asyncio
async def test_probe_does_not_rely_on_high_level_pytgcalls_methods():
    from app.services import call_security_service
    from app.services import group_call_moderation_service as svc

    high_level_only = SimpleNamespace(mute=AsyncMock(), unmute=AsyncMock())

    assert await svc.probe_group_call_mute_support(high_level_only) is False
    assert call_security_service.probe_mute_support(high_level_only) is False


@pytest.mark.asyncio
async def test_resolve_active_call_from_get_full_channel():
    from app.services import group_call_moderation_service as svc

    client = _RawClient()
    call = await svc.resolve_active_input_group_call(client, -100123)

    assert call is client.call
    assert type(client.requests[0]).__name__ == "GetFullChannel"


@pytest.mark.asyncio
async def test_resolve_active_call_from_get_full_chat():
    from app.services import group_call_moderation_service as svc

    client = _RawClient(basic=True)
    call = await svc.resolve_active_input_group_call(client, -123)

    assert call is client.call
    assert type(client.requests[0]).__name__ == "GetFullChat"


@pytest.mark.asyncio
async def test_no_active_call_returns_no_active_call_not_unsupported():
    from app.services import group_call_moderation_service as svc

    client = _RawClient(call=None)
    result = await svc.mute_group_call_participant(
        client,
        -100123,
        777,
        muted=True,
    )

    assert result.supported is True
    assert result.ok is False
    assert result.reason == "no_active_call"


@pytest.mark.asyncio
async def test_successful_mute_invokes_edit_group_call_participant_muted_true():
    from app.services import group_call_moderation_service as svc

    client = _RawClient()
    result = await svc.mute_group_call_participant(
        client,
        -100123,
        777,
        muted=True,
    )

    assert result.ok is True
    request = client.requests[-1]
    assert type(request).__name__ == "EditGroupCallParticipant"
    assert request.muted is True


@pytest.mark.asyncio
async def test_successful_unmute_invokes_edit_group_call_participant_muted_false():
    from app.services import group_call_moderation_service as svc

    client = _RawClient()
    result = await svc.mute_group_call_participant(
        client,
        -100123,
        777,
        muted=False,
    )

    assert result.ok is True
    request = client.requests[-1]
    assert type(request).__name__ == "EditGroupCallParticipant"
    assert request.muted is False


@pytest.mark.asyncio
async def test_permission_error_maps_to_permission_denied():
    from app.services import group_call_moderation_service as svc

    exc = type("ChatAdminRequired", (Exception,), {})("CHAT_ADMIN_REQUIRED")
    result = await svc.mute_group_call_participant(
        _RawClient(invoke_error=exc),
        -100123,
        777,
        muted=True,
    )

    assert result.ok is False
    assert result.reason == "permission_denied"
    assert result.error_class == "ChatAdminRequired"


@pytest.mark.asyncio
async def test_peer_error_maps_to_participant_not_found():
    from app.services import group_call_moderation_service as svc

    exc = type("PeerIdInvalid", (Exception,), {})("PEER_ID_INVALID")
    result = await svc.mute_group_call_participant(
        _RawClient(peer_error=exc),
        -100123,
        777,
        muted=True,
    )

    assert result.ok is False
    assert result.reason == "participant_not_found"


@pytest.mark.asyncio
async def test_floodwait_maps_to_flood_wait():
    from app.services import group_call_moderation_service as svc

    exc = type("FloodWait", (Exception,), {})("FLOOD_WAIT_10")
    result = await svc.mute_group_call_participant(
        _RawClient(invoke_error=exc),
        -100123,
        777,
        muted=True,
    )

    assert result.ok is False
    assert result.reason == "flood_wait"


@pytest.mark.asyncio
async def test_missing_raw_function_maps_to_unsupported():
    from app.services import group_call_moderation_service as svc

    with patch(
        "app.services.group_call_moderation_service.raw_mute_api_available",
        return_value=False,
    ):
        result = await svc.try_mute_participant(-100123, 777)

    assert result.ok is False
    assert result.supported is False
    assert result.reason == "raw_api_unavailable"


@pytest.mark.asyncio
async def test_logs_contain_exception_class_not_secret(caplog):
    from app.services import group_call_moderation_service as svc

    caplog.set_level(logging.INFO)
    exc = type("ChatAdminRequired", (Exception,), {})("SECRET_API_HASH")
    await svc.mute_group_call_participant(
        _RawClient(invoke_error=exc),
        -100123,
        777,
        muted=True,
    )

    assert "ChatAdminRequired" in caplog.text
    assert "SECRET_API_HASH" not in caplog.text


@pytest.mark.asyncio
async def test_top_level_try_mute_uses_helper_client_and_raw_call():
    from pyrogram import raw

    from app.services import group_call_moderation_service as svc

    helper = SimpleNamespace(id=5, tg_user_id=5005, session_string_enc="encrypted")
    inner_client = _RawClient(call=raw.types.InputGroupCall(id=10, access_hash=20))
    inner_client.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(
            status="administrator",
            privileges=SimpleNamespace(can_manage_video_chats=True),
            user=SimpleNamespace(id=5005),
        )
    )
    with (
        patch(
            "app.services.group_call_moderation_service._helper_candidates_for_chat",
            AsyncMock(return_value=[(helper, "bound")]),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.get_helper_session",
            AsyncMock(return_value="session"),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.ensure_helper_joined",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.bind_chat_to_helper",
            AsyncMock(),
        ),
        patch(
            "app.services.group_call_moderation_service.HelperPoolService.build_client",
            return_value=_AsyncContextClient(inner_client),
        ),
    ):
        result = await svc.try_mute_participant(-100123, 777, muted=True)

    assert result.ok is True
    assert type(inner_client.requests[-1]).__name__ == "EditGroupCallParticipant"


async def _run_runtime_join(
    *,
    privileged: bool,
    auto_unmute: bool,
    mute_result,
    unmute_result,
):
    from app.handlers import call_security_runtime as csr

    participant = SimpleNamespace(
        peer=SimpleNamespace(user_id=777),
        muted=True,
        left=False,
        just_joined=True,
        video=None,
    )
    settings = SimpleNamespace(
        enabled=True,
        mute_incoming_enabled=True,
        report_enabled=True,
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
        ),
        patch(
            "app.handlers.call_security_runtime.call_security_service.is_privileged_member",
            AsyncMock(return_value=privileged),
        ),
        patch(
            "app.handlers.call_security_runtime.call_security_service.should_auto_unmute",
            AsyncMock(return_value=auto_unmute),
        ),
        patch(
            "app.handlers.call_security_runtime.call_security_service.get_capabilities",
            return_value=SimpleNamespace(raw_mute_api_available=True),
        ),
        patch(
            "app.handlers.call_security_runtime.call_security_service.try_mute_participant",
            AsyncMock(return_value=mute_result),
        ) as mute_mock,
        patch(
            "app.handlers.call_security_runtime.call_security_service.try_unmute_participant",
            AsyncMock(return_value=unmute_result),
        ) as unmute_mock,
        patch("app.handlers.call_security_runtime._load_user_state", AsyncMock(return_value={})),
        patch("app.handlers.call_security_runtime._save_user_state", AsyncMock()),
        patch("app.handlers.call_security_runtime._record_moderation_action", AsyncMock()) as record_mock,
    ):
        await csr._process_participant(SimpleNamespace(), None, -100123, participant)
    return mute_mock, unmute_mock, record_mock


@pytest.mark.asyncio
async def test_call_security_join_path_mutes_unknown_new_user():
    result = SimpleNamespace(ok=True, supported=True, reason="muted")
    mute_mock, unmute_mock, record_mock = await _run_runtime_join(
        privileged=False,
        auto_unmute=False,
        mute_result=result,
        unmute_result=SimpleNamespace(ok=False, supported=True, reason="unused"),
    )

    mute_mock.assert_awaited_once()
    unmute_mock.assert_not_awaited()
    assert record_mock.await_args.kwargs["action_reason"] == "mute_enforced"


@pytest.mark.asyncio
async def test_call_security_join_path_unmutes_privileged_user():
    result = SimpleNamespace(ok=True, supported=True, reason="unmuted")
    mute_mock, unmute_mock, record_mock = await _run_runtime_join(
        privileged=True,
        auto_unmute=True,
        mute_result=SimpleNamespace(ok=False, supported=True, reason="unused"),
        unmute_result=result,
    )

    mute_mock.assert_not_awaited()
    unmute_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["action_reason"] == "unmute_privileged"


@pytest.mark.asyncio
async def test_call_security_join_path_unmutes_old_membership_user():
    result = SimpleNamespace(ok=True, supported=True, reason="unmuted")
    mute_mock, unmute_mock, record_mock = await _run_runtime_join(
        privileged=False,
        auto_unmute=True,
        mute_result=SimpleNamespace(ok=False, supported=True, reason="unused"),
        unmute_result=result,
    )

    mute_mock.assert_not_awaited()
    unmute_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["action_reason"] == "unmute_membership_age"


@pytest.mark.asyncio
async def test_unknown_membership_age_does_not_auto_unmute():
    result = SimpleNamespace(ok=True, supported=True, reason="muted")
    mute_mock, unmute_mock, _record_mock = await _run_runtime_join(
        privileged=False,
        auto_unmute=False,
        mute_result=result,
        unmute_result=SimpleNamespace(ok=False, supported=True, reason="unused"),
    )

    mute_mock.assert_awaited_once()
    unmute_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_enforcement_is_recorded_for_rate_limited_report_path():
    from app.handlers import call_security_runtime as csr

    event = SimpleNamespace(ok=False, supported=True, reason="permission_denied")
    with (
        patch("app.handlers.call_security_runtime._append_event", AsyncMock()) as append_mock,
        patch("app.handlers.call_security_runtime._send_live_report", AsyncMock()) as send_mock,
    ):
        await csr._record_moderation_action(
            SimpleNamespace(),
            -100123,
            777,
            action_reason="mute_failed",
            detail=event.reason,
            report_enabled=True,
            report_failure=True,
        )

    append_mock.assert_awaited_once()
    send_mock.assert_awaited_once()


def test_no_call_security_callback_data_changed():
    from app.utils.ui import CB

    assert CB["GRP_CALLSEC"] == "grp:callsec"
    assert CB["GRP_CALLSEC_MUTE_IN"] == "grp:callsec:mute_in"
    assert CB["GRP_CALLSEC_AGE"] == "grp:callsec:age"

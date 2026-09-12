from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.database.models import HelperAccount
from app.services.helper_pool_service import (
    HelperJoinResult,
    HelperPoolService,
    ReservedHelper,
    _helper_is_pool_eligible,
    _reserved_from_row,
)


def _helper(
    *,
    helper_id: int,
    status: str = "active",
    current_active_calls: int = 0,
    max_concurrent_calls: int = 50,
    cooldown_until=None,
    banned_until=None,
) -> HelperAccount:
    return HelperAccount(
        id=helper_id,
        phone=f"+{helper_id}",
        status=status,
        current_active_calls=current_active_calls,
        max_concurrent_calls=max_concurrent_calls,
        cooldown_until=cooldown_until,
        banned_until=banned_until,
    )


def _session_with_begin(*execute_results):
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.execute = AsyncMock(side_effect=list(execute_results))
    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    sess.begin = MagicMock(return_value=begin_ctx)
    return sess


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def test_reserved_from_row_reflects_increment():
    row = _helper(helper_id=3, current_active_calls=2)
    reserved = _reserved_from_row(row, incremented=True)
    assert reserved.current_active_calls == 3
    assert reserved.id == 3


@pytest.mark.asyncio
async def test_reserve_best_helper_bound_uses_for_update_skip_locked():
    binding = SimpleNamespace(helper_account_id=5)
    bound = _helper(helper_id=5, current_active_calls=1)
    sess = _session_with_begin(_scalar_result(binding), _scalar_result(bound), MagicMock())

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.reserve_best_helper(-1001)

    assert result is not None
    assert result.id == 5
    bound_stmt = sess.execute.await_args_list[1].args[0]
    assert bound_stmt._for_update_arg is not None  # noqa: SLF001
    assert bound_stmt._for_update_arg.skip_locked is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_reserve_best_helper_pool_uses_skip_locked_and_id_tiebreak():
    helper = _helper(helper_id=2, current_active_calls=0)
    sess = _session_with_begin(
        _scalar_result(None),
        _scalar_result(helper),
        MagicMock(),
    )

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.reserve_best_helper(-1002)

    assert result is not None
    assert result.id == 2
    pool_stmt = sess.execute.await_args_list[1].args[0]
    assert pool_stmt._for_update_arg.skip_locked is True  # noqa: SLF001
    order = pool_stmt._order_by_clauses  # noqa: SLF001
    assert len(order) == 2


@pytest.mark.asyncio
async def test_reserve_best_helper_increments_in_same_transaction():
    helper = _helper(helper_id=4, current_active_calls=2)
    sess = _session_with_begin(_scalar_result(None), _scalar_result(helper), MagicMock())

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.reserve_best_helper(-1003)

    assert result is not None
    assert result.current_active_calls == 2
    update_stmt = sess.execute.await_args_list[2].args[0]
    assert "current_active_calls" in str(update_stmt)


@pytest.mark.asyncio
async def test_reserve_best_helper_prefers_eligible_bound_helper():
    binding = SimpleNamespace(helper_account_id=8)
    bound = _helper(helper_id=8, current_active_calls=0)
    sess = _session_with_begin(_scalar_result(binding), _scalar_result(bound), MagicMock())

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.reserve_best_helper(-1004)

    assert result is not None
    assert result.id == 8
    assert sess.execute.await_count == 3


@pytest.mark.asyncio
async def test_reserve_best_helper_skips_bound_at_capacity_uses_pool():
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    binding = SimpleNamespace(helper_account_id=9)
    at_cap = _helper(helper_id=9, current_active_calls=1, max_concurrent_calls=1)
    pool = _helper(helper_id=10, current_active_calls=0)
    assert _helper_is_pool_eligible(at_cap, now) is False

    sess = _session_with_begin(
        _scalar_result(binding),
        _scalar_result(None),
        _scalar_result(pool),
        MagicMock(),
    )

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.reserve_best_helper(-1005)

    assert result is not None
    assert result.id == 10


@pytest.mark.asyncio
async def test_reserve_best_helper_returns_none_when_empty():
    sess = _session_with_begin(_scalar_result(None), _scalar_result(None))

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.reserve_best_helper(-1006)

    assert result is None


@pytest.mark.asyncio
async def test_concurrent_reserve_different_helpers_when_capacity_one():
    """With row locks, second waiter should not receive the same locked row."""
    helper_a = _helper(helper_id=1, current_active_calls=0, max_concurrent_calls=1)
    helper_b = _helper(helper_id=2, current_active_calls=0, max_concurrent_calls=1)
    call_n = {"n": 0}

    def _next_pool():
        call_n["n"] += 1
        return _scalar_result(helper_a if call_n["n"] == 1 else helper_b)

    def _make_sess():
        return _session_with_begin(_scalar_result(None), _next_pool(), MagicMock())

    with patch(
        "app.services.helper_pool_service.async_session",
        side_effect=[_make_sess(), _make_sess()],
    ):
        first, second = await asyncio.gather(
            HelperPoolService.reserve_best_helper(-2001),
            HelperPoolService.reserve_best_helper(-2002),
        )

    assert first is not None and second is not None
    assert first.id != second.id


@pytest.mark.asyncio
async def test_release_helper_reservation_clamps_in_sql():
    sess = _session_with_begin(MagicMock())
    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        await HelperPoolService.release_helper_reservation(7)

    stmt = sess.execute.await_args.args[0]
    assert "current_active_calls" in str(stmt.whereclause)


def test_join_voice_chat_does_not_increment_after_reservation():
    from app.services import call_service

    src = inspect.getsource(call_service.CallService.join_voice_chat)
    assert "increment_active_calls" not in src
    assert "reserve_best_helper" not in src
    assert "_ensure_helper_in_chat" in src


@pytest.mark.asyncio
async def test_join_voice_chat_releases_on_setup_failure():
    with (
        patch(
            "app.services.call_service._ensure_helper_in_chat",
            AsyncMock(return_value=11),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="/tmp/safe.ogg"),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.transcode_pool.pre_transcode",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ) as release_mock,
    ):
        from app.services.call_service import CallService

        ok = await CallService.join_voice_chat(
            MagicMock(), -9001, "/tmp/safe.ogg", media_type="audio"
        )

    assert ok is False
    release_mock.assert_awaited_once_with(11)


@pytest.mark.asyncio
async def test_join_voice_chat_does_not_release_on_success():
    call_py = MagicMock()
    call_py.join_group_call = AsyncMock()

    with (
        patch(
            "app.services.call_service._ensure_helper_in_chat",
            AsyncMock(return_value=12),
        ),
        patch(
            "app.services.call_service.resolve_media_source_for_playback",
            AsyncMock(return_value="/tmp/safe.ogg"),
        ),
        patch(
            "app.services.media_capability_service.is_media_feature_allowed",
            AsyncMock(return_value=True),
        ),
        patch(
            "app.services.transcode_pool.pre_transcode",
            AsyncMock(return_value="/tmp/safe.tc.ogg"),
        ),
        patch(
            "app.services.call_service.normalize_media_source",
            side_effect=lambda x: x,
        ),
        patch("app.services.call_service._ensure_group_call_before_play", AsyncMock(return_value=True)),
        patch("app.utils.voice_stack.build_audio_stream", return_value=MagicMock()),
        patch("app.utils.voice_stack.vc_join", AsyncMock()),
        patch("app.services.call_service._save_playback_state", AsyncMock()),
        patch("app.services.call_service._trigger_prefetch"),
        patch("app.services.seek_tracker.start_seek_tracker", AsyncMock()),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ) as release_mock,
        patch(
            "app.services.helper_pool_service.HelperPoolService.increment_active_calls",
            AsyncMock(),
        ) as inc_mock,
    ):
        from app.services.call_service import CallService

        ok = await CallService.join_voice_chat(call_py, -9002, "/tmp/safe.ogg")

    assert ok is True
    release_mock.assert_not_awaited()
    inc_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_helper_releases_on_join_failure_before_retry():
    reserved1 = ReservedHelper(
        id=1, phone="+1", status="active", current_active_calls=1, max_concurrent_calls=50,
    )
    reserved2 = ReservedHelper(
        id=2, phone="+2", status="active", current_active_calls=1, max_concurrent_calls=50,
    )
    reserves = [reserved1, reserved2]

    async def _reserve(_chat_id: int):
        return reserves.pop(0)

    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
            side_effect=_reserve,
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_with_client",
            AsyncMock(side_effect=[
                HelperJoinResult(ok=False, reason="join_failed"),
                HelperJoinResult(ok=True, reason="joined"),
            ]),
        ),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_helper", AsyncMock(return_value=MagicMock())),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_client_for_helper", AsyncMock(return_value=MagicMock())),
        patch("app.services.helper_admin_service.ensure_helper_call_admin", AsyncMock(return_value=MagicMock(ok=True, reason="promoted"))),
        patch("app.services.helper_pool_service.HelperPoolService.get_all_helpers", AsyncMock(return_value=[])),
        patch("app.services.helper_pool_service.HelperPoolService._get_helper_row", AsyncMock(return_value=None)),
        patch(
            "app.services.helper_pool_service.HelperPoolService.release_helper_reservation",
            AsyncMock(),
        ) as release_mock,
        patch(
            "app.services.helper_pool_service.HelperPoolService.quarantine_helper",
            AsyncMock(),
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.bind_chat_to_helper",
            AsyncMock(),
        ),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        from app.services.call_service import _ensure_helper_in_chat

        helper_id = await _ensure_helper_in_chat(-9003, max_retries=2)

        assert helper_id.helper_id == 2
    release_mock.assert_awaited_once_with(1)


@pytest.mark.asyncio
async def test_leave_voice_chat_still_decrements_once():
    with patch(
        "app.services.helper_pool_service.HelperPoolService.decrement_active_calls",
        AsyncMock(),
    ) as dec_mock:
        from app.services.call_service import CallService

        call_py = MagicMock()
        call_py.leave_group_call = AsyncMock()
        await CallService.leave_voice_chat(call_py, -9004)

    dec_mock.assert_awaited_once_with(-9004)

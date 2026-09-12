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
    _helper_is_pool_eligible,
    _helper_sort_key,
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


def _session_chain(*execute_results):
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.execute = AsyncMock(side_effect=list(execute_results))
    return sess


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def test_helper_is_pool_eligible_respects_status_and_capacity():
    now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
    assert _helper_is_pool_eligible(_helper(helper_id=1), now) is True
    assert _helper_is_pool_eligible(_helper(helper_id=2, status="quarantined"), now) is False
    assert _helper_is_pool_eligible(
        _helper(helper_id=3, current_active_calls=50, max_concurrent_calls=50),
        now,
    ) is False
    assert _helper_is_pool_eligible(
        _helper(helper_id=4, cooldown_until=now + timedelta(hours=1)),
        now,
    ) is False
    assert _helper_is_pool_eligible(
        _helper(helper_id=5, banned_until=now + timedelta(hours=1)),
        now,
    ) is False


def test_helper_sort_key_prefers_fewer_active_calls():
    low = _helper(helper_id=2, current_active_calls=1)
    high = _helper(helper_id=1, current_active_calls=5)
    assert _helper_sort_key(low) < _helper_sort_key(high)


@pytest.mark.asyncio
async def test_get_best_helper_returns_bound_active_helper():
    bound_helper = _helper(helper_id=7, current_active_calls=99, max_concurrent_calls=1)
    binding = SimpleNamespace(helper_account_id=7)
    sess = _session_chain(
        _scalar_result(binding),
        _scalar_result(bound_helper),
    )

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.get_best_helper(-1001)

    assert result is bound_helper
    assert sess.execute.await_count == 2


@pytest.mark.asyncio
async def test_get_best_helper_pool_picks_lowest_active_calls():
    helper_low = _helper(helper_id=2, current_active_calls=1)
    binding_none = _scalar_result(None)
    pool_result = _scalar_result(helper_low)
    sess = _session_chain(binding_none, pool_result)

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.get_best_helper(-2002)

    assert result is helper_low
    pool_stmt = sess.execute.await_args_list[1].args[0]
    compiled = str(pool_stmt)
    assert "current_active_calls" in compiled
    assert pool_stmt._limit_clause.value == 1  # noqa: SLF001


@pytest.mark.asyncio
async def test_get_best_helper_returns_none_when_pool_empty():
    sess = _session_chain(_scalar_result(None), _scalar_result(None))

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        result = await HelperPoolService.get_best_helper(-3003)

    assert result is None


@pytest.mark.asyncio
async def test_get_best_helper_sql_filters_capacity_and_status():
    sess = _session_chain(_scalar_result(None), _scalar_result(None))

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        await HelperPoolService.get_best_helper(-4004)

    pool_stmt = sess.execute.await_args_list[1].args[0]
    compiled = str(pool_stmt.whereclause)
    assert "status" in compiled
    assert "max_concurrent_calls" in compiled
    assert "cooldown_until" in compiled
    assert "banned_until" in compiled


@pytest.mark.asyncio
async def test_increment_active_calls_updates_bound_helper():
    mock_binding = MagicMock()
    mock_binding.helper_account_id = 9
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.execute = AsyncMock(side_effect=[_scalar_result(mock_binding), MagicMock()])
    sess.begin = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
    )

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        await HelperPoolService.increment_active_calls(-5005)

    update_stmt = sess.execute.await_args_list[1].args[0]
    compiled = str(update_stmt)
    assert "current_active_calls" in compiled


@pytest.mark.asyncio
async def test_decrement_active_calls_clamps_at_zero_in_sql():
    mock_binding = MagicMock()
    mock_binding.helper_account_id = 9
    sess = AsyncMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    sess.execute = AsyncMock(side_effect=[_scalar_result(mock_binding), MagicMock()])
    sess.begin = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=False))
    )

    with patch("app.services.helper_pool_service.async_session", return_value=sess):
        await HelperPoolService.decrement_active_calls(-5006)

    update_stmt = sess.execute.await_args_list[1].args[0]
    compiled = str(update_stmt.whereclause)
    assert "current_active_calls" in compiled


@pytest.mark.asyncio
async def test_concurrent_get_best_helper_not_atomic_same_pick():
    """Documents race: parallel selection can return the same helper without locking."""
    shared = _helper(helper_id=1, current_active_calls=0)

    def _make_sess():
        return _session_chain(_scalar_result(None), _scalar_result(shared))

    with patch(
        "app.services.helper_pool_service.async_session",
        side_effect=[_make_sess(), _make_sess()],
    ):
        first, second = await asyncio.gather(
            HelperPoolService.get_best_helper(-6001),
            HelperPoolService.get_best_helper(-6002),
        )

    assert first is not None and second is not None
    assert first.id == second.id == 1


def test_join_voice_chat_uses_ensure_without_post_join_increment():
    from app.services import call_service

    src = inspect.getsource(call_service.CallService.join_voice_chat)
    assert "_ensure_helper_in_chat" in src
    assert "increment_active_calls" not in src


@pytest.mark.asyncio
async def test_ensure_helper_in_chat_selects_before_bind():
    """Pre-stream path: reserve_best_helper runs before bind_chat_to_helper."""
    order: list[str] = []
    reserved = MagicMock(id=3, status="active")

    async def _reserve(_chat_id: int):
        order.append("reserve_best_helper")
        return reserved

    async def _ensure(*_args, **_kwargs):
        order.append("ensure_helper_joined_with_client")
        return HelperJoinResult(ok=True, reason="joined")

    async def _bind(_chat_id: int, _helper_id: int):
        order.append("bind_chat_to_helper")

    with (
        patch(
            "app.services.helper_pool_service.HelperPoolService.reserve_best_helper",
            side_effect=_reserve,
        ),
        patch(
            "app.services.helper_pool_service.HelperPoolService.ensure_helper_joined_with_client",
            side_effect=_ensure,
        ),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_for_helper", AsyncMock(return_value=MagicMock())),
        patch("app.services.helper_pytgcalls_pool.HelperPyTgCallsPool.get_client_for_helper", AsyncMock(return_value=MagicMock())),
        patch(
            "app.services.helper_pool_service.HelperPoolService.bind_chat_to_helper",
            side_effect=_bind,
        ),
        patch("app.services.helper_admin_service.ensure_helper_call_admin", AsyncMock(return_value=MagicMock(ok=True, reason="promoted"))),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()),
    ):
        from app.services.call_service import _ensure_helper_in_chat

        helper_id = await _ensure_helper_in_chat(-7007, max_retries=1)

    assert order == ["reserve_best_helper", "ensure_helper_joined_with_client", "bind_chat_to_helper"]
    assert helper_id.helper_id == 3


@pytest.mark.asyncio
async def test_ensure_helper_logs_event_on_success():
    reserved = MagicMock(id=4, status="active")

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
        patch("app.services.helper_admin_service.ensure_helper_call_admin", AsyncMock(return_value=MagicMock(ok=True, reason="promoted"))),
        patch("app.repositories.helper_event_repo.log_event", AsyncMock()) as log_mock,
    ):
        from app.services.call_service import _ensure_helper_in_chat

        await _ensure_helper_in_chat(-8008, max_retries=1)

    log_mock.assert_awaited()
    assert log_mock.await_args.args[0] == "helper.join_chat"

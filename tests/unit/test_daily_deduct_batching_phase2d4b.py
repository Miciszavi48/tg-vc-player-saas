from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.config.settings import settings
from app.scheduler import midnight_credit_deduct
from app.services.credit_service import CreditService
from app.utils.redis_keys import CREDIT_EXPIRED_PENDING, credit_expired_pending_member


def _credit(
    *,
    row_id: int,
    chat_id: int,
    chat_type: str = "group",
    credit_days: int,
    status: str = "active",
    last_daily_deducted_on=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        chat_id=chat_id,
        chat_type=chat_type,
        credit_days=credit_days,
        status=status,
        last_daily_deducted_on=last_daily_deducted_on,
    )


def _session_for_batch(credits: list) -> MagicMock:
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    sess.begin.return_value = tx
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = credits
    sess.execute = AsyncMock(return_value=mock_result)
    return sess


def _redis_mock(*, sismember: bool = False) -> AsyncMock:
    r = AsyncMock()
    r.sismember = AsyncMock(return_value=sismember)
    r.sadd = AsyncMock()
    r.expire = AsyncMock()
    r.delete = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_default_flag_uses_legacy_path():
    with (
        patch.object(settings, "DAILY_DEDUCT_BATCHING_ENABLED", False),
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch(
            "app.services.credit_service.CreditService._daily_deduct_legacy",
            AsyncMock(return_value=7),
        ) as legacy_mock,
        patch(
            "app.services.credit_service.CreditService._daily_deduct_batched",
            AsyncMock(return_value=99),
        ) as batch_mock,
    ):
        count = await CreditService.daily_deduct_all()

    assert count == 7
    legacy_mock.assert_awaited_once()
    batch_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_flag_routes_to_batched_path():
    with (
        patch.object(settings, "DAILY_DEDUCT_BATCHING_ENABLED", True),
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch(
            "app.services.credit_service.CreditService._daily_deduct_legacy",
            AsyncMock(return_value=7),
        ) as legacy_mock,
        patch(
            "app.services.credit_service.CreditService._daily_deduct_batched",
            AsyncMock(return_value=12),
        ) as batch_mock,
    ):
        count = await CreditService.daily_deduct_all()

    assert count == 12
    batch_mock.assert_awaited_once()
    legacy_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_batched_processes_multiple_batches():
    batch1 = [_credit(row_id=1, chat_id=-1, credit_days=2), _credit(row_id=2, chat_id=-2, credit_days=3)]
    batch2 = [_credit(row_id=3, chat_id=-3, chat_type="channel", credit_days=1)]
    sessions = [_session_for_batch(batch1), _session_for_batch(batch2), _session_for_batch([])]
    redis_mock = _redis_mock()

    with (
        patch.object(settings, "DAILY_DEDUCT_BATCHING_ENABLED", True),
        patch.object(settings, "DAILY_DEDUCT_BATCH_SIZE", 2),
        patch("app.services.credit_service.async_session", side_effect=sessions),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=redis_mock)),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
    ):
        count = await CreditService._daily_deduct_batched()

    assert count == 3
    assert batch1[0].credit_days == 1
    assert batch2[0].status == "expired"
    pending_calls = [
        c for c in redis_mock.sadd.await_args_list if c.args[0] == CREDIT_EXPIRED_PENDING
    ]
    assert len(pending_calls) == 1
    assert pending_calls[0].args[1] == credit_expired_pending_member(-3, "channel")


@pytest.mark.asyncio
async def test_batched_respects_batch_size_limit_in_sql():
    rows = [_credit(row_id=5, chat_id=-5, credit_days=4)]
    sess = _session_for_batch(rows)
    redis_mock = _redis_mock()

    with (
        patch.object(settings, "DAILY_DEDUCT_BATCH_SIZE", 2),
        patch("app.services.credit_service.async_session", return_value=sess),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=redis_mock)),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
    ):
        await CreditService._daily_deduct_batched()

    stmt = sess.execute.await_args.args[0]
    assert stmt._limit_clause.value == 2  # noqa: SLF001
    assert "last_daily_deducted_on" in str(stmt.whereclause)


@pytest.mark.asyncio
async def test_batched_invalidates_every_processed_row():
    rows = [
        _credit(row_id=1, chat_id=-10, credit_days=2),
        _credit(row_id=2, chat_id=-20, credit_days=2),
    ]
    invalidate = AsyncMock()

    with (
        patch.object(settings, "DAILY_DEDUCT_BATCH_SIZE", 10),
        patch("app.services.credit_service.async_session", return_value=_session_for_batch(rows)),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=_redis_mock())),
        patch("app.services.credit_service.invalidate_credit", invalidate),
    ):
        count = await CreditService._daily_deduct_batched()

    assert count == 2
    assert invalidate.await_count == 2


@pytest.mark.asyncio
async def test_batched_adds_expired_to_pending_across_batches():
    batch1 = [_credit(row_id=1, chat_id=-1, credit_days=1)]
    batch2 = [_credit(row_id=2, chat_id=-2, credit_days=1)]
    redis_mock = _redis_mock()

    with (
        patch.object(settings, "DAILY_DEDUCT_BATCH_SIZE", 1),
        patch(
            "app.services.credit_service.async_session",
            side_effect=[_session_for_batch(batch1), _session_for_batch(batch2), _session_for_batch([])],
        ),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=redis_mock)),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
    ):
        await CreditService._daily_deduct_batched()

    pending_calls = [
        c for c in redis_mock.sadd.await_args_list if c.args[0] == CREDIT_EXPIRED_PENDING
    ]
    assert len(pending_calls) == 2
    assert pending_calls[0].args[1] == credit_expired_pending_member(-1, "group")
    assert pending_calls[1].args[1] == credit_expired_pending_member(-2, "group")


@pytest.mark.asyncio
async def test_batched_stamps_last_daily_deducted_on():
    today = date(2026, 6, 5)
    rows = [_credit(row_id=1, chat_id=-1, credit_days=2)]

    with (
        patch("app.services.credit_service.CreditService._daily_deduct_today", return_value=today),
        patch("app.services.credit_service.async_session", return_value=_session_for_batch(rows)),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=_redis_mock())),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
    ):
        await CreditService._daily_deduct_batched()

    assert rows[0].last_daily_deducted_on == today


@pytest.mark.asyncio
async def test_batched_no_credit_history():
    rows = [_credit(row_id=1, chat_id=-1, credit_days=2)]

    with (
        patch("app.services.credit_service.async_session", return_value=_session_for_batch(rows)),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=_redis_mock())),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
        patch("app.repositories.credit_repo.add_credit_history", AsyncMock()) as history_mock,
    ):
        await CreditService._daily_deduct_batched()

    history_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_skip_returns_zero_without_db():
    with (
        patch.object(settings, "DAILY_DEDUCT_BATCHING_ENABLED", True),
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value=None)),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.async_session") as session_mock,
    ):
        count = await CreditService.daily_deduct_all()

    assert count == 0
    session_mock.assert_not_called()


@pytest.mark.asyncio
async def test_batched_mid_batch_exception_returns_partial_count():
    ok_sess = _session_for_batch([_credit(row_id=1, chat_id=-1, credit_days=2)])
    fail_sess = MagicMock()
    fail_sess.__aenter__ = AsyncMock(return_value=fail_sess)
    fail_sess.__aexit__ = AsyncMock(return_value=False)
    fail_sess.begin.side_effect = RuntimeError("db down")

    redis_mock = _redis_mock()

    with (
        patch.object(settings, "DAILY_DEDUCT_BATCH_SIZE", 10),
        patch(
            "app.services.credit_service.async_session",
            side_effect=[ok_sess, fail_sess],
        ),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=redis_mock)),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
    ):
        count = await CreditService._daily_deduct_batched()

    assert count == 1
    redis_mock.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_deduct_releases_lock_on_batched_path():
    release = AsyncMock()
    with (
        patch.object(settings, "DAILY_DEDUCT_BATCHING_ENABLED", True),
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", release),
        patch(
            "app.services.credit_service.CreditService._daily_deduct_batched",
            AsyncMock(return_value=3),
        ),
    ):
        await CreditService.daily_deduct_all()

    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_midnight_scheduler_still_calls_credit_service_only():
    with (
        patch("app.scheduler.CreditService.daily_deduct_all", AsyncMock(return_value=1)) as svc_mock,
        patch("app.repositories.credit_repo.daily_deduct_all", AsyncMock()) as repo_mock,
        patch("app.scheduler.get_redis", AsyncMock(return_value=AsyncMock(smembers=AsyncMock(return_value=set())))),
    ):
        await midnight_credit_deduct()

    svc_mock.assert_awaited_once()
    repo_mock.assert_not_awaited()


def test_apply_one_day_deduction_pure_helper():
    row = _credit(row_id=1, chat_id=-1, credit_days=2)
    assert CreditService._apply_one_day_deduction(row) is False
    assert row.credit_days == 1
    assert row.status == "active"

    last = _credit(row_id=2, chat_id=-2, credit_days=1)
    assert CreditService._apply_one_day_deduction(last) is True
    assert last.credit_days == 0
    assert last.status == "expired"

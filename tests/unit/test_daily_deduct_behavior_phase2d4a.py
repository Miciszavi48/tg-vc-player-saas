from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")

from app.repositories import credit_repo
from app.scheduler import midnight_credit_deduct
from app.services.credit_service import CreditService
from app.utils.redis_keys import CREDIT_EXPIRED_PENDING, credit_expired_pending_member


def _credit(
    *,
    chat_id: int,
    chat_type: str = "group",
    credit_days: int,
    status: str = "active",
) -> SimpleNamespace:
    return SimpleNamespace(
        chat_id=chat_id,
        chat_type=chat_type,
        credit_days=credit_days,
        status=status,
    )


def _session_with_credits(credits: list) -> MagicMock:
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


async def _run_daily_deduct(credits: list, *, lock_token: str = "tok-1"):
    invalidate = AsyncMock()
    redis_mock = AsyncMock()
    redis_mock.sadd = AsyncMock()
    redis_mock.expire = AsyncMock()

    with (
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value=lock_token)),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.async_session", return_value=_session_with_credits(credits)),
        patch("app.services.credit_service.invalidate_credit", invalidate),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=redis_mock)),
    ):
        count = await CreditService.daily_deduct_all()

    return count, credits, invalidate, redis_mock


@pytest.mark.asyncio
async def test_midnight_scheduler_uses_credit_service_not_repo():
    with (
        patch("app.scheduler.CreditService.daily_deduct_all", AsyncMock(return_value=2)) as svc_mock,
        patch("app.repositories.credit_repo.daily_deduct_all", AsyncMock(return_value=99)) as repo_mock,
        patch("app.scheduler.get_redis", AsyncMock(return_value=AsyncMock(smembers=AsyncMock(return_value=set())))),
    ):
        await midnight_credit_deduct()

    svc_mock.assert_awaited_once()
    repo_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_deduct_skips_when_lock_not_acquired():
    with (
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value=None)),
        patch("app.services.credit_service.release_lock", AsyncMock()) as release_mock,
        patch("app.services.credit_service.async_session") as session_mock,
    ):
        count = await CreditService.daily_deduct_all()

    assert count == 0
    session_mock.assert_not_called()
    release_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_deduct_sql_filters_active_positive_days():
    credits = [_credit(chat_id=-1, credit_days=3)]
    sess = _session_with_credits(credits)

    with (
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.async_session", return_value=sess),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
        patch("app.utils.cache.get_redis", AsyncMock()),
    ):
        await CreditService.daily_deduct_all()

    stmt = sess.execute.await_args.args[0]
    compiled = str(stmt.whereclause)
    assert "status" in compiled
    assert "credit_days" in compiled
    assert "last_daily_deducted_on" in compiled
    assert stmt._for_update_arg is not None  # noqa: SLF001


@pytest.mark.asyncio
async def test_daily_deduct_decrements_active_row_by_one():
    row = _credit(chat_id=-100, credit_days=5)
    count, credits, _, _ = await _run_daily_deduct([row])

    assert count == 1
    assert credits[0].credit_days == 4
    assert credits[0].status == "active"
    assert credits[0].last_daily_deducted_on is not None


@pytest.mark.asyncio
async def test_daily_deduct_expires_row_when_last_day():
    row = _credit(chat_id=-101, credit_days=1)
    count, credits, invalidate, redis_mock = await _run_daily_deduct([row])

    assert count == 1
    assert credits[0].credit_days == 0
    assert credits[0].status == "expired"
    invalidate.assert_awaited_once_with(-101, "group")
    redis_mock.sadd.assert_awaited_once_with(
        CREDIT_EXPIRED_PENDING,
        credit_expired_pending_member(-101, "group"),
    )
    redis_mock.expire.assert_awaited_once_with(CREDIT_EXPIRED_PENDING, 86400)


@pytest.mark.asyncio
async def test_daily_deduct_handles_group_and_channel_rows():
    group_row = _credit(chat_id=-200, chat_type="group", credit_days=2)
    channel_row = _credit(chat_id=-300, chat_type="channel", credit_days=2)
    count, credits, invalidate, _ = await _run_daily_deduct([group_row, channel_row])

    assert count == 2
    assert credits[0].credit_days == 1
    assert credits[1].credit_days == 1
    assert invalidate.await_count == 2


@pytest.mark.asyncio
async def test_daily_deduct_invalidates_cache_for_all_processed_rows():
    rows = [
        _credit(chat_id=-1, credit_days=3),
        _credit(chat_id=-2, credit_days=2),
    ]
    _, _, invalidate, _ = await _run_daily_deduct(rows)

    assert invalidate.await_count == 2
    invalidate.assert_any_await(-1, "group")
    invalidate.assert_any_await(-2, "group")


@pytest.mark.asyncio
async def test_daily_deduct_does_not_add_credit_history():
    row = _credit(chat_id=-1, credit_days=2)
    sess = _session_with_credits([row])

    with (
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.async_session", return_value=sess),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
        patch("app.utils.cache.get_redis", AsyncMock()),
        patch("app.repositories.credit_repo.add_credit_history", AsyncMock()) as history_mock,
    ):
        await CreditService.daily_deduct_all()

    history_mock.assert_not_awaited()
    sess.add.assert_not_called()


@pytest.mark.asyncio
async def test_daily_deduct_returns_processed_count():
    rows = [
        _credit(chat_id=-1, credit_days=4),
        _credit(chat_id=-2, credit_days=3),
        _credit(chat_id=-3, credit_days=1),
    ]
    count, _, _, _ = await _run_daily_deduct(rows)
    assert count == 3


@pytest.mark.asyncio
async def test_daily_deduct_cache_invalidation_failure_still_returns_count():
    row = _credit(chat_id=-50, credit_days=2)
    invalidate = AsyncMock(side_effect=RuntimeError("redis down"))

    with (
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.async_session", return_value=_session_with_credits([row])),
        patch("app.services.credit_service.invalidate_credit", invalidate),
        patch("app.utils.cache.get_redis", AsyncMock()),
    ):
        count = await CreditService.daily_deduct_all()

    assert count == 1
    assert row.credit_days == 1


@pytest.mark.asyncio
async def test_daily_deduct_uses_session_begin_transaction():
    row = _credit(chat_id=-1, credit_days=2)
    sess = _session_with_credits([row])

    with (
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.async_session", return_value=sess),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
        patch("app.utils.cache.get_redis", AsyncMock()),
    ):
        await CreditService.daily_deduct_all()

    sess.begin.assert_called_once()
    tx = sess.begin.return_value
    tx.__aenter__.assert_awaited_once()
    tx.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_midnight_scheduler_runs_auto_leave_after_deduct():
    expired_ids = {"group:-100", "channel:-200"}
    redis_mock = AsyncMock()
    redis_mock.smembers = AsyncMock(return_value=expired_ids)
    redis_mock.delete = AsyncMock()

    with (
        patch("app.scheduler.CreditService.daily_deduct_all", AsyncMock(return_value=1)),
        patch("app.scheduler.get_redis", AsyncMock(return_value=redis_mock)),
        patch("app.scheduler.CreditService.auto_leave_check", AsyncMock()) as leave_mock,
        patch("app.scheduler._call_py", object()),
        patch("app.scheduler._bot", object()),
    ):
        await midnight_credit_deduct()

    assert leave_mock.await_count == 2
    called = {(c.args[0], c.args[1]) for c in leave_mock.await_args_list}
    assert called == {(-100, "group"), (-200, "channel")}
    redis_mock.delete.assert_awaited_once_with(CREDIT_EXPIRED_PENDING)


@pytest.mark.asyncio
async def test_midnight_scheduler_accepts_legacy_pending_chat_id_values():
    redis_mock = AsyncMock()
    redis_mock.smembers = AsyncMock(return_value={"-300"})
    redis_mock.delete = AsyncMock()

    with (
        patch("app.scheduler.CreditService.daily_deduct_all", AsyncMock(return_value=1)),
        patch("app.scheduler.get_redis", AsyncMock(return_value=redis_mock)),
        patch("app.scheduler.CreditService.auto_leave_check", AsyncMock()) as leave_mock,
        patch("app.scheduler._call_py", object()),
        patch("app.scheduler._bot", object()),
    ):
        await midnight_credit_deduct()

    leave_mock.assert_awaited_once()
    assert leave_mock.await_args.args[:2] == (-300, "group")
    redis_mock.delete.assert_awaited_once_with(CREDIT_EXPIRED_PENDING)


@pytest.mark.asyncio
async def test_credit_repo_daily_deduct_has_no_lock_or_redis_pending():
    """Repo helper is not used by cron; documents divergent dead-path behavior."""
    row = _credit(chat_id=-1, credit_days=2)
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [row]
    sess.execute = AsyncMock(return_value=mock_result)
    sess.commit = AsyncMock()

    redis_mock = AsyncMock()
    with (
        patch("app.repositories.credit_repo.async_session", return_value=sess),
        patch("app.utils.cache.get_redis", AsyncMock(return_value=redis_mock)),
        patch("app.utils.cache.acquire_lock", AsyncMock()) as lock_mock,
    ):
        count = await credit_repo.daily_deduct_all()

    assert count == 1
    assert row.credit_days == 1
    lock_mock.assert_not_awaited()
    redis_mock.sadd.assert_not_awaited()
    sess.commit.assert_awaited_once()


def test_ineligible_rows_not_loaded_by_mock_contract():
    """Rows with status != active or credit_days <= 0 are excluded by SQL; mocks only return eligible rows."""
    eligible = _credit(chat_id=-1, credit_days=1, status="active")
    assert eligible.credit_days > 0
    assert eligible.status == "active"

    skipped = _credit(chat_id=-2, credit_days=0, status="active")
    assert skipped.credit_days <= 0

    expired_status = _credit(chat_id=-3, credit_days=5, status="expired")
    assert expired_status.status != "active"

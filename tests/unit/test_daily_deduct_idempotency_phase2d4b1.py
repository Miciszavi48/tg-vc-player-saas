from __future__ import annotations

import importlib
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
from app.services.credit_service import CreditService


def _credit(
    *,
    row_id: int = 1,
    chat_id: int = -1,
    chat_type: str = "group",
    credit_days: int = 2,
    status: str = "active",
    last_daily_deducted_on: date | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        chat_id=chat_id,
        chat_type=chat_type,
        credit_days=credit_days,
        status=status,
        last_daily_deducted_on=last_daily_deducted_on,
    )


def test_migration_0019_module_exists():
    mod = importlib.import_module(
        "app.database.migrations.versions.0019_group_credit_daily_deduct_idempotency",
    )
    assert mod.revision == "0019_daily_deduct_idem"
    assert mod.down_revision == "0018_credit_hist_orphan"


def test_eligible_filters_include_last_daily_deducted_on():
    today = date(2026, 6, 5)
    filters = CreditService._daily_deduct_eligible_filters(today)
    compiled = " ".join(str(f) for f in filters)
    assert "last_daily_deducted_on" in compiled


def test_process_row_sets_last_daily_deducted_on():
    today = date(2026, 6, 5)
    row = _credit(credit_days=3, last_daily_deducted_on=None)
    CreditService._process_daily_deduct_row(row, today)
    assert row.last_daily_deducted_on == today
    assert row.credit_days == 2


def test_process_row_expires_last_day_and_stamps_date():
    today = date(2026, 6, 5)
    row = _credit(credit_days=1)
    assert CreditService._process_daily_deduct_row(row, today) is True
    assert row.credit_days == 0
    assert row.status == "expired"
    assert row.last_daily_deducted_on == today


@pytest.mark.asyncio
async def test_legacy_second_run_same_day_processes_zero_rows():
    today = date(2026, 6, 5)
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    sess.begin.return_value = tx
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    sess.execute = AsyncMock(return_value=mock_result)

    with (
        patch("app.services.credit_service.CreditService._daily_deduct_today", return_value=today),
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.async_session", return_value=sess),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
    ):
        count = await CreditService._daily_deduct_legacy()

    assert count == 0
    compiled = str(sess.execute.await_args.args[0].whereclause)
    assert "last_daily_deducted_on" in compiled


@pytest.mark.asyncio
async def test_legacy_processes_row_deducted_yesterday():
    today = date(2026, 6, 5)
    yesterday = date(2026, 6, 4)
    row = _credit(credit_days=2, last_daily_deducted_on=yesterday)
    sess = MagicMock()
    sess.__aenter__ = AsyncMock(return_value=sess)
    sess.__aexit__ = AsyncMock(return_value=False)
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    sess.begin.return_value = tx
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [row]
    sess.execute = AsyncMock(return_value=mock_result)

    with (
        patch("app.services.credit_service.CreditService._daily_deduct_today", return_value=today),
        patch("app.services.credit_service.acquire_lock", AsyncMock(return_value="tok")),
        patch("app.services.credit_service.release_lock", AsyncMock()),
        patch("app.services.credit_service.async_session", return_value=sess),
        patch("app.services.credit_service.invalidate_credit", AsyncMock()),
        patch("app.utils.cache.get_redis", AsyncMock()),
    ):
        count = await CreditService._daily_deduct_legacy()

    assert count == 1
    assert row.credit_days == 1
    assert row.last_daily_deducted_on == today


@pytest.mark.asyncio
async def test_group_credit_model_has_last_daily_deducted_on():
    from app.database.models import GroupCredit

    assert "last_daily_deducted_on" in GroupCredit.__table__.columns

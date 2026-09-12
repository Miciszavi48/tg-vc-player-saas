"""Install flow + trial + credit integration tests.

Uses the same fixture pattern as test_credit_service.py to avoid
event-loop contamination.
"""
from __future__ import annotations

import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings
from app.database.models import Base, CreditHistory, GroupCredit

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest_asyncio.fixture
async def install_engine():
    eng = create_async_engine(os.environ["DATABASE_URL"], echo=False, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def install_sf(install_engine):
    return async_sessionmaker(install_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def install_env(install_sf):
    import app.utils.cache as cache_mod
    engine_mod = sys.modules["app.database.engine"]
    orig_s = engine_mod.async_session
    orig_r = cache_mod._redis
    engine_mod.async_session = install_sf
    try:
        from app.services.credit_service import CreditService
        yield CreditService
    finally:
        cache_mod._redis = orig_r
        engine_mod.async_session = orig_s


async def _cleanup(sf, chat_id):
    async with sf() as s:
        async with s.begin():
            await s.execute(delete(CreditHistory).where(CreditHistory.chat_id == chat_id))
            await s.execute(delete(GroupCredit).where(GroupCredit.chat_id == chat_id))


@pytest.mark.asyncio
class TestInstallTrialCredit:
    async def test_trial_creates_credit(self, install_env, install_sf):
        cid = -600101
        try:
            cr = await install_env.activate_trial(cid, "group")
            assert cr.is_trial is True
            assert cr.credit_days > 0
            assert cr.status == "active"
        finally:
            await _cleanup(install_sf, cid)

    async def test_trial_once_only(self, install_env, install_sf):
        cid = -600102
        try:
            await install_env.activate_trial(cid, "group")
            with pytest.raises(ValueError, match="Trial already used"):
                await install_env.activate_trial(cid, "group")
        finally:
            await _cleanup(install_sf, cid)

    async def test_charge_adds_days(self, install_env, install_sf):
        cid = -600103
        try:
            await install_env.charge(
                cid, "group", 10, operated_by=settings.DEVELOPER_ID,
            )
            cr = await install_env.charge(
                cid, "group", 5, operated_by=settings.DEVELOPER_ID,
            )
            assert cr.credit_days == 15
        finally:
            await _cleanup(install_sf, cid)

    async def test_deduct_to_zero_expires(self, install_env, install_sf):
        cid = -600104
        try:
            await install_env.charge(
                cid, "group", 5, operated_by=settings.DEVELOPER_ID,
            )
            cr = await install_env.deduct(
                cid, 5, operated_by=settings.DEVELOPER_ID,
            )
            assert cr.credit_days == 0
            assert cr.status == "expired"
        finally:
            await _cleanup(install_sf, cid)

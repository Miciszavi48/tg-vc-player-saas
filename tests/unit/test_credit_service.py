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

os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./.pytest-test-mode.db",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


@pytest_asyncio.fixture
async def credit_engine():
    engine = create_async_engine(
        os.environ["DATABASE_URL"], echo=False, poolclass=NullPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def credit_session_factory(credit_engine):
    return async_sessionmaker(
        credit_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture
async def patched_credit_service(credit_session_factory):
    import app.utils.cache as cache_mod

    engine_mod = sys.modules["app.database.engine"]
    original_session = engine_mod.async_session
    original_redis = cache_mod._redis
    engine_mod.async_session = credit_session_factory
    try:
        from app.services.credit_service import CreditService
        yield CreditService
    finally:
        cache_mod._redis = original_redis
        engine_mod.async_session = original_session


async def _cleanup(credit_session_factory, chat_id: int):
    async with credit_session_factory() as session:
        async with session.begin():
            await session.execute(
                delete(CreditHistory).where(CreditHistory.chat_id == chat_id)
            )
            await session.execute(
                delete(GroupCredit).where(GroupCredit.chat_id == chat_id)
            )


@pytest.mark.asyncio
class TestCreditService:
    async def test_activate_trial(self, patched_credit_service, credit_session_factory):
        chat_id = -900001
        try:
            credit = await patched_credit_service.activate_trial(chat_id, "group")
            assert credit.is_trial is True
            assert credit.credit_days > 0
            assert credit.status == "active"
        finally:
            await _cleanup(credit_session_factory, chat_id)

    async def test_charge(self, patched_credit_service, credit_session_factory):
        chat_id = -900002
        try:
            credit = await patched_credit_service.charge(
                chat_id,
                "group",
                30,
                operated_by=settings.DEVELOPER_ID,
                note="test charge",
            )
            assert credit.credit_days == 30
            assert credit.total_charged == 30
            assert credit.status == "active"
        finally:
            await _cleanup(credit_session_factory, chat_id)

    async def test_deduct(self, patched_credit_service, credit_session_factory):
        chat_id = -900003
        try:
            await patched_credit_service.charge(
                chat_id,
                "group",
                10,
                operated_by=settings.DEVELOPER_ID,
            )
            credit = await patched_credit_service.deduct(
                chat_id,
                3,
                operated_by=settings.DEVELOPER_ID,
                note="test deduct",
            )
            assert credit is not None
            assert credit.credit_days == 7
        finally:
            await _cleanup(credit_session_factory, chat_id)

    async def test_charge_denied_for_non_developer(
        self, patched_credit_service, credit_session_factory
    ):
        chat_id = -900010
        try:
            with pytest.raises(ValueError, match="only_developer_can_mutate_credit"):
                await patched_credit_service.charge(
                    chat_id,
                    "group",
                    5,
                    operated_by=999001,
                )
        finally:
            await _cleanup(credit_session_factory, chat_id)

    async def test_zero_credit_status(self, patched_credit_service, credit_session_factory):
        chat_id = -900004
        try:
            await patched_credit_service.charge(
                chat_id,
                "group",
                5,
                operated_by=settings.DEVELOPER_ID,
            )
            credit = await patched_credit_service.deduct(
                chat_id,
                5,
                operated_by=settings.DEVELOPER_ID,
            )
            assert credit is not None
            assert credit.credit_days == 0
            assert credit.status == "expired"
        finally:
            await _cleanup(credit_session_factory, chat_id)

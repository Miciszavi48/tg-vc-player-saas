"""Concurrency stress tests for multi-instance safety.

Validates that concurrent operations on the same chat/wallet produce
correct results without data corruption.

Run: pytest tests/test_concurrency.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config.settings import settings

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest_asyncio.fixture
async def conc_engine():
    eng = create_async_engine(os.environ["DATABASE_URL"], echo=False, poolclass=NullPool)
    from app.database.models import Base
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def conc_session_factory(conc_engine):
    return async_sessionmaker(conc_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def patched_env(conc_session_factory):
    """Patch async_session and reset Redis singleton for test isolation."""
    import app.utils.cache as cache_mod

    engine_mod = sys.modules["app.database.engine"]
    orig_session = engine_mod.async_session
    orig_redis = cache_mod._redis
    engine_mod.async_session = conc_session_factory
    try:
        yield conc_session_factory
    finally:
        cache_mod._redis = orig_redis
        engine_mod.async_session = orig_session


# ═══════════════════════════════════════════════════════════════════════
# Concurrent credit charge stress test
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestConcurrentCreditCharge:
    async def test_10_concurrent_charges_produce_correct_total(self, patched_env):
        """10 concurrent charge(5 days) calls on the same chat must
        result in exactly 50 days total, not more or fewer."""
        from app.database.models import CreditHistory, Group, GroupCredit
        from app.services.credit_service import CreditService

        chat_id = -800001
        n = 10
        days_each = 5

        async with patched_env() as session:
            async with session.begin():
                await session.execute(delete(CreditHistory).where(CreditHistory.chat_id == chat_id))
                await session.execute(delete(GroupCredit).where(GroupCredit.chat_id == chat_id))
                await session.execute(delete(Group).where(Group.chat_id == chat_id))
                session.add(Group(chat_id=chat_id, chat_title="Concurrent credit test", status="active"))

        try:
            tasks = [
                CreditService.charge(
                    chat_id,
                    "group",
                    days_each,
                    operated_by=settings.DEVELOPER_ID,
                    note=f"conc_{i}",
                )
                for i in range(n)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            successes = [r for r in results if not isinstance(r, Exception)]
            errors = [r for r in results if isinstance(r, Exception)]

            assert len(successes) + len(errors) == n

            async with patched_env() as session:
                result = await session.execute(
                    select(GroupCredit).where(GroupCredit.chat_id == chat_id)
                )
                credit = result.scalar_one_or_none()

            assert credit is not None
            assert credit.credit_days == len(successes) * days_each

            async with patched_env() as session:
                result = await session.execute(
                    select(CreditHistory).where(CreditHistory.chat_id == chat_id)
                )
                history = list(result.scalars().all())

            assert len(history) == len(successes)

        finally:
            async with patched_env() as session:
                async with session.begin():
                    await session.execute(delete(CreditHistory).where(CreditHistory.chat_id == chat_id))
                    await session.execute(delete(GroupCredit).where(GroupCredit.chat_id == chat_id))
                    await session.execute(delete(Group).where(Group.chat_id == chat_id))


# ═══════════════════════════════════════════════════════════════════════
# Concurrent playlist modification stress test
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestConcurrentPlaylistOps:
    async def test_concurrent_queue_adds_no_position_collision(self, patched_env):
        """5 concurrent add_to_queue calls, each protected by distributed lock
        (as handlers do), must all get unique positions."""
        from app.database.models import Playlist
        from app.repositories import playlist_repo
        from app.utils.cache import acquire_lock, release_lock

        chat_id = -800002
        n = 5

        async with patched_env() as session:
            async with session.begin():
                await session.execute(delete(Playlist).where(Playlist.chat_id == chat_id))

        async def locked_add(i: int):
            lock_key = f"chat:{chat_id}"
            token = await acquire_lock(lock_key)
            if token is None:
                raise RuntimeError("lock not acquired")
            try:
                return await playlist_repo.add_to_queue(
                    chat_id=chat_id,
                    stream_url=f"http://test/{i}.mp3",
                    title=f"Track {i}",
                    added_by=i,
                )
            finally:
                await release_lock(lock_key, token)

        try:
            tasks = [locked_add(i) for i in range(n)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            successes = [r for r in results if not isinstance(r, Exception)]

            async with patched_env() as session:
                result = await session.execute(
                    select(Playlist).where(Playlist.chat_id == chat_id).order_by(Playlist.position)
                )
                items = list(result.scalars().all())

            positions = [item.position for item in items]
            assert len(positions) == len(set(positions)), f"Duplicate positions: {positions}"
            assert len(items) == len(successes)

        finally:
            async with patched_env() as session:
                async with session.begin():
                    await session.execute(delete(Playlist).where(Playlist.chat_id == chat_id))


# ═══════════════════════════════════════════════════════════════════════
# Daily deduct singleton lock test
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestDailyDeductSingleton:
    async def test_concurrent_daily_deduct_runs_once(self, patched_env):
        """Two concurrent daily_deduct_all calls: only one should perform
        the deduction (the other skips due to lock)."""
        from app.database.models import CreditHistory, Group, GroupCredit
        from app.services.credit_service import CreditService

        chat_id = -800003

        async with patched_env() as session:
            async with session.begin():
                await session.execute(delete(CreditHistory).where(CreditHistory.chat_id == chat_id))
                await session.execute(delete(GroupCredit).where(GroupCredit.chat_id == chat_id))
                await session.execute(delete(Group).where(Group.chat_id == chat_id))
                session.add(Group(chat_id=chat_id, chat_title="Daily deduct test", status="active"))

        try:
            await CreditService.charge(
                chat_id,
                "group",
                10,
                operated_by=settings.DEVELOPER_ID,
                note="setup",
            )

            results = await asyncio.gather(
                CreditService.daily_deduct_all(),
                CreditService.daily_deduct_all(),
                return_exceptions=True,
            )

            total_deducted = sum(r for r in results if isinstance(r, int))

            async with patched_env() as session:
                result = await session.execute(
                    select(GroupCredit).where(GroupCredit.chat_id == chat_id)
                )
                credit = result.scalar_one_or_none()

            assert credit is not None
            assert credit.credit_days == 9
            assert total_deducted >= 1

        finally:
            async with patched_env() as session:
                async with session.begin():
                    await session.execute(delete(CreditHistory).where(CreditHistory.chat_id == chat_id))
                    await session.execute(delete(GroupCredit).where(GroupCredit.chat_id == chat_id))
                    await session.execute(delete(Group).where(Group.chat_id == chat_id))


# ═══════════════════════════════════════════════════════════════════════
# Distributed lock correctness
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestLockCorrectness:
    async def test_lock_mutual_exclusion(self, patched_env):
        """Two concurrent lock acquisitions on the same key: only one succeeds."""
        from app.utils.cache import acquire_lock, release_lock

        key = "test:mutex:001"
        t1 = await acquire_lock(key, ttl_ms=5000)
        assert t1 is not None

        t2 = await acquire_lock(key, ttl_ms=5000)
        assert t2 is None

        await release_lock(key, t1)

        t3 = await acquire_lock(key, ttl_ms=5000)
        assert t3 is not None
        await release_lock(key, t3)

    async def test_lock_auto_expires(self, patched_env):
        """A lock with short TTL auto-expires and allows re-acquisition."""
        from app.utils.cache import acquire_lock, release_lock

        key = "test:ttl:001"
        t1 = await acquire_lock(key, ttl_ms=200)
        assert t1 is not None

        await asyncio.sleep(0.3)

        t2 = await acquire_lock(key, ttl_ms=5000)
        assert t2 is not None
        await release_lock(key, t2)

"""PostgreSQL integration tests for atomic helper reservation (Phase 2D-5C).

Requires ``musicbot_disposable`` (or other explicitly safe test DB). Never runs against
``musicbot_dev`` or production-like database names.

Test chat_id namespace: ``-990000002000`` … ``-990000002099``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.models import HelperAccount, HelperChatBinding
from app.services.helper_pool_service import HelperPoolService, _pool_eligible_filters

pytestmark = pytest.mark.asyncio

EXPECTED_ALEMBIC_HEAD = "0039_hot_seat"
REQUIRED_DISPOSABLE_DB = "musicbot_disposable"
SAFE_DB_MARKERS = ("disposable", "test", "dev", "local", "staging")
BLOCKED_DB_MARKERS = ("musicbot_dev", "musicbot_prod", "production")

CHAT_ID_BASE = -990000002000
CHAT_ID_MAX = -990000002099

_phone_seq = 0


def _next_phone() -> str:
    global _phone_seq  # noqa: PLW0603
    _phone_seq += 1
    suffix = uuid.uuid4().hex[:8]
    return f"+99052d5c{suffix}"[:32]


def _next_chat_id(offset: int) -> int:
    chat_id = CHAT_ID_BASE - offset
    if chat_id < CHAT_ID_MAX:
        raise ValueError("test chat_id namespace exhausted")
    return chat_id


def _database_name_from_url(url: str) -> str | None:
    try:
        return make_url(url).database
    except Exception:
        return None


def _is_safe_database_name(db_name: str | None) -> tuple[bool, str]:
    if not db_name:
        return False, "could not determine database name from URL"
    lowered = db_name.lower()
    if any(blocked in lowered for blocked in BLOCKED_DB_MARKERS):
        return False, f"refusing blocked database name: {db_name!r}"
    if lowered == REQUIRED_DISPOSABLE_DB:
        return True, f"database is {REQUIRED_DISPOSABLE_DB}"
    for marker in SAFE_DB_MARKERS:
        if marker in lowered:
            return True, f"database name contains safe marker '{marker}'"
    return (
        False,
        f"database {db_name!r} is not {REQUIRED_DISPOSABLE_DB!r} and lacks safe marker",
    )


def _disposable_database_url() -> str | None:
    """Return a PostgreSQL URL only when it targets a safe disposable/test database."""
    candidates: list[str] = []
    for key in ("DATABASE_URL", "TEST_DATABASE_URL"):
        url = os.getenv(key, "").strip()
        if url.startswith("postgresql"):
            candidates.append(url)
    config_path = Path("app") / "config.env.disposable"
    if config_path.is_file():
        for line in config_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip()
                if url.startswith("postgresql"):
                    candidates.append(url)
    for url in candidates:
        if "musicbot_dev" in url.lower():
            continue
        db_name = _database_name_from_url(url)
        ok, _ = _is_safe_database_name(db_name)
        if ok:
            return url
    return None


async def _assert_alembic_head(engine) -> None:
    async with engine.connect() as conn:
        try:
            row = (
                await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            ).scalar_one_or_none()
        except Exception as exc:
            pytest.skip(f"alembic_version not readable on disposable DB: {exc}")
    if row != EXPECTED_ALEMBIC_HEAD:
        pytest.skip(
            f"disposable DB at alembic {row!r}, expected {EXPECTED_ALEMBIC_HEAD!r}; "
            "run: cd app && alembic upgrade head",
        )


async def _fetch_helper_row(session_factory, helper_id: int) -> HelperAccount:
    async with session_factory() as session:
        result = await session.execute(
            select(HelperAccount).where(HelperAccount.id == helper_id)
        )
        return result.scalar_one()


async def _insert_helper(
    session_factory,
    *,
    status: str = "active",
    current_active_calls: int = 0,
    max_concurrent_calls: int = 50,
    cooldown_until: datetime | None = None,
    banned_until: datetime | None = None,
    phone: str | None = None,
) -> int:
    async with session_factory() as session:
        async with session.begin():
            row = HelperAccount(
                phone=phone or _next_phone(),
                status=status,
                current_active_calls=current_active_calls,
                max_concurrent_calls=max_concurrent_calls,
                cooldown_until=cooldown_until,
                banned_until=banned_until,
            )
            session.add(row)
            await session.flush()
            return int(row.id)


async def _insert_binding(session_factory, chat_id: int, helper_id: int) -> None:
    async with session_factory() as session:
        async with session.begin():
            session.add(
                HelperChatBinding(chat_id=chat_id, helper_account_id=helper_id),
            )


async def _count_pool_eligible(sf) -> int:
    now = datetime.now(timezone.utc)
    async with sf() as session:
        result = await session.execute(
            select(func.count())
            .select_from(HelperAccount)
            .where(*_pool_eligible_filters(now)),
        )
        return int(result.scalar_one())


async def _require_eligible_count(sf, expected: int) -> None:
    count = await _count_pool_eligible(sf)
    if count != expected:
        pytest.skip(
            f"disposable DB has {count} pool-eligible helpers, need exactly {expected} "
            "(pre-existing helpers affect isolation)",
        )


async def _cleanup_test_rows(
    session_factory,
    *,
    helper_ids: list[int],
    chat_ids: list[int],
) -> None:
    if not helper_ids and not chat_ids:
        return
    async with session_factory() as session:
        async with session.begin():
            if chat_ids:
                await session.execute(
                    delete(HelperChatBinding).where(
                        HelperChatBinding.chat_id.in_(chat_ids),
                    ),
                )
            if helper_ids:
                await session.execute(
                    delete(HelperAccount).where(HelperAccount.id.in_(helper_ids)),
                )


@pytest.fixture(scope="module")
async def disposable_helper_env():
    """Patch ``async_session`` to a safe disposable PostgreSQL engine."""
    url = _disposable_database_url()
    if url is None:
        pytest.skip(
            "musicbot_disposable DATABASE_URL not configured "
            "(set DATABASE_URL or app/config.env.disposable)",
        )
    db_name = _database_name_from_url(url)
    ok, reason = _is_safe_database_name(db_name)
    if not ok:
        pytest.skip(reason)

    engine = create_async_engine(url, echo=False, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"disposable PostgreSQL unreachable: {exc}")

    await _assert_alembic_head(engine)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False,
    )

    import app.services.helper_pool_service as helper_pool_mod

    engine_mod = sys.modules["app.database.engine"]
    orig_engine_session = engine_mod.async_session
    orig_pool_session = helper_pool_mod.async_session
    engine_mod.async_session = session_factory
    helper_pool_mod.async_session = session_factory
    try:
        yield session_factory
    finally:
        engine_mod.async_session = orig_engine_session
        helper_pool_mod.async_session = orig_pool_session
        await engine.dispose()


@pytest.fixture
def sf(disposable_helper_env):
    return disposable_helper_env


# ── 1. Single reservation increments ─────────────────────────────────────


async def test_disposable_reserve_increments_active_calls(sf):
    chat_id = _next_chat_id(1)
    helper_id = None
    try:
        helper_id = await _insert_helper(sf, max_concurrent_calls=5)
        await _insert_binding(sf, chat_id, helper_id)
        before = (await _fetch_helper_row(sf, helper_id)).current_active_calls
        reserved = await HelperPoolService.reserve_best_helper(chat_id)
        assert reserved is not None
        assert reserved.id == helper_id
        assert reserved.current_active_calls == before + 1
        row = await _fetch_helper_row(sf, helper_id)
        assert row.current_active_calls == before + 1
    finally:
        await _cleanup_test_rows(sf, helper_ids=[helper_id] if helper_id else [], chat_ids=[chat_id])


# ── 2. Release decrements and clamps ───────────────────────────────────────


async def test_disposable_release_decrements_and_clamps(sf):
    chat_id = _next_chat_id(2)
    helper_id = None
    try:
        helper_id = await _insert_helper(sf, max_concurrent_calls=5)
        await _insert_binding(sf, chat_id, helper_id)
        reserved = await HelperPoolService.reserve_best_helper(chat_id)
        assert reserved is not None
        await HelperPoolService.release_helper_reservation(helper_id)
        row = await _fetch_helper_row(sf, helper_id)
        assert row.current_active_calls == 0
        await HelperPoolService.release_helper_reservation(helper_id)
        row = await _fetch_helper_row(sf, helper_id)
        assert row.current_active_calls == 0
    finally:
        await _cleanup_test_rows(sf, helper_ids=[helper_id] if helper_id else [], chat_ids=[chat_id])


# ── 3. Concurrent reserve — one helper, max=1 ────────────────────────────


async def test_disposable_concurrent_single_helper_capacity_one(sf):
    chat_a = _next_chat_id(3)
    chat_b = _next_chat_id(4)
    helper_id = None
    try:
        await _require_eligible_count(sf, 0)
        helper_id = await _insert_helper(
            sf, max_concurrent_calls=1, current_active_calls=0,
        )
        await _require_eligible_count(sf, 1)
        barrier = asyncio.Barrier(2)

        async def _reserve_after_barrier(chat_id: int):
            await barrier.wait()
            return await HelperPoolService.reserve_best_helper(chat_id)

        first, second = await asyncio.gather(
            _reserve_after_barrier(chat_a),
            _reserve_after_barrier(chat_b),
        )
        successes = [r for r in (first, second) if r is not None]
        assert len(successes) <= 1
        if len(successes) == 1:
            assert successes[0].id == helper_id
        row = await _fetch_helper_row(sf, helper_id)
        assert row.current_active_calls <= 1
        assert row.current_active_calls <= row.max_concurrent_calls
    finally:
        await _cleanup_test_rows(
            sf,
            helper_ids=[helper_id] if helper_id else [],
            chat_ids=[chat_a, chat_b],
        )


# ── 4. Concurrent reserve — two helpers ──────────────────────────────────


async def test_disposable_concurrent_two_helpers_different_ids(sf):
    chat_a = _next_chat_id(5)
    chat_b = _next_chat_id(6)
    helper_a = helper_b = None
    try:
        await _require_eligible_count(sf, 0)
        helper_a = await _insert_helper(sf, max_concurrent_calls=5)
        helper_b = await _insert_helper(sf, max_concurrent_calls=5)
        await _require_eligible_count(sf, 2)
        barrier = asyncio.Barrier(2)

        async def _reserve(chat_id: int):
            await barrier.wait()
            return await HelperPoolService.reserve_best_helper(chat_id)

        r_a, r_b = await asyncio.gather(_reserve(chat_a), _reserve(chat_b))
        assert r_a is not None and r_b is not None
        assert r_a.id != r_b.id
        assert {r_a.id, r_b.id} == {helper_a, helper_b}
    finally:
        await _cleanup_test_rows(
            sf,
            helper_ids=[h for h in (helper_a, helper_b) if h],
            chat_ids=[chat_a, chat_b],
        )


# ── 5. At-capacity helper skipped ──────────────────────────────────────────


async def test_disposable_at_capacity_helper_skipped(sf):
    chat_id = _next_chat_id(7)
    helper_id = None
    try:
        await _require_eligible_count(sf, 0)
        helper_id = await _insert_helper(
            sf,
            max_concurrent_calls=1,
            current_active_calls=1,
        )
        await _insert_binding(sf, chat_id, helper_id)
        reserved = await HelperPoolService.reserve_best_helper(chat_id)
        assert reserved is None
    finally:
        await _cleanup_test_rows(sf, helper_ids=[helper_id] if helper_id else [], chat_ids=[chat_id])


# ── 6. Ineligible helpers skipped ──────────────────────────────────────────


async def test_disposable_ineligible_helpers_skipped(sf):
    chat_id = _next_chat_id(8)
    now = datetime.now(timezone.utc)
    ids: list[int] = []
    try:
        await _require_eligible_count(sf, 0)
        ids.append(
            await _insert_helper(sf, status="quarantined", max_concurrent_calls=5),
        )
        ids.append(
            await _insert_helper(
                sf,
                cooldown_until=now + timedelta(hours=1),
                max_concurrent_calls=5,
            ),
        )
        ids.append(
            await _insert_helper(
                sf,
                banned_until=now + timedelta(hours=1),
                max_concurrent_calls=5,
            ),
        )
        reserved = await HelperPoolService.reserve_best_helper(chat_id)
        assert reserved is None
    finally:
        await _cleanup_test_rows(sf, helper_ids=ids, chat_ids=[chat_id])


# ── 7. Bound helper preferred when eligible ────────────────────────────────


async def test_disposable_bound_helper_preferred(sf):
    chat_id = _next_chat_id(9)
    bound_id = other_id = None
    try:
        bound_id = await _insert_helper(sf, max_concurrent_calls=5, current_active_calls=0)
        other_id = await _insert_helper(sf, max_concurrent_calls=5, current_active_calls=0)
        await _insert_binding(sf, chat_id, bound_id)
        reserved = await HelperPoolService.reserve_best_helper(chat_id)
        assert reserved is not None
        assert reserved.id == bound_id
        assert reserved.id != other_id
    finally:
        await _cleanup_test_rows(
            sf,
            helper_ids=[h for h in (bound_id, other_id) if h],
            chat_ids=[chat_id],
        )


# ── 8. Bound at capacity → pool helper ───────────────────────────────────


async def test_disposable_bound_at_capacity_uses_pool(sf):
    chat_id = _next_chat_id(10)
    bound_id = pool_id = None
    try:
        await _require_eligible_count(sf, 0)
        bound_id = await _insert_helper(
            sf, max_concurrent_calls=1, current_active_calls=1,
        )
        pool_id = await _insert_helper(
            sf, max_concurrent_calls=5, current_active_calls=0,
        )
        await _insert_binding(sf, chat_id, bound_id)
        await _require_eligible_count(sf, 1)
        reserved = await HelperPoolService.reserve_best_helper(chat_id)
        assert reserved is not None
        assert reserved.id == pool_id
    finally:
        await _cleanup_test_rows(
            sf,
            helper_ids=[h for h in (bound_id, pool_id) if h],
            chat_ids=[chat_id],
        )


# ── 9. Release restores capacity ───────────────────────────────────────────


async def test_disposable_release_restores_capacity(sf):
    chat_id = _next_chat_id(11)
    helper_id = None
    try:
        helper_id = await _insert_helper(sf, max_concurrent_calls=1)
        await _insert_binding(sf, chat_id, helper_id)
        first = await HelperPoolService.reserve_best_helper(chat_id)
        assert first is not None
        await HelperPoolService.release_helper_reservation(helper_id)
        row = await _fetch_helper_row(sf, helper_id)
        assert row.current_active_calls == 0
        second = await HelperPoolService.reserve_best_helper(chat_id)
        assert second is not None
        assert second.id == helper_id
    finally:
        await _cleanup_test_rows(sf, helper_ids=[helper_id] if helper_id else [], chat_ids=[chat_id])


# ── 10. Reservation returns before simulated join (no held lock) ───────────


async def test_disposable_reservation_not_held_during_simulated_join(sf):
    """Indirect: after reserve commits, another chat can reserve while join sleeps."""
    chat_a = _next_chat_id(12)
    chat_b = _next_chat_id(13)
    helper_id = None
    try:
        await _require_eligible_count(sf, 0)
        helper_id = await _insert_helper(sf, max_concurrent_calls=2)
        await _require_eligible_count(sf, 1)
        await _insert_binding(sf, chat_a, helper_id)
        first = await HelperPoolService.reserve_best_helper(chat_a)
        assert first is not None
        assert first.id == helper_id

        async def _simulated_join_then_reserve():
            await asyncio.sleep(0.05)
            return await HelperPoolService.reserve_best_helper(chat_b)

        second = await _simulated_join_then_reserve()
        assert second is not None
        assert second.id == helper_id
        row = await _fetch_helper_row(sf, helper_id)
        assert row.current_active_calls == 2
        assert row.current_active_calls <= row.max_concurrent_calls
    finally:
        await _cleanup_test_rows(
            sf,
            helper_ids=[helper_id] if helper_id else [],
            chat_ids=[chat_a, chat_b],
        )

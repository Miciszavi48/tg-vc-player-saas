from __future__ import annotations

import os
from datetime import date, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./.pytest-test-mode.db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


@pytest_asyncio.fixture
async def redis_test():
    import fakeredis.aioredis as fakeredis
    r = fakeredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


# ── Unit: track_event generates Redis keys ───────────────────────────────

@pytest.mark.asyncio
async def test_track_event_increments_redis_keys(redis_test):
    import app.utils.cache as cache_mod
    original = cache_mod._redis
    cache_mod._redis = redis_test
    try:
        from app.services.analytics_service import track_event
        await track_event("bot.start", chat_type="private", role="developer")

        keys = []
        cursor = 0
        from app.utils.redis_keys import AN_SCAN_PATTERN, strip_instance_namespace
        while True:
            cursor, batch = await redis_test.scan(
                cursor,
                match=AN_SCAN_PATTERN,
                count=500,
            )
            keys.extend(batch)
            if cursor == 0:
                break

        logical_keys = [strip_instance_namespace(key) for key in keys]
        key_suffixes = [
            key.split(":", 5)[-1]
            for key in logical_keys
            if len(key.split(":", 5)) == 6
        ]
        assert "events_total" in key_suffixes
        assert "bot.start" in key_suffixes
    finally:
        cache_mod._redis = original


# ── Unit: track_event swallows errors ────────────────────────────────────

@pytest.mark.asyncio
async def test_track_event_swallows_errors():
    with patch("app.services.analytics_service.get_redis", side_effect=Exception("redis down")):
        from app.services.analytics_service import track_event
        await track_event("bot.start")


# ── Unit: flush upserts and clears ──────────────────────────────────────

@pytest.mark.asyncio
async def test_flush_upserts_and_clears(redis_test):
    import app.utils.cache as cache_mod
    original = cache_mod._redis
    cache_mod._redis = redis_test
    try:
        from app.services.analytics_service import flush_analytics, track_event

        await track_event("bot.start", chat_type="private", role="user")
        await track_event("bot.start", chat_type="private", role="user")
        await track_event("playback.play_audio", chat_type="group", role="user", feature="playback")

        await flush_analytics()

        keys_after = []
        cursor = 0
        from app.utils.redis_keys import AN_SCAN_PATTERN
        while True:
            cursor, batch = await redis_test.scan(
                cursor,
                match=AN_SCAN_PATTERN,
                count=500,
            )
            keys_after.extend(batch)
            if cursor == 0:
                break
        assert len(keys_after) == 0, f"Expected 0 Redis keys after flush, got {len(keys_after)}"

        from app.database.engine import async_session
        from sqlalchemy import text
        async with async_session() as session:
            result = await session.execute(text(
                "SELECT metric, value FROM analytics_hourly "
                "WHERE scope = 'global' AND scope_key = 'global' AND metric = 'events_total'"
            ))
            rows = result.all()
            assert len(rows) > 0
            total = sum(r.value for r in rows)
            assert total >= 3
    finally:
        cache_mod._redis = original


# ── Unit: flush idempotent (GETDEL prevents double counting) ────────────

@pytest.mark.asyncio
async def test_flush_idempotent(redis_test):
    import app.utils.cache as cache_mod
    original = cache_mod._redis
    cache_mod._redis = redis_test
    try:
        from app.services.analytics_service import flush_analytics

        import uuid
        unique_metric = f"test.idemp.{uuid.uuid4().hex[:8]}"
        from app.utils.redis_keys import an_counter_key
        await redis_test.set(
            an_counter_key(
                _today(),
                _hour(),
                "global",
                "global",
                unique_metric,
            ),
            "1",
            ex=300,
        )
        await flush_analytics()
        await flush_analytics()

        from app.database.engine import async_session
        from sqlalchemy import text
        async with async_session() as session:
            result = await session.execute(text(
                "SELECT SUM(value) as total FROM analytics_hourly "
                "WHERE scope = 'global' AND scope_key = 'global' AND metric = :m"
            ), {"m": unique_metric})
            total = result.scalar() or 0
            assert total == 1, f"Expected 1 after double flush, got {total}"
    finally:
        cache_mod._redis = original


def _today():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%d")

def _hour():
    from datetime import datetime, timezone
    return str(datetime.now(timezone.utc).hour)


# ── Unit: report yesterday reads from DB ─────────────────────────────────

@pytest.mark.asyncio
async def test_report_yesterday():
    from app.database.engine import async_session
    from sqlalchemy import text
    yesterday = date.today() - timedelta(days=1)

    async with async_session() as session:
        async with session.begin():
            await session.execute(text(
                "INSERT INTO analytics_hourly (day, hour, scope, scope_key, metric, value) "
                "VALUES (:day, 10, 'global', 'global', 'events_total', 500) "
                "ON CONFLICT (day, hour, scope, scope_key, metric) DO UPDATE SET value = 500"
            ), {"day": yesterday})
            await session.execute(text(
                "INSERT INTO analytics_hourly (day, hour, scope, scope_key, metric, value) "
                "VALUES (:day, 10, 'global', 'global', 'playback.play_audio', 42) "
                "ON CONFLICT (day, hour, scope, scope_key, metric) DO UPDATE SET value = 42"
            ), {"day": yesterday})

    from app.services.analytics_service import get_report
    data = await get_report("yesterday")
    assert data["events_total"] >= 500
    assert data["play_audio"] >= 42


# ── Unit: peak hours calculation ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_peak_hours_calculation():
    from app.database.engine import async_session
    from sqlalchemy import text
    test_day = date.today() - timedelta(days=2)

    async with async_session() as session:
        async with session.begin():
            await session.execute(text(
                "DELETE FROM analytics_hourly WHERE day = :day AND scope = 'global' "
                "AND scope_key = 'global' AND metric = 'events_total'"
            ), {"day": test_day})
            for hour, val in [(14, 100), (15, 200), (16, 50), (20, 9999)]:
                await session.execute(text(
                    "INSERT INTO analytics_hourly (day, hour, scope, scope_key, metric, value) "
                    "VALUES (:day, :hour, 'global', 'global', 'events_total', :val) "
                    "ON CONFLICT (day, hour, scope, scope_key, metric) "
                    "DO UPDATE SET value = :val"
                ), {"day": test_day, "hour": hour, "val": val})

    from app.services.analytics_service import _query_peak_hours
    peaks = await _query_peak_hours(test_day, test_day, limit=3)
    assert len(peaks) >= 2
    assert peaks[0][0] == 20


# ── Unit: report caching ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_caching(redis_test):
    import app.utils.cache as cache_mod
    original = cache_mod._redis
    cache_mod._redis = redis_test
    try:
        from app.services.analytics_service import get_report

        data1 = await get_report("yesterday")
        data2 = await get_report("yesterday")
        assert data1 == data2
    finally:
        cache_mod._redis = original


# ── Integration: concurrent flush lock ───────────────────────────────────

@pytest.mark.asyncio
async def test_concurrent_flush_lock(redis_test):
    import app.utils.cache as cache_mod
    original = cache_mod._redis
    cache_mod._redis = redis_test
    try:
        from app.utils.cache import acquire_lock
        token = await acquire_lock("analytics:flush", ttl_ms=10_000)
        assert token is not None

        from app.services.analytics_service import flush_analytics
        await flush_analytics()
        # Should return without error (lock already held)

        from app.utils.cache import release_lock
        await release_lock("analytics:flush", token)
    finally:
        cache_mod._redis = original


# ── i18n parity: analytics keys exist in both fa and en ──────────────────

def test_analytics_i18n_parity():
    from tests.i18n_test_utils import load_en_i18n, load_fa_i18n

    fa = load_fa_i18n()
    en = load_en_i18n()

    fa_keys = set(_flatten_keys(fa.get("admin", {}).get("analytics", {})))
    en_keys = set(_flatten_keys(en.get("admin", {}).get("analytics", {})))

    assert fa_keys, "No analytics keys found in fa.json"
    assert fa_keys == en_keys, f"Key mismatch: fa_only={fa_keys - en_keys}, en_only={en_keys - fa_keys}"


def _flatten_keys(d, prefix=""):
    keys = []
    for k, v in d.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            keys.extend(_flatten_keys(v, full))
        else:
            keys.append(full)
    return keys

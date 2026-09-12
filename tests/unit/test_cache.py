from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.utils.cache import acquire_lock, release_lock


@pytest.mark.asyncio
class TestCache:
    async def test_distributed_lock_acquire_release(self, redis_conn):
        token = await acquire_lock("test_lock_key", ttl_ms=5000)
        assert token is not None

        second = await acquire_lock("test_lock_key", ttl_ms=5000)
        assert second is None

        released = await release_lock("test_lock_key", token)
        assert released is True

        token2 = await acquire_lock("test_lock_key", ttl_ms=5000)
        assert token2 is not None
        await release_lock("test_lock_key", token2)

    async def test_cache_set_get(self, redis_conn):
        await redis_conn.set("test:key", "hello", ex=60)
        val = await redis_conn.get("test:key")
        assert val == "hello"

        await redis_conn.delete("test:key")
        val = await redis_conn.get("test:key")
        assert val is None

    async def test_release_lock_never_falls_back_to_get_then_delete(self):
        redis_client = SimpleNamespace(
            eval=AsyncMock(side_effect=NotImplementedError),
            get=AsyncMock(return_value="owner-token"),
            delete=AsyncMock(return_value=1),
        )
        with patch(
            "app.utils.cache.get_redis",
            AsyncMock(return_value=redis_client),
        ):
            released = await release_lock("ownership-safe", "owner-token")

        assert released is False
        redis_client.get.assert_not_awaited()
        redis_client.delete.assert_not_awaited()

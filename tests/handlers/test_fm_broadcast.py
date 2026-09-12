from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

os.environ.setdefault(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./.pytest-test-mode.db",
)
# Never default to db 0: that is the deployed bot's database, and this module
# flushes whatever it connects to.
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("BOT_TOKEN", "test")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "testhash")
os.environ.setdefault("DEVELOPER_ID", "123456789")


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes")


# Mirrors conftest._USE_FAKE_REDIS. This fixture flushes the database it opens,
# so it must never reach a real server unless the runner opts in explicitly.
_USE_FAKE_REDIS = _env_true("TEST_MODE") or not _env_true("ALLOW_EXTERNAL_REDIS")


@pytest_asyncio.fixture
async def redis_test():
    if _USE_FAKE_REDIS:
        import fakeredis.aioredis as fakeredis

        r = fakeredis.FakeRedis(decode_responses=True)
        yield r
        await r.flushdb()
        await r.aclose()
        return

    r = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
    yield r
    await r.flushdb()
    await r.aclose()


# ── FM cache tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_membership_cached(redis_test):
    """When fm:ok:<user_id> is set in Redis, is_fm_ok() returns True."""
    import app.utils.cache as cache_mod

    from app.utils.redis_keys import fm_ok_key

    original = cache_mod._redis
    cache_mod._redis = redis_test
    try:
        # Write through the key factory: is_fm_ok() reads the instance-namespaced key.
        await redis_test.set(fm_ok_key(123), "1", ex=180)
        from app.utils.cache import is_fm_ok
        result = await is_fm_ok(123)
        assert result is True
    finally:
        cache_mod._redis = original


@pytest.mark.asyncio
async def test_check_membership_missing():
    """When getChatMember returns 'left', check() returns the missing list."""
    mock_client = AsyncMock()
    mock_member = MagicMock()
    mock_member.status.value = "left"
    mock_client.get_chat_member = AsyncMock(return_value=mock_member)

    targets = [
        {
            "id": 1,
            "channel_id": -1001234567890,
            "username": "testchan",
            "invite_link": "https://t.me/testchan",
            "display_name": "Test Channel",
            "chat_type": "channel",
            "verify_status": "ok",
        }
    ]

    with (
        patch(
            "app.services.forced_membership_service.ForcedMembershipService.is_enabled",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.services.forced_membership_service.is_fm_ok",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.services.forced_membership_service.ForcedMembershipService.get_targets_cached",
            new_callable=AsyncMock,
            return_value=targets,
        ),
    ):
        from app.services.forced_membership_service import ForcedMembershipService
        missing = await ForcedMembershipService.check(mock_client, 999)
        assert missing is not None
        assert len(missing) == 1
        assert missing[0]["channel_id"] == -1001234567890


@pytest.mark.asyncio
async def test_check_membership_disabled():
    """When force_join_enabled is false, check() returns None."""
    mock_client = AsyncMock()
    with patch(
        "app.services.forced_membership_service.ForcedMembershipService.is_enabled",
        new_callable=AsyncMock,
        return_value=False,
    ):
        from app.services.forced_membership_service import ForcedMembershipService
        result = await ForcedMembershipService.check(mock_client, 999)
        assert result is None


# ── Broadcast payload extraction tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_payload_text():
    """extract_payload correctly extracts text + entities."""
    from app.services.broadcast_service_v2 import BroadcastServiceV2

    msg = MagicMock()
    msg.text = "Hello **world**"
    entity = MagicMock()
    entity.type.value = "bold"
    entity.offset = 6
    entity.length = 9
    entity.url = None
    entity.user = None
    entity.language = None
    entity.custom_emoji_id = None
    msg.entities = [entity]
    msg.photo = None
    msg.video = None
    msg.document = None
    msg.audio = None
    msg.animation = None
    msg.voice = None
    msg.video_note = None
    msg.sticker = None

    payload = BroadcastServiceV2.extract_payload(msg)
    assert payload["payload_type"] == "text"
    assert payload["text_content"] == "Hello **world**"
    assert payload["entities_json"] is not None

    parsed = json.loads(payload["entities_json"])
    assert len(parsed) == 1
    assert parsed[0]["type"] == "bold"
    assert parsed[0]["offset"] == 6
    assert parsed[0]["length"] == 9


@pytest.mark.asyncio
async def test_extract_payload_photo():
    """extract_payload correctly extracts photo + caption."""
    from app.services.broadcast_service_v2 import BroadcastServiceV2

    msg = MagicMock()
    msg.text = None
    msg.entities = None
    photo = MagicMock()
    photo.file_id = "AgACAgIAAxkBAAI"
    msg.photo = photo
    msg.caption = "Nice photo"
    msg.caption_entities = None
    msg.video = None
    msg.document = None
    msg.audio = None
    msg.animation = None
    msg.voice = None
    msg.video_note = None
    msg.sticker = None

    payload = BroadcastServiceV2.extract_payload(msg)
    assert payload["payload_type"] == "photo"
    assert payload["file_id"] == "AgACAgIAAxkBAAI"
    assert payload["caption"] == "Nice photo"


# ── Entity serialization tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_serialize_entities_roundtrip():
    """Serialize then rebuild entities — offset/length/type match."""
    from app.services.broadcast_service_v2 import _serialize_entities

    entity = MagicMock()
    entity.type.value = "bold"
    entity.offset = 0
    entity.length = 5
    entity.url = None
    entity.user = None
    entity.language = None
    entity.custom_emoji_id = None

    serialized = _serialize_entities([entity])
    assert serialized is not None

    parsed = json.loads(serialized)
    assert len(parsed) == 1
    assert parsed[0]["offset"] == 0
    assert parsed[0]["length"] == 5
    assert parsed[0]["type"] == "bold"


# ── Rate limit tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fm_rate_limit(redis_test):
    """First call returns True (allowed), immediate second returns False (blocked)."""
    import app.utils.cache as cache_mod

    original = cache_mod._redis
    cache_mod._redis = redis_test
    try:
        from app.utils.cache import check_fm_rate_limit
        first = await check_fm_rate_limit(42)
        assert first is True
        second = await check_fm_rate_limit(42)
        assert second is False
    finally:
        cache_mod._redis = original


# ── Broadcast lock tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broadcast_lock_prevents_double():
    """When the lock is already acquired, execute returns without sending."""
    with patch(
        "app.services.broadcast_service_v2.acquire_lock",
        new_callable=AsyncMock,
        return_value=None,
    ):
        from app.services.broadcast_service_v2 import BroadcastServiceV2
        result = await BroadcastServiceV2.execute(AsyncMock(), 999)
        assert result is None

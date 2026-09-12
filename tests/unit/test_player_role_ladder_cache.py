from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

import pytest

from app.repositories import admin_repo
from app.utils.redis_keys import (
    music_admin_members_key,
    player_deputy_members_key,
    player_owner_members_key,
    player_role_summary_key,
    player_role_version_key,
    player_vip_expiry_key,
    player_vip_members_key,
)


def test_player_role_redis_key_helpers() -> None:
    prefix = "musicbot:test:"
    assert player_owner_members_key(-100) == f"{prefix}player_role:owners:-100"
    assert player_deputy_members_key(-100) == f"{prefix}player_role:deputies:-100"
    assert music_admin_members_key(-100) == f"{prefix}player_role:music_admins:-100"
    assert player_vip_members_key(-100) == f"{prefix}player_role:vips:-100"
    assert player_role_summary_key(-100, 42) == f"{prefix}player_role:summary:-100:42"
    assert player_role_version_key(-100) == f"{prefix}player_role:version:-100"
    assert player_vip_expiry_key(-100) == f"{prefix}player_role:vip_expiry:-100"


@pytest.mark.asyncio
async def test_deputy_add_remove_updates_redis_summary_and_set(redis_conn):
    chat_id = -10068201
    user_id = 682011

    await admin_repo.promote_player_deputy(chat_id, user_id, username="dep")
    raw = await redis_conn.get(player_role_summary_key(chat_id, user_id))
    assert raw is not None
    assert json.loads(raw)["deputy"] is True
    assert await redis_conn.sismember(player_deputy_members_key(chat_id), str(user_id))

    await admin_repo.demote_player_deputy(chat_id, user_id)
    raw = await redis_conn.get(player_role_summary_key(chat_id, user_id))
    assert raw is not None
    assert json.loads(raw)["deputy"] is False
    assert not await redis_conn.sismember(player_deputy_members_key(chat_id), str(user_id))


@pytest.mark.asyncio
async def test_vip_expiry_is_not_allowed_and_cleanup_invalidates_cache(redis_conn):
    chat_id = -10068202
    active_id = 682021
    expired_id = 682022
    now = datetime.now(timezone.utc)

    await admin_repo.promote_vip(chat_id, active_id, expires_at=now + timedelta(hours=1))
    await admin_repo.promote_vip(chat_id, expired_id, expires_at=now - timedelta(minutes=1))

    assert await admin_repo.is_vip(active_id, chat_id) is True
    assert await admin_repo.is_vip(expired_id, chat_id) is False
    assert await redis_conn.sismember(player_vip_members_key(chat_id), str(active_id))
    assert not await redis_conn.sismember(player_vip_members_key(chat_id), str(expired_id))

    removed = await admin_repo.expire_due_vips(now)
    assert removed >= 1
    assert await admin_repo.is_vip(expired_id, chat_id) is False


@pytest.mark.asyncio
async def test_role_check_falls_back_to_db_when_redis_unavailable():
    chat_id = -10068203
    user_id = 682031
    await admin_repo.promote_music_admin(chat_id, user_id)

    with patch("app.utils.cache.get_redis", side_effect=ConnectionError("redis down")):
        assert await admin_repo.is_music_admin(user_id, chat_id) is True

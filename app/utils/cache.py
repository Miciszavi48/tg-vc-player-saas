"""Redis cache layer — all key templates come from ``redis_keys``."""
from __future__ import annotations

import json
import logging
import hashlib
import uuid
from typing import Any

import redis.asyncio as redis
from redis.exceptions import WatchError
from sqlalchemy import text

from app.config.settings import settings
from app.utils.redis_keys import (
    BC_ACTIVE,
    FILTERWORDS,
    FM_TARGETS,
    OWNERLIST,
    PG_ADVISORY_TOKEN_PREFIX,
    SUDOLIST,
    TTL_BC_PROGRESS,
    TTL_BLACKLIST,
    TTL_GLOBAL_BAN,
    TTL_FM_OK,
    TTL_FM_RATE_LIMIT,
    TTL_FM_TARGETS,
    TTL_LIST,
    TTL_ROLE,
    bc_cursor_key,
    bc_fail_key,
    bc_sent_key,
    blacklist_group_key,
    blacklist_user_key,
    global_ban_user_key,
    botset_key,
    credit_key,
    fm_ok_key,
    fm_rate_limit_key,
    instance_scan_pattern,
    lock_key,
    role_key,
    settings_key,
)

logger = logging.getLogger(__name__)

_redis: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    global _redis  # noqa: PLW0603
    if _redis is None:
        _redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


# ── Chat settings cache ──────────────────────────────────────────────────────

async def get_chat_settings_cached(
    chat_id: int, chat_type: str = "group"
) -> dict[str, Any] | None:
    r = await get_redis()
    raw = await r.get(settings_key(chat_id, chat_type))
    if raw is None:
        return None
    return json.loads(raw)


async def set_chat_settings_cached(
    chat_id: int, data: dict[str, Any], chat_type: str = "group"
) -> None:
    r = await get_redis()
    await r.set(
        settings_key(chat_id, chat_type),
        json.dumps(data),
        ex=settings.REDIS_SETTINGS_TTL,
    )


async def invalidate_chat_settings(chat_id: int, chat_type: str = "group") -> None:
    r = await get_redis()
    await r.delete(settings_key(chat_id, chat_type))


# ── Credit cache ─────────────────────────────────────────────────────────────

async def get_credit_cached(chat_id: int, chat_type: str = "group") -> int | None:
    r = await get_redis()
    raw = await r.get(credit_key(chat_id, chat_type))
    if raw is None:
        return None
    return int(raw)


async def set_credit_cached(chat_id: int, credit_days: int, chat_type: str = "group") -> None:
    r = await get_redis()
    await r.set(credit_key(chat_id, chat_type), str(credit_days), ex=settings.REDIS_CREDIT_TTL)


async def invalidate_credit(chat_id: int, chat_type: str = "group") -> None:
    r = await get_redis()
    await r.delete(credit_key(chat_id, chat_type))


# ── Role cache ───────────────────────────────────────────────────────────────

async def get_role_cached(user_id: int) -> str | None:
    r = await get_redis()
    return await r.get(role_key(user_id))


async def set_role_cached(user_id: int, role: str) -> None:
    r = await get_redis()
    await r.set(role_key(user_id), role, ex=TTL_ROLE)


async def invalidate_role(user_id: int) -> None:
    r = await get_redis()
    await r.delete(role_key(user_id))


# ── Bot settings cache ──────────────────────────────────────────────────────

async def get_bot_setting_cached(key: str) -> str | None:
    r = await get_redis()
    return await r.get(botset_key(key))


async def set_bot_setting_cached(key: str, value: str) -> None:
    r = await get_redis()
    await r.set(botset_key(key), value, ex=settings.REDIS_SETTINGS_TTL)


async def invalidate_bot_setting(key: str) -> None:
    r = await get_redis()
    await r.delete(botset_key(key))


# ── Blacklist cache ──────────────────────────────────────────────────────────

async def is_blacklisted_cached(entity_id: int, entity_type: str) -> bool | None:
    r = await get_redis()
    key = blacklist_group_key(entity_id) if entity_type == "group" else blacklist_user_key(entity_id)
    val = await r.get(key)
    if val is None:
        return None
    return val == "1"


async def set_blacklisted_cached(entity_id: int, entity_type: str, blocked: bool) -> None:
    r = await get_redis()
    key = blacklist_group_key(entity_id) if entity_type == "group" else blacklist_user_key(entity_id)
    await r.set(key, "1" if blocked else "0", ex=TTL_BLACKLIST)


async def invalidate_blacklist(entity_id: int, entity_type: str) -> None:
    r = await get_redis()
    key = blacklist_group_key(entity_id) if entity_type == "group" else blacklist_user_key(entity_id)
    await r.delete(key)


# ── Global ban cache ─────────────────────────────────────────────────────────

async def is_global_ban_cached(user_id: int) -> bool | None:
    """Return cached ban state, or None on cache miss."""
    r = await get_redis()
    val = await r.get(global_ban_user_key(user_id))
    if val is None:
        return None
    return val == "1"


async def set_global_ban_cached(user_id: int, banned: bool) -> None:
    r = await get_redis()
    await r.set(global_ban_user_key(user_id), "1" if banned else "0", ex=TTL_GLOBAL_BAN)


async def invalidate_global_ban(user_id: int) -> None:
    r = await get_redis()
    await r.delete(global_ban_user_key(user_id))


async def invalidate_all_global_bans() -> None:
    """Best-effort flush of global ban keys (used after clear-all)."""
    r = await get_redis()
    try:
        keys = [
            k
            async for k in r.scan_iter(
                instance_scan_pattern("global_ban:user:*")
            )
        ]
        if keys:
            await r.delete(*keys)
    except Exception:
        logger.debug("invalidate_all_global_bans scan failed", exc_info=True)


# ── List caches (filterwords, sudolist, ownerlist) ───────────────────────────

async def get_filterwords_cached() -> list[str] | None:
    r = await get_redis()
    raw = await r.get(FILTERWORDS)
    return json.loads(raw) if raw else None


async def set_filterwords_cached(words: list[str]) -> None:
    r = await get_redis()
    await r.set(FILTERWORDS, json.dumps(words), ex=TTL_LIST)


async def invalidate_filterwords() -> None:
    r = await get_redis()
    await r.delete(FILTERWORDS)


async def get_sudolist_cached() -> list[int] | None:
    r = await get_redis()
    raw = await r.get(SUDOLIST)
    return json.loads(raw) if raw else None


async def set_sudolist_cached(ids: list[int]) -> None:
    r = await get_redis()
    await r.set(SUDOLIST, json.dumps(ids), ex=TTL_LIST)


async def invalidate_sudolist() -> None:
    r = await get_redis()
    await r.delete(SUDOLIST)


async def get_ownerlist_cached() -> list[int] | None:
    r = await get_redis()
    raw = await r.get(OWNERLIST)
    return json.loads(raw) if raw else None


async def set_ownerlist_cached(ids: list[int]) -> None:
    r = await get_redis()
    await r.set(OWNERLIST, json.dumps(ids), ex=TTL_LIST)


async def invalidate_ownerlist() -> None:
    r = await get_redis()
    await r.delete(OWNERLIST)


# ── Forced membership cache ─────────────────────────────────────────────────

async def get_fm_targets_cached() -> list[dict] | None:
    r = await get_redis()
    raw = await r.get(FM_TARGETS)
    return json.loads(raw) if raw else None


async def set_fm_targets_cached(data: list[dict]) -> None:
    r = await get_redis()
    await r.set(FM_TARGETS, json.dumps(data), ex=TTL_FM_TARGETS)


async def invalidate_fm_targets() -> None:
    r = await get_redis()
    await r.delete(FM_TARGETS)


async def is_fm_ok(user_id: int) -> bool:
    r = await get_redis()
    return await r.get(fm_ok_key(user_id)) is not None


async def set_fm_ok(user_id: int) -> None:
    r = await get_redis()
    await r.set(fm_ok_key(user_id), "1", ex=TTL_FM_OK)


async def invalidate_fm_ok(user_id: int) -> None:
    r = await get_redis()
    await r.delete(fm_ok_key(user_id))


async def check_fm_rate_limit(user_id: int) -> bool:
    """Return True if allowed, False if rate-limited."""
    r = await get_redis()
    if await r.get(fm_rate_limit_key(user_id)):
        return False
    await r.set(fm_rate_limit_key(user_id), "1", ex=TTL_FM_RATE_LIMIT)
    return True


# ── Broadcast cache ──────────────────────────────────────────────────────────

async def set_bc_active(broadcast_id: int) -> None:
    r = await get_redis()
    await r.set(BC_ACTIVE, str(broadcast_id))


async def clear_bc_active() -> None:
    r = await get_redis()
    await r.delete(BC_ACTIVE)


async def update_bc_progress(broadcast_id: int, cursor: int, sent: int, fail: int) -> None:
    r = await get_redis()
    pipe = r.pipeline()
    pipe.set(bc_cursor_key(broadcast_id), str(cursor), ex=TTL_BC_PROGRESS)
    pipe.set(bc_sent_key(broadcast_id), str(sent), ex=TTL_BC_PROGRESS)
    pipe.set(bc_fail_key(broadcast_id), str(fail), ex=TTL_BC_PROGRESS)
    await pipe.execute()


async def get_bc_progress(broadcast_id: int) -> tuple[int, int, int]:
    r = await get_redis()
    pipe = r.pipeline()
    pipe.get(bc_cursor_key(broadcast_id))
    pipe.get(bc_sent_key(broadcast_id))
    pipe.get(bc_fail_key(broadcast_id))
    results = await pipe.execute()
    return (int(results[0] or 0), int(results[1] or 0), int(results[2] or 0))


async def clear_bc_progress(broadcast_id: int) -> None:
    r = await get_redis()
    pipe = r.pipeline()
    pipe.delete(bc_cursor_key(broadcast_id))
    pipe.delete(bc_sent_key(broadcast_id))
    pipe.delete(bc_fail_key(broadcast_id))
    await pipe.execute()


# ── Distributed lock ────────────────────────────────────────────────────────

_RELEASE_LOCK_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


_pg_advisory_connections: dict[str, Any] = {}


async def acquire_lock(key: str, ttl_ms: int = 5000) -> str | None:
    """Acquire a distributed lock. Redis first; Postgres advisory fallback."""
    try:
        r = await get_redis()
        token = str(uuid.uuid4())
        acquired = await r.set(lock_key(key), token, px=ttl_ms, nx=True)
        if acquired:
            return token
        return None
    except (redis.ConnectionError, redis.RedisError, OSError):
        logger.warning("Redis unavailable for lock %s – falling back to pg_advisory_lock", key)
        return await _pg_advisory_acquire(key)


async def release_lock(key: str, token: str) -> bool:
    """Release a distributed lock."""
    if token.startswith(PG_ADVISORY_TOKEN_PREFIX):
        return await _pg_advisory_release(key)
    try:
        r = await get_redis()
        redis_key = lock_key(key)
        try:
            result = await r.eval(_RELEASE_LOCK_SCRIPT, 1, redis_key, token)
            if result == 1:
                return True
            return False
        except (redis.ConnectionError, redis.RedisError, OSError, NotImplementedError):
            try:
                async with r.pipeline(transaction=True) as pipe:
                    await pipe.watch(redis_key)
                    if await pipe.get(redis_key) != token:
                        await pipe.unwatch()
                        return False
                    pipe.multi()
                    pipe.delete(redis_key)
                    result = await pipe.execute()
                    return bool(result and result[0] == 1)
            except WatchError:
                return False
            except (
                AttributeError,
                redis.ConnectionError,
                redis.RedisError,
                OSError,
                NotImplementedError,
            ):
                logger.warning(
                    "Atomic Redis lock release unavailable for %s; leaving the TTL "
                    "to expire instead of risking deletion of a new owner's lock",
                    key,
                )
                return False
    except (redis.ConnectionError, redis.RedisError, OSError):
        return False


def _lock_key_to_bigint(key: str) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


async def _pg_advisory_acquire(key: str) -> str | None:
    from app.database.engine import engine

    if engine.dialect.name != "postgresql":
        logger.warning(
            "Redis unavailable for lock %s and advisory fallback requires PostgreSQL",
            key,
        )
        return None

    lock_id = _lock_key_to_bigint(key)
    conn = await engine.connect()
    try:
        result = await conn.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id})
        acquired = result.scalar()
        await conn.commit()
    except Exception:
        await conn.close()
        return None

    if acquired:
        token = f"{PG_ADVISORY_TOKEN_PREFIX}{lock_id}"
        _pg_advisory_connections[token] = conn
        return token

    await conn.close()
    return None


async def _pg_advisory_release(key: str) -> bool:
    lock_id = _lock_key_to_bigint(key)
    token = f"{PG_ADVISORY_TOKEN_PREFIX}{lock_id}"
    conn = _pg_advisory_connections.pop(token, None)
    if conn is None:
        return False
    try:
        result = await conn.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": lock_id})
        released = result.scalar()
        await conn.commit()
        return bool(released)
    finally:
        await conn.close()

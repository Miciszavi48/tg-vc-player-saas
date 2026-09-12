from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, or_, select

from app.utils.redis_keys import (
    TTL_ROLE,
    music_admin_members_key,
    player_deputy_members_key,
    player_owner_members_key,
    player_role_summary_key,
    player_role_version_key,
    player_vip_expiry_key,
    player_vip_members_key,
    vip_key,
)
from app.database.engine import async_session
from app.database.models import MusicAdmin, PlayerDeputy, PlayerOwner, PlayerVip, VideoAdmin

logger = logging.getLogger(__name__)

VIP_LIST_PAGE_SIZE = 10
_ROLE_MEMBER_KEYS = {
    "owner": player_owner_members_key,
    "deputy": player_deputy_members_key,
    "music_admin": music_admin_members_key,
    "vip": player_vip_members_key,
}


def _vip_total_pages(total: int, page_size: int) -> int:
    return max(1, (total + page_size - 1) // page_size)


def clamp_vip_list_page(
    page: int,
    total: int,
    page_size: int = VIP_LIST_PAGE_SIZE,
) -> int:
    """Clamp page index after VIP list changes (e.g. demotion)."""
    if total <= 0:
        return 0
    return max(0, min(page, _vip_total_pages(total, page_size) - 1))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _vip_active_clause():
    return or_(PlayerVip.expires_at.is_(None), PlayerVip.expires_at > _now_utc())


def _summary_ttl(summary: dict[str, Any]) -> int:
    expires_raw = summary.get("vip_expires_at")
    if not summary.get("vip") or not expires_raw:
        return TTL_ROLE
    try:
        expires_at = _aware(datetime.fromisoformat(str(expires_raw)))
    except ValueError:
        return TTL_ROLE
    remaining = int((expires_at - _now_utc()).total_seconds())
    if remaining <= 0:
        return 1
    return max(1, min(TTL_ROLE, remaining))


def _cached_vip_expired(summary: dict[str, Any]) -> bool:
    expires_raw = summary.get("vip_expires_at")
    if not summary.get("vip") or not expires_raw:
        return False
    try:
        return _aware(datetime.fromisoformat(str(expires_raw))) <= _now_utc()
    except ValueError:
        return True


async def _redis_or_none():
    try:
        from app.utils.cache import get_redis

        return await get_redis()
    except Exception:
        logger.warning("player role cache unavailable", exc_info=True)
        return None


async def _load_role_summary_from_db(chat_id: int, user_id: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "owner": False,
        "deputy": False,
        "music_admin": False,
        "vip": False,
        "vip_expires_at": None,
    }
    async with async_session() as session:
        for key, model in (
            ("owner", PlayerOwner),
            ("deputy", PlayerDeputy),
            ("music_admin", MusicAdmin),
        ):
            result = await session.execute(
                select(model.user_id).where(model.chat_id == chat_id, model.user_id == user_id)
            )
            summary[key] = result.scalar_one_or_none() is not None

        vip_result = await session.execute(
            select(PlayerVip).where(
                PlayerVip.chat_id == chat_id,
                PlayerVip.user_id == user_id,
                _vip_active_clause(),
            )
        )
        vip = vip_result.scalar_one_or_none()
        if vip is not None:
            summary["vip"] = True
            expires_at = getattr(vip, "expires_at", None)
            if expires_at is not None:
                summary["vip_expires_at"] = _aware(expires_at).isoformat()
    return summary


async def _write_role_summary_cache(chat_id: int, user_id: int, summary: dict[str, Any]) -> None:
    r = await _redis_or_none()
    if r is None:
        return
    member = str(int(user_id))
    ttl = _summary_ttl(summary)
    try:
        await r.set(
            player_role_summary_key(chat_id, user_id),
            json.dumps(summary, separators=(",", ":")),
            ex=ttl,
        )
        for role, key_factory in _ROLE_MEMBER_KEYS.items():
            key = key_factory(chat_id)
            if summary.get(role):
                await r.sadd(key, member)
                await r.expire(key, TTL_ROLE)
            else:
                await r.srem(key, member)
        await r.set(vip_key(chat_id, user_id), "1" if summary.get("vip") else "0", ex=ttl)
        expiry_key = player_vip_expiry_key(chat_id)
        expires_raw = summary.get("vip_expires_at")
        if summary.get("vip") and expires_raw:
            expires_at = _aware(datetime.fromisoformat(str(expires_raw)))
            await r.zadd(expiry_key, {member: expires_at.timestamp()})
            await r.expire(expiry_key, TTL_ROLE)
        else:
            await r.zrem(expiry_key, member)
        await r.incr(player_role_version_key(chat_id))
        await r.expire(player_role_version_key(chat_id), TTL_ROLE)
    except Exception:
        logger.warning("player role cache write failed chat_id=%s user_id=%s", chat_id, user_id, exc_info=True)


async def _get_cached_role_summary(chat_id: int, user_id: int) -> dict[str, Any] | None:
    r = await _redis_or_none()
    if r is None:
        return None
    try:
        raw = await r.get(player_role_summary_key(chat_id, user_id))
        if raw is None:
            return None
        summary = json.loads(raw)
        if not isinstance(summary, dict):
            await r.delete(player_role_summary_key(chat_id, user_id))
            return None
        if _cached_vip_expired(summary):
            await r.delete(player_role_summary_key(chat_id, user_id), vip_key(chat_id, user_id))
            await r.srem(player_vip_members_key(chat_id), str(int(user_id)))
            return None
        return summary
    except Exception:
        logger.warning("player role cache read failed chat_id=%s user_id=%s", chat_id, user_id, exc_info=True)
        return None


async def get_player_role_summary(chat_id: int, user_id: int) -> dict[str, Any]:
    cached = await _get_cached_role_summary(chat_id, user_id)
    if cached is not None:
        return cached
    summary = await _load_role_summary_from_db(chat_id, user_id)
    await _write_role_summary_cache(chat_id, user_id, summary)
    return summary


async def refresh_player_role_cache(chat_id: int, user_id: int) -> None:
    summary = await _load_role_summary_from_db(chat_id, user_id)
    await _write_role_summary_cache(chat_id, user_id, summary)


async def refresh_player_role_caches(chat_id: int, user_ids: list[int]) -> None:
    for user_id in sorted({int(value) for value in user_ids}):
        await refresh_player_role_cache(chat_id, user_id)


async def count_player_vips(chat_id: int) -> int:
    async with async_session() as session:
        stmt = (
            select(func.count())
            .select_from(PlayerVip)
            .where(PlayerVip.chat_id == chat_id, _vip_active_clause())
        )
        result = await session.execute(stmt)
        return int(result.scalar() or 0)


async def get_player_vips_page(
    chat_id: int,
    page: int,
    page_size: int = VIP_LIST_PAGE_SIZE,
) -> tuple[list[PlayerVip], int, int]:
    """Return (rows, total_pages, clamped_page_index)."""
    total = await count_player_vips(chat_id)
    if total == 0:
        return [], 1, 0

    total_pages = _vip_total_pages(total, page_size)
    page = clamp_vip_list_page(page, total, page_size)

    async with async_session() as session:
        stmt = (
            select(PlayerVip)
            .where(PlayerVip.chat_id == chat_id, _vip_active_clause())
            .order_by(PlayerVip.id.asc())
            .offset(page * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all()), total_pages, page


async def is_music_admin(user_id: int, chat_id: int) -> bool:
    summary = await get_player_role_summary(chat_id, user_id)
    return bool(summary.get("music_admin"))


async def is_video_admin(user_id: int, chat_id: int) -> bool:
    async with async_session() as session:
        stmt = select(VideoAdmin).where(
            VideoAdmin.user_id == user_id, VideoAdmin.chat_id == chat_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def is_player_owner(user_id: int, chat_id: int) -> bool:
    summary = await get_player_role_summary(chat_id, user_id)
    return bool(summary.get("owner"))


async def is_player_deputy(user_id: int, chat_id: int) -> bool:
    summary = await get_player_role_summary(chat_id, user_id)
    return bool(summary.get("deputy"))


async def is_player_deputy_or_above(user_id: int, chat_id: int) -> bool:
    summary = await get_player_role_summary(chat_id, user_id)
    return any(bool(summary.get(key)) for key in ("owner", "deputy"))


async def is_music_admin_or_above(user_id: int, chat_id: int) -> bool:
    summary = await get_player_role_summary(chat_id, user_id)
    return any(bool(summary.get(key)) for key in ("owner", "deputy", "music_admin"))


async def promote_music_admin(
    chat_id: int,
    user_id: int,
    username: str | None = None,
    display_name: str | None = None,
    promoted_by: int | None = None,
) -> MusicAdmin:
    async with async_session() as session:
        stmt = select(MusicAdmin).where(
            MusicAdmin.chat_id == chat_id, MusicAdmin.user_id == user_id
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            await refresh_player_role_cache(chat_id, user_id)
            return existing

        admin = MusicAdmin(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            promoted_by=promoted_by,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
    await refresh_player_role_cache(chat_id, user_id)
    return admin


async def demote_music_admin(chat_id: int, user_id: int) -> None:
    async with async_session() as session:
        stmt = delete(MusicAdmin).where(
            MusicAdmin.chat_id == chat_id, MusicAdmin.user_id == user_id
        )
        await session.execute(stmt)
        await session.commit()
    await refresh_player_role_cache(chat_id, user_id)


async def promote_video_admin(
    chat_id: int,
    user_id: int,
    username: str | None = None,
    display_name: str | None = None,
    promoted_by: int | None = None,
) -> VideoAdmin:
    async with async_session() as session:
        stmt = select(VideoAdmin).where(
            VideoAdmin.chat_id == chat_id, VideoAdmin.user_id == user_id
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        admin = VideoAdmin(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            promoted_by=promoted_by,
        )
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
        return admin


async def demote_video_admin(chat_id: int, user_id: int) -> None:
    async with async_session() as session:
        stmt = delete(VideoAdmin).where(
            VideoAdmin.chat_id == chat_id, VideoAdmin.user_id == user_id
        )
        await session.execute(stmt)
        await session.commit()


async def promote_player_owner(
    chat_id: int,
    user_id: int,
    username: str | None = None,
    display_name: str | None = None,
    promoted_by: int | None = None,
) -> PlayerOwner:
    async with async_session() as session:
        stmt = select(PlayerOwner).where(
            PlayerOwner.chat_id == chat_id, PlayerOwner.user_id == user_id
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            await refresh_player_role_cache(chat_id, user_id)
            return existing

        po = PlayerOwner(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            promoted_by=promoted_by,
        )
        session.add(po)
        await session.commit()
        await session.refresh(po)
    await refresh_player_role_cache(chat_id, user_id)
    return po


async def demote_player_owner(chat_id: int, user_id: int) -> None:
    async with async_session() as session:
        stmt = delete(PlayerOwner).where(
            PlayerOwner.chat_id == chat_id, PlayerOwner.user_id == user_id
        )
        await session.execute(stmt)
        await session.commit()
    await refresh_player_role_cache(chat_id, user_id)


async def promote_player_deputy(
    chat_id: int,
    user_id: int,
    username: str | None = None,
    display_name: str | None = None,
    promoted_by: int | None = None,
) -> PlayerDeputy:
    async with async_session() as session:
        stmt = select(PlayerDeputy).where(
            PlayerDeputy.chat_id == chat_id, PlayerDeputy.user_id == user_id
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            await refresh_player_role_cache(chat_id, user_id)
            return existing
        deputy = PlayerDeputy(
            chat_id=chat_id,
            user_id=user_id,
            username=username,
            display_name=display_name,
            promoted_by=promoted_by,
        )
        session.add(deputy)
        await session.commit()
        await session.refresh(deputy)
    await refresh_player_role_cache(chat_id, user_id)
    return deputy


async def demote_player_deputy(chat_id: int, user_id: int) -> None:
    async with async_session() as session:
        stmt = delete(PlayerDeputy).where(
            PlayerDeputy.chat_id == chat_id, PlayerDeputy.user_id == user_id
        )
        await session.execute(stmt)
        await session.commit()
    await refresh_player_role_cache(chat_id, user_id)


async def get_music_admins(chat_id: int) -> list[MusicAdmin]:
    async with async_session() as session:
        stmt = select(MusicAdmin).where(MusicAdmin.chat_id == chat_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_video_admins(chat_id: int) -> list[VideoAdmin]:
    async with async_session() as session:
        stmt = select(VideoAdmin).where(VideoAdmin.chat_id == chat_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_player_owners(chat_id: int) -> list[PlayerOwner]:
    async with async_session() as session:
        stmt = select(PlayerOwner).where(PlayerOwner.chat_id == chat_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_player_deputies(chat_id: int) -> list[PlayerDeputy]:
    async with async_session() as session:
        stmt = select(PlayerDeputy).where(PlayerDeputy.chat_id == chat_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def clear_music_admins(chat_id: int) -> None:
    async with async_session() as session:
        result = await session.execute(select(MusicAdmin.user_id).where(MusicAdmin.chat_id == chat_id))
        user_ids = [int(value) for value in result.scalars().all()]
        stmt = delete(MusicAdmin).where(MusicAdmin.chat_id == chat_id)
        await session.execute(stmt)
        await session.commit()
    await refresh_player_role_caches(chat_id, user_ids)


async def clear_video_admins(chat_id: int) -> None:
    async with async_session() as session:
        stmt = delete(VideoAdmin).where(VideoAdmin.chat_id == chat_id)
        await session.execute(stmt)
        await session.commit()


async def clear_player_deputies(chat_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(select(PlayerDeputy.user_id).where(PlayerDeputy.chat_id == chat_id))
        user_ids = [int(value) for value in result.scalars().all()]
        if not user_ids:
            return 0
        await session.execute(delete(PlayerDeputy).where(PlayerDeputy.chat_id == chat_id))
        await session.commit()
    await refresh_player_role_caches(chat_id, user_ids)
    return len(user_ids)


async def is_vip(user_id: int, chat_id: int) -> bool:
    summary = await get_player_role_summary(chat_id, user_id)
    return bool(summary.get("vip"))


async def promote_vip(
    chat_id: int, user_id: int,
    username: str | None = None, display_name: str | None = None,
    promoted_by: int | None = None,
    expires_at: datetime | None = None,
) -> PlayerVip:
    async with async_session() as session:
        stmt = select(PlayerVip).where(
            PlayerVip.chat_id == chat_id, PlayerVip.user_id == user_id
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.username = username if username is not None else existing.username
            existing.display_name = display_name if display_name is not None else existing.display_name
            existing.promoted_by = promoted_by if promoted_by is not None else existing.promoted_by
            existing.expires_at = expires_at
            await session.commit()
            await session.refresh(existing)
            await refresh_player_role_cache(chat_id, user_id)
            return existing
        vip = PlayerVip(
            chat_id=chat_id, user_id=user_id,
            username=username, display_name=display_name,
            promoted_by=promoted_by, expires_at=expires_at,
        )
        session.add(vip)
        await session.commit()
        await session.refresh(vip)
    await refresh_player_role_cache(chat_id, user_id)
    return vip


async def demote_vip(chat_id: int, user_id: int) -> None:
    async with async_session() as session:
        stmt = delete(PlayerVip).where(
            PlayerVip.chat_id == chat_id, PlayerVip.user_id == user_id
        )
        await session.execute(stmt)
        await session.commit()
    await refresh_player_role_cache(chat_id, user_id)


async def clear_player_vips(chat_id: int) -> int:
    """Remove only player VIP rows for one chat and invalidate VIP cache keys."""
    async with async_session() as session:
        stmt = select(PlayerVip.user_id).where(PlayerVip.chat_id == chat_id)
        result = await session.execute(stmt)
        user_ids = [int(uid) for uid in result.scalars().all()]
        if not user_ids:
            return 0
        await session.execute(delete(PlayerVip).where(PlayerVip.chat_id == chat_id))
        await session.commit()

    await refresh_player_role_caches(chat_id, user_ids)
    return len(user_ids)


async def expire_due_vips(now: datetime | None = None) -> int:
    cutoff = now or _now_utc()
    async with async_session() as session:
        result = await session.execute(
            select(PlayerVip.chat_id, PlayerVip.user_id).where(
                PlayerVip.expires_at.isnot(None),
                PlayerVip.expires_at <= cutoff,
            )
        )
        rows = [(int(chat_id), int(user_id)) for chat_id, user_id in result.all()]
        if not rows:
            return 0
        await session.execute(
            delete(PlayerVip).where(
                PlayerVip.expires_at.isnot(None),
                PlayerVip.expires_at <= cutoff,
            )
        )
        await session.commit()
    for chat_id, user_id in rows:
        await refresh_player_role_cache(chat_id, user_id)
    return len(rows)

"""Repository for developer global user bans (ban-all)."""

from __future__ import annotations

from sqlalchemy import func, select, update

from app.database.engine import async_session
from app.database.models import GlobalBan
from app.utils.cache import (
    invalidate_all_global_bans,
    invalidate_global_ban,
    is_global_ban_cached,
    set_global_ban_cached,
)


class GlobalBanValidationError(ValueError):
    """Raised when a global ban request has invalid parameters."""


def validate_global_ban_user_id(user_id: int) -> None:
    """Validate a Telegram user id for global ban operations."""
    from app.utils.bot_guards import is_developer

    if user_id <= 0:
        raise GlobalBanValidationError("invalid_user_id")
    if is_developer(user_id):
        raise GlobalBanValidationError("cannot_ban_developer")


async def add_global_ban(
    user_id: int,
    created_by: int,
    reason: str | None = None,
) -> tuple[GlobalBan, bool]:
    """Add or reactivate a global ban.

    Returns:
        Tuple of (ban row, created_new). ``created_new`` is False when an
        active ban already existed (idempotent duplicate).
    """
    validate_global_ban_user_id(user_id)

    async with async_session() as session:
        stmt = select(GlobalBan).where(GlobalBan.user_id == user_id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None and existing.is_active:
            await set_global_ban_cached(user_id, True)
            return existing, False

        if existing is not None:
            existing.is_active = True
            existing.reason = reason
            existing.created_by = created_by
            await session.commit()
            await session.refresh(existing)
            await set_global_ban_cached(user_id, True)
            return existing, True

        entry = GlobalBan(
            user_id=user_id,
            reason=reason,
            created_by=created_by,
            is_active=True,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        await set_global_ban_cached(user_id, True)
        return entry, True


async def remove_global_ban(user_id: int) -> bool:
    """Deactivate a global ban. Returns True if an active ban was found."""
    async with async_session() as session:
        stmt = select(GlobalBan).where(
            GlobalBan.user_id == user_id,
            GlobalBan.is_active.is_(True),
        )
        result = await session.execute(stmt)
        entry = result.scalar_one_or_none()
        if entry is None:
            await set_global_ban_cached(user_id, False)
            return False

        entry.is_active = False
        await session.commit()
        await invalidate_global_ban(user_id)
        return True


async def is_globally_banned(user_id: int) -> bool:
    """Return True when the user has an active global ban."""
    cached = await is_global_ban_cached(user_id)
    if cached is not None:
        return cached

    async with async_session() as session:
        stmt = select(GlobalBan.id).where(
            GlobalBan.user_id == user_id,
            GlobalBan.is_active.is_(True),
        )
        result = await session.execute(stmt)
        banned = result.scalar_one_or_none() is not None

    await set_global_ban_cached(user_id, banned)
    return banned


async def list_global_bans(
    page: int = 0,
    limit: int = 10,
) -> tuple[list[GlobalBan], int]:
    """Return paginated active global bans and total count."""
    page = max(0, page)
    limit = max(1, min(limit, 50))
    offset = page * limit

    async with async_session() as session:
        count_stmt = select(func.count()).select_from(GlobalBan).where(
            GlobalBan.is_active.is_(True),
        )
        total = int((await session.execute(count_stmt)).scalar_one())

        stmt = (
            select(GlobalBan)
            .where(GlobalBan.is_active.is_(True))
            .order_by(GlobalBan.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all()), total


async def clear_global_bans() -> int:
    """Deactivate all active global bans. Returns count cleared."""
    async with async_session() as session:
        count_stmt = select(func.count()).select_from(GlobalBan).where(
            GlobalBan.is_active.is_(True),
        )
        total = int((await session.execute(count_stmt)).scalar_one())
        if total == 0:
            return 0

        stmt = (
            update(GlobalBan)
            .where(GlobalBan.is_active.is_(True))
            .values(is_active=False)
        )
        await session.execute(stmt)
        await session.commit()

    await invalidate_all_global_bans()
    return total

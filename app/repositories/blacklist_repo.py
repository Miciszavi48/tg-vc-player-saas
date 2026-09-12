from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database.engine import async_session
from app.database.models import Blacklist


async def is_blacklisted(entity_id: int, entity_type: str) -> bool:
    async with async_session() as session:
        stmt = select(Blacklist).where(
            Blacklist.entity_id == entity_id,
            Blacklist.entity_type == entity_type,
            Blacklist.is_active.is_(True),
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None


async def add_to_blacklist(
    entity_id: int,
    entity_type: str,
    reason: str | None = None,
    blocked_by: int | None = None,
) -> Blacklist:
    async with async_session() as session:
        stmt = select(Blacklist).where(
            Blacklist.entity_id == entity_id,
            Blacklist.entity_type == entity_type,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.is_active = True
            existing.reason = reason
            existing.blocked_by = blocked_by
            existing.unblocked_at = None
            await session.commit()
            await session.refresh(existing)
            return existing

        entry = Blacklist(
            entity_id=entity_id,
            entity_type=entity_type,
            reason=reason,
            blocked_by=blocked_by,
            is_active=True,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry


async def remove_from_blacklist(entity_id: int, entity_type: str) -> None:
    async with async_session() as session:
        stmt = (
            update(Blacklist)
            .where(
                Blacklist.entity_id == entity_id,
                Blacklist.entity_type == entity_type,
            )
            .values(is_active=False, unblocked_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.commit()


async def get_blacklist() -> list[Blacklist]:
    async with async_session() as session:
        stmt = select(Blacklist).where(Blacklist.is_active.is_(True))
        result = await session.execute(stmt)
        return list(result.scalars().all())

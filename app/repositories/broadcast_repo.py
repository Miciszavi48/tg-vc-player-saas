from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database.engine import async_session
from app.database.models import Broadcast


async def create(bc: Broadcast) -> Broadcast:
    async with async_session() as session:
        session.add(bc)
        await session.commit()
        await session.refresh(bc)
        return bc


async def create_many(broadcasts: list[Broadcast]) -> list[Broadcast]:
    """Persist one wizard confirmation atomically across all target scopes."""
    if not broadcasts:
        return []
    async with async_session() as session:
        session.add_all(broadcasts)
        # Allocate primary keys before the single atomic commit. Avoid
        # post-commit refresh calls: a refresh failure after persistence would
        # falsely report failure and leave the caller unable to distinguish an
        # orphaned committed batch from a rolled-back batch.
        await session.flush()
        await session.commit()
        return broadcasts


async def get_by_id(broadcast_id: int) -> Broadcast | None:
    async with async_session() as session:
        stmt = select(Broadcast).where(Broadcast.id == broadcast_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def update_status(broadcast_id: int, status: str) -> None:
    values: dict = {"status": status}
    if status == "running":
        values["started_at"] = datetime.now(timezone.utc)
    async with async_session() as session:
        stmt = update(Broadcast).where(Broadcast.id == broadcast_id).values(**values)
        await session.execute(stmt)
        await session.commit()


async def update_total(broadcast_id: int, total: int) -> None:
    async with async_session() as session:
        stmt = (
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(total_recipients=total)
        )
        await session.execute(stmt)
        await session.commit()


async def finish(broadcast_id: int, status: str, sent: int, fail: int) -> None:
    async with async_session() as session:
        stmt = (
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status=status,
                sent_count=sent,
                fail_count=fail,
                finished_at=datetime.now(timezone.utc),
            )
        )
        await session.execute(stmt)
        await session.commit()


async def get_recent(limit: int = 20) -> list[Broadcast]:
    async with async_session() as session:
        stmt = (
            select(Broadcast)
            .order_by(Broadcast.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_recent_by_admin(admin_id: int, limit: int = 20) -> list[Broadcast]:
    async with async_session() as session:
        stmt = (
            select(Broadcast)
            .where(Broadcast.admin_id == admin_id)
            .order_by(Broadcast.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_pending_scheduled(limit: int = 1000) -> list[Broadcast]:
    async with async_session() as session:
        stmt = (
            select(Broadcast)
            .where(Broadcast.status == "pending")
            .where(Broadcast.run_at.is_not(None))
            .order_by(Broadcast.run_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

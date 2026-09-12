from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update

from app.database.engine import async_session
from app.database.models import ForceJoinChannel


def _total_pages(total: int, page_size: int) -> int:
    return max(1, (total + page_size - 1) // page_size)


def _clamp_page(page: int, total_pages: int) -> int:
    return max(0, min(page, total_pages - 1))


async def get_active_targets() -> list[ForceJoinChannel]:
    async with async_session() as session:
        stmt = (
            select(ForceJoinChannel)
            .where(ForceJoinChannel.is_active.is_(True))
            .order_by(ForceJoinChannel.position)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def count_active_targets() -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count()).select_from(ForceJoinChannel).where(
                ForceJoinChannel.is_active.is_(True)
            )
        )
        return result.scalar() or 0


async def get_active_targets_page(
    page: int,
    page_size: int = 10,
) -> tuple[list[ForceJoinChannel], int]:
    total = await count_active_targets()
    if total == 0:
        return [], 1

    total_pages = _total_pages(total, page_size)
    page = _clamp_page(page, total_pages)

    async with async_session() as session:
        stmt = (
            select(ForceJoinChannel)
            .where(ForceJoinChannel.is_active.is_(True))
            .order_by(ForceJoinChannel.position)
            .offset(page * page_size)
            .limit(page_size)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all()), total_pages


async def get_all_targets() -> list[ForceJoinChannel]:
    async with async_session() as session:
        stmt = select(ForceJoinChannel).order_by(ForceJoinChannel.position)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_target_by_channel_id(channel_id: int) -> ForceJoinChannel | None:
    async with async_session() as session:
        stmt = select(ForceJoinChannel).where(ForceJoinChannel.channel_id == channel_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_active_target_by_channel_id(channel_id: int) -> ForceJoinChannel | None:
    async with async_session() as session:
        stmt = select(ForceJoinChannel).where(
            ForceJoinChannel.channel_id == channel_id,
            ForceJoinChannel.is_active.is_(True),
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def upsert_target(
    channel_id: int,
    channel_username: str | None = None,
    invite_link: str | None = None,
    display_name: str | None = None,
    chat_type: str = "channel",
    added_by: int | None = None,
    verify_status: str = "pending",
) -> ForceJoinChannel:
    async with async_session() as session:
        stmt = select(ForceJoinChannel).where(ForceJoinChannel.channel_id == channel_id)
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.is_active = True
            existing.channel_username = channel_username or existing.channel_username
            existing.invite_link = invite_link or existing.invite_link
            existing.display_name = display_name or existing.display_name
            existing.chat_type = chat_type
            existing.verify_status = verify_status
            existing.verified_at = datetime.now(timezone.utc) if verify_status == "ok" else existing.verified_at
            await session.commit()
            await session.refresh(existing)
            return existing

        max_pos_stmt = select(ForceJoinChannel.position).order_by(ForceJoinChannel.position.desc()).limit(1)
        max_pos_result = await session.execute(max_pos_stmt)
        max_pos = max_pos_result.scalar() or 0

        entry = ForceJoinChannel(
            channel_id=channel_id,
            channel_username=channel_username,
            invite_link=invite_link,
            display_name=display_name,
            chat_type=chat_type,
            added_by=added_by,
            verify_status=verify_status,
            verified_at=datetime.now(timezone.utc) if verify_status == "ok" else None,
            position=max_pos + 1,
            is_active=True,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry


async def deactivate(channel_id: int) -> None:
    async with async_session() as session:
        stmt = (
            update(ForceJoinChannel)
            .where(ForceJoinChannel.channel_id == channel_id)
            .values(is_active=False)
        )
        await session.execute(stmt)
        await session.commit()


async def update_verify_status(
    target_id: int,
    status: str,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        stmt = (
            update(ForceJoinChannel)
            .where(ForceJoinChannel.id == target_id)
            .values(
                verify_status=status,
                last_checked_at=now,
                verified_at=now if status == "ok" else ForceJoinChannel.verified_at,
                last_error=error,
            )
        )
        await session.execute(stmt)
        await session.commit()


async def update_username(target_id: int, username: str | None) -> None:
    async with async_session() as session:
        stmt = (
            update(ForceJoinChannel)
            .where(ForceJoinChannel.id == target_id)
            .values(channel_username=username)
        )
        await session.execute(stmt)
        await session.commit()
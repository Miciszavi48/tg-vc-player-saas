from __future__ import annotations

from sqlalchemy import delete, func, select, update

from app.database.engine import async_session
from app.database.models import Playlist


async def add_to_queue(
    chat_id: int,
    file_path: str | None = None,
    stream_url: str | None = None,
    title: str | None = None,
    duration: int = 0,
    media_type: str = "audio",
    added_by: int | None = None,
) -> Playlist:
    async with async_session() as session:
        async with session.begin():
            lock_stmt = (
                select(Playlist.position)
                .where(Playlist.chat_id == chat_id)
                .with_for_update()
            )
            await session.execute(lock_stmt)

            max_pos_stmt = select(func.coalesce(func.max(Playlist.position), -1)).where(
                Playlist.chat_id == chat_id
            )
            result = await session.execute(max_pos_stmt)
            max_pos = result.scalar() or 0

            item = Playlist(
                chat_id=chat_id,
                position=max_pos + 1,
                file_path=file_path,
                stream_url=stream_url,
                title=title,
                duration_seconds=duration,
                media_type=media_type,
                added_by=added_by,
            )
            session.add(item)
            await session.flush()

        await session.refresh(item)
        return item


async def get_queue(chat_id: int) -> list[Playlist]:
    async with async_session() as session:
        stmt = (
            select(Playlist)
            .where(Playlist.chat_id == chat_id)
            .order_by(Playlist.position.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def get_current(chat_id: int) -> Playlist | None:
    async with async_session() as session:
        stmt = (
            select(Playlist)
            .where(Playlist.chat_id == chat_id)
            .order_by(Playlist.position.asc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def remove_from_queue(chat_id: int, position: int) -> None:
    async with async_session() as session:
        stmt = delete(Playlist).where(
            Playlist.chat_id == chat_id, Playlist.position == position
        )
        await session.execute(stmt)
        await session.commit()


async def clear_queue(chat_id: int) -> None:
    async with async_session() as session:
        stmt = delete(Playlist).where(Playlist.chat_id == chat_id)
        await session.execute(stmt)
        await session.commit()


async def advance_queue(chat_id: int) -> Playlist | None:
    async with async_session() as session:
        async with session.begin():
            lock_stmt = (
                select(Playlist)
                .where(Playlist.chat_id == chat_id)
                .with_for_update()
            )
            await session.execute(lock_stmt)

            current_stmt = (
                select(Playlist)
                .where(Playlist.chat_id == chat_id)
                .order_by(Playlist.position.asc())
                .limit(1)
            )
            result = await session.execute(current_stmt)
            current = result.scalar_one_or_none()
            if current is not None:
                await session.delete(current)
                await session.flush()

            shift_stmt = (
                update(Playlist)
                .where(Playlist.chat_id == chat_id)
                .values(position=Playlist.position - 1)
            )
            await session.execute(shift_stmt)

        next_stmt = (
            select(Playlist)
            .where(Playlist.chat_id == chat_id)
            .order_by(Playlist.position.asc())
            .limit(1)
        )
        result = await session.execute(next_stmt)
        return result.scalar_one_or_none()


async def get_queue_length(chat_id: int) -> int:
    async with async_session() as session:
        stmt = select(func.count()).select_from(Playlist).where(
            Playlist.chat_id == chat_id
        )
        result = await session.execute(stmt)
        return result.scalar() or 0

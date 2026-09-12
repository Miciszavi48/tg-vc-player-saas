"""Persistence for named playlists (TRANSPORT-09/10/11).

Distinct from `playlist_repo`, which owns the transient per-chat play queue.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select

from app.database.engine import async_session
from app.database.models import NamedPlaylist, NamedPlaylistItem

MAX_NAME_LENGTH = 64
MAX_PLAYLISTS_PER_CHAT = 50
MAX_ITEMS_PER_PLAYLIST = 200


def normalize_name(name: str) -> str:
    """Collapse whitespace so 'my  list' and 'my list' are the same playlist."""
    return " ".join(str(name or "").split())


async def get_playlist(chat_id: int, name: str) -> NamedPlaylist | None:
    normalized = normalize_name(name)
    async with async_session() as session:
        result = await session.execute(
            select(NamedPlaylist).where(
                NamedPlaylist.chat_id == chat_id,
                func.lower(NamedPlaylist.name) == normalized.lower(),
            )
        )
        return result.scalar_one_or_none()


async def count_playlists(chat_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count(NamedPlaylist.id)).where(NamedPlaylist.chat_id == chat_id)
        )
        return int(result.scalar_one() or 0)


async def create_playlist(
    chat_id: int, name: str, *, created_by: int | None = None,
) -> NamedPlaylist:
    """Create a playlist. Caller must have validated name and quota first."""
    async with async_session() as session:
        async with session.begin():
            row = NamedPlaylist(
                chat_id=chat_id, name=normalize_name(name), created_by=created_by
            )
            session.add(row)
        await session.refresh(row)
        return row


async def rename_playlist(chat_id: int, old_name: str, new_name: str) -> bool:
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(NamedPlaylist).where(
                    NamedPlaylist.chat_id == chat_id,
                    func.lower(NamedPlaylist.name) == normalize_name(old_name).lower(),
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return False
            row.name = normalize_name(new_name)
        return True


async def delete_playlist(chat_id: int, name: str) -> bool:
    playlist = await get_playlist(chat_id, name)
    if playlist is None:
        return False
    async with async_session() as session:
        async with session.begin():
            # Items are removed explicitly so the delete works on backends where
            # ON DELETE CASCADE is not enforced (e.g. SQLite without pragma).
            await session.execute(
                delete(NamedPlaylistItem).where(
                    NamedPlaylistItem.playlist_id == playlist.id
                )
            )
            await session.execute(
                delete(NamedPlaylist).where(NamedPlaylist.id == playlist.id)
            )
    return True


async def list_playlists(
    chat_id: int, page: int = 0, per_page: int = 10,
) -> tuple[list[NamedPlaylist], int]:
    """Return one page of playlists plus the total count (TRANSPORT-10)."""
    page = max(0, int(page))
    per_page = max(1, int(per_page))
    async with async_session() as session:
        total = int(
            (
                await session.execute(
                    select(func.count(NamedPlaylist.id)).where(
                        NamedPlaylist.chat_id == chat_id
                    )
                )
            ).scalar_one()
            or 0
        )
        rows = (
            (
                await session.execute(
                    select(NamedPlaylist)
                    .where(NamedPlaylist.chat_id == chat_id)
                    .order_by(NamedPlaylist.name.asc())
                    .offset(page * per_page)
                    .limit(per_page)
                )
            )
            .scalars()
            .all()
        )
    return list(rows), total


async def count_items(playlist_id: int) -> int:
    async with async_session() as session:
        result = await session.execute(
            select(func.count(NamedPlaylistItem.id)).where(
                NamedPlaylistItem.playlist_id == playlist_id
            )
        )
        return int(result.scalar_one() or 0)


async def add_item(
    playlist_id: int,
    *,
    source: str,
    title: str | None = None,
    media_type: str = "audio",
    added_by: int | None = None,
) -> NamedPlaylistItem:
    async with async_session() as session:
        async with session.begin():
            next_position = int(
                (
                    await session.execute(
                        select(func.coalesce(func.max(NamedPlaylistItem.position), -1)).where(
                            NamedPlaylistItem.playlist_id == playlist_id
                        )
                    )
                ).scalar_one()
                or -1
            ) + 1
            row = NamedPlaylistItem(
                playlist_id=playlist_id,
                position=next_position,
                source=source,
                title=title,
                media_type=media_type,
                added_by=added_by,
            )
            session.add(row)
        await session.refresh(row)
        return row


async def get_items(playlist_id: int) -> list[NamedPlaylistItem]:
    async with async_session() as session:
        rows = (
            (
                await session.execute(
                    select(NamedPlaylistItem)
                    .where(NamedPlaylistItem.playlist_id == playlist_id)
                    .order_by(NamedPlaylistItem.position.asc())
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

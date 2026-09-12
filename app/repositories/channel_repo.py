from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database.engine import async_session
from app.database.models import Channel


async def upsert_channel(
    chat_id: int,
    chat_title: str | None = None,
    invite_link: str | None = None,
    installed_by: int | None = None,
) -> Channel:
    async with async_session() as session:
        stmt = select(Channel).where(Channel.chat_id == chat_id)
        result = await session.execute(stmt)
        channel = result.scalar_one_or_none()
        if channel is None:
            channel = Channel(
                chat_id=chat_id,
                chat_title=chat_title,
                invite_link=invite_link,
                installed_by=installed_by,
                status="active",
            )
            session.add(channel)
        else:
            if chat_title is not None:
                channel.chat_title = chat_title
            if invite_link is not None:
                channel.invite_link = invite_link
            channel.status = "active"
            channel.last_activity = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(channel)
        return channel


async def get_channel(chat_id: int) -> Channel | None:
    async with async_session() as session:
        stmt = select(Channel).where(Channel.chat_id == chat_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_all_active_channels() -> list[Channel]:
    async with async_session() as session:
        stmt = select(Channel).where(Channel.status == "active")
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def deactivate_channel(chat_id: int) -> None:
    async with async_session() as session:
        stmt = (
            update(Channel)
            .where(Channel.chat_id == chat_id)
            .values(status="inactive")
        )
        await session.execute(stmt)
        await session.commit()

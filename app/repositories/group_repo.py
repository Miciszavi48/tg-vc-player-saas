from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from app.database.engine import async_session
from app.database.models import Group


async def upsert_group(
    chat_id: int,
    chat_title: str | None = None,
    invite_link: str | None = None,
    installed_by: int | None = None,
) -> Group:
    async with async_session() as session:
        stmt = select(Group).where(Group.chat_id == chat_id)
        result = await session.execute(stmt)
        group = result.scalar_one_or_none()
        if group is None:
            group = Group(
                chat_id=chat_id,
                chat_title=chat_title,
                invite_link=invite_link,
                installed_by=installed_by,
                status="active",
            )
            session.add(group)
        else:
            if chat_title is not None:
                group.chat_title = chat_title
            if invite_link is not None:
                group.invite_link = invite_link
            group.status = "active"
            group.last_activity = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(group)
        return group


async def get_group(chat_id: int) -> Group | None:
    async with async_session() as session:
        stmt = select(Group).where(Group.chat_id == chat_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


async def get_all_active_groups() -> list[Group]:
    async with async_session() as session:
        stmt = select(Group).where(Group.status == "active")
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def deactivate_group(chat_id: int) -> None:
    async with async_session() as session:
        stmt = (
            update(Group)
            .where(Group.chat_id == chat_id)
            .values(status="inactive")
        )
        await session.execute(stmt)
        await session.commit()


async def update_member_count(chat_id: int, count: int) -> None:
    async with async_session() as session:
        stmt = (
            update(Group)
            .where(Group.chat_id == chat_id)
            .values(member_count=count)
        )
        await session.execute(stmt)
        await session.commit()

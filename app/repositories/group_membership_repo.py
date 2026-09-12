"""Repository for observed per-chat group membership timestamps."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import GroupMemberMembership


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source(value: str | None) -> str:
    raw = (value or "manual_unknown").strip()
    return raw[:64] or "manual_unknown"


async def get_membership(
    chat_id: int,
    user_id: int,
) -> GroupMemberMembership | None:
    """Return observed membership row, if any."""
    async with async_session() as session:
        result = await session.execute(
            select(GroupMemberMembership).where(
                GroupMemberMembership.chat_id == chat_id,
                GroupMemberMembership.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()


async def record_member_join(
    chat_id: int,
    user_id: int,
    joined_at: datetime | None = None,
    source: str = "chat_member_update",
) -> GroupMemberMembership:
    """Record an observed group join/rejoin event."""
    event_at = _as_utc(joined_at)
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(GroupMemberMembership).where(
                    GroupMemberMembership.chat_id == chat_id,
                    GroupMemberMembership.user_id == user_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = GroupMemberMembership(
                    chat_id=chat_id,
                    user_id=user_id,
                    joined_at=event_at,
                    first_seen_at=event_at,
                    last_seen_at=event_at,
                    left_at=None,
                    source=_source(source),
                )
                session.add(row)
                return row

            if row.left_at is not None:
                row.joined_at = event_at
                row.left_at = None
            if row.first_seen_at is None or event_at < _as_utc(row.first_seen_at):
                row.first_seen_at = event_at
            row.last_seen_at = event_at
            row.source = _source(source)
            return row


async def record_member_left(
    chat_id: int,
    user_id: int,
    left_at: datetime | None = None,
    source: str = "chat_member_update",
) -> GroupMemberMembership:
    """Record an observed group leave/kick event."""
    event_at = _as_utc(left_at)
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(GroupMemberMembership).where(
                    GroupMemberMembership.chat_id == chat_id,
                    GroupMemberMembership.user_id == user_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = GroupMemberMembership(
                    chat_id=chat_id,
                    user_id=user_id,
                    joined_at=event_at,
                    first_seen_at=event_at,
                    last_seen_at=event_at,
                    left_at=event_at,
                    source=_source(source),
                )
                session.add(row)
                return row

            row.left_at = event_at
            row.last_seen_at = event_at
            row.source = _source(source)
            return row


async def record_member_seen(
    chat_id: int,
    user_id: int,
    seen_at: datetime | None = None,
    source: str = "first_seen",
) -> GroupMemberMembership:
    """Record a member seen without reliable historical join date.

    Unknown existing members are treated as joined at first observation so they
    do not incorrectly pass age thresholds immediately after tracking starts.
    """
    event_at = _as_utc(seen_at)
    async with async_session() as session:
        async with session.begin():
            result = await session.execute(
                select(GroupMemberMembership).where(
                    GroupMemberMembership.chat_id == chat_id,
                    GroupMemberMembership.user_id == user_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = GroupMemberMembership(
                    chat_id=chat_id,
                    user_id=user_id,
                    joined_at=event_at,
                    first_seen_at=event_at,
                    last_seen_at=event_at,
                    left_at=None,
                    source=_source(source),
                )
                session.add(row)
                return row

            if row.left_at is not None:
                row.joined_at = event_at
                row.left_at = None
            if row.first_seen_at is None:
                row.first_seen_at = event_at
            row.last_seen_at = event_at
            row.source = _source(source)
            return row

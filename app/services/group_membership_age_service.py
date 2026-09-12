"""Group membership age tracking for Call Security."""

from __future__ import annotations

from datetime import datetime, timezone

from app.repositories import group_membership_repo


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def record_member_join(
    chat_id: int,
    user_id: int,
    joined_at: datetime | None = None,
    source: str = "chat_member_update",
) -> None:
    """Record a reliable group join/rejoin event."""
    await group_membership_repo.record_member_join(chat_id, user_id, joined_at, source)


async def record_member_left(
    chat_id: int,
    user_id: int,
    left_at: datetime | None = None,
    source: str = "chat_member_update",
) -> None:
    """Record a reliable group leave/kick event."""
    await group_membership_repo.record_member_left(chat_id, user_id, left_at, source)


async def record_member_seen(
    chat_id: int,
    user_id: int,
    seen_at: datetime | None = None,
    source: str = "first_seen",
) -> None:
    """Record a member seen without reliable historical join date."""
    await group_membership_repo.record_member_seen(chat_id, user_id, seen_at, source)


async def get_membership_age_days(
    chat_id: int,
    user_id: int,
    now: datetime | None = None,
) -> int | None:
    """Return observed active group membership age in whole days.

    Returns ``None`` when the bot has no active observed membership row. Unknown
    historical age is never inferred from Telegram account id.
    """
    row = await group_membership_repo.get_membership(chat_id, user_id)
    if row is None or row.left_at is not None:
        return None
    joined_at = getattr(row, "joined_at", None)
    if joined_at is None:
        return None
    delta = _as_utc(now) - _as_utc(joined_at)
    return max(0, int(delta.total_seconds() // 86_400))


async def is_membership_old_enough(
    chat_id: int,
    user_id: int,
    threshold_days: int,
    now: datetime | None = None,
) -> bool:
    """Return True when observed membership age meets the threshold."""
    age_days = await get_membership_age_days(chat_id, user_id, now=now)
    if age_days is None:
        return False
    return age_days >= int(threshold_days)

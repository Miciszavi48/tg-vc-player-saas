"""Repository for per-chat Call Security settings."""

from __future__ import annotations

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import CallSecuritySettings

MIN_MEMBERSHIP_AGE_DAYS = 0
MAX_MEMBERSHIP_AGE_DAYS = 3650

TOGGLE_FIELDS = frozenset(
    {
        "enabled",
        "owner_access_enabled",
        "mute_incoming_enabled",
        "summary_enabled",
        "report_enabled",
    }
)


def clamp_membership_age_days(days: int) -> int:
    """Clamp membership age days to allowed range."""
    return max(MIN_MEMBERSHIP_AGE_DAYS, min(MAX_MEMBERSHIP_AGE_DAYS, int(days)))


def clamp_account_age_days(days: int) -> int:
    """Deprecated compatibility wrapper for the old 0024 field name."""
    return clamp_membership_age_days(days)


async def get_call_security_settings(chat_id: int) -> CallSecuritySettings | None:
    """Return Call Security settings for a chat, or None."""
    async with async_session() as session:
        result = await session.execute(
            select(CallSecuritySettings).where(CallSecuritySettings.chat_id == chat_id)
        )
        return result.scalar_one_or_none()


async def get_or_create_call_security_settings(chat_id: int) -> CallSecuritySettings:
    """Return existing settings or create defaults for the chat."""
    async with async_session() as session:
        result = await session.execute(
            select(CallSecuritySettings).where(CallSecuritySettings.chat_id == chat_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            return row
        row = CallSecuritySettings(chat_id=chat_id)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def update_call_security_setting(
    chat_id: int,
    key: str,
    value: object,
    *,
    updated_by: int | None = None,
) -> CallSecuritySettings:
    """Update a single Call Security field."""
    if key not in TOGGLE_FIELDS and key not in {"membership_age_days", "account_age_days"}:
        raise ValueError(f"unsupported call security field: {key}")

    async with async_session() as session:
        result = await session.execute(
            select(CallSecuritySettings).where(CallSecuritySettings.chat_id == chat_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = CallSecuritySettings(chat_id=chat_id)
            session.add(row)
        if key in {"membership_age_days", "account_age_days"}:
            target_key = "membership_age_days"
            setattr(row, target_key, clamp_membership_age_days(int(value)))
        else:
            setattr(row, key, bool(value))
        if updated_by is not None:
            row.updated_by = updated_by
        await session.commit()
        await session.refresh(row)
        return row


async def set_membership_age_days(
    chat_id: int,
    days: int,
    *,
    updated_by: int | None = None,
) -> CallSecuritySettings:
    """Persist group-membership-age threshold for auto-unmute decisions."""
    return await update_call_security_setting(
        chat_id,
        "membership_age_days",
        clamp_membership_age_days(days),
        updated_by=updated_by,
    )


async def set_account_age_days(
    chat_id: int,
    days: int,
    *,
    updated_by: int | None = None,
) -> CallSecuritySettings:
    """Deprecated compatibility wrapper; stores membership_age_days."""
    return await set_membership_age_days(chat_id, days, updated_by=updated_by)

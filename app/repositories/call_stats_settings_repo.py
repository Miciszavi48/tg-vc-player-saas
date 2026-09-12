"""Per-chat BotSetting storage for the call-stats feature toggle."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.database.engine import async_session
from app.database.models import BotSetting
from app.repositories import id_command_settings_repo

KEY_PREFIX = "call_stats"
SETTING_ENABLED = "enabled"
SETTING_RESET_CADENCE = "reset_cadence"
SETTING_LAST_RESET_BUCKET = "last_reset_bucket"
SETTING_RESET_WATERMARK = "reset_watermark"

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled", "active"}
VALID_RESET_CADENCES = frozenset({"daily", "monthly"})


def setting_key(chat_id: int, name: str) -> str:
    """Build a namespaced BotSetting key for one chat."""
    return f"{KEY_PREFIX}:{chat_id}:{name}"


async def _set_value(
    chat_id: int,
    name: str,
    value: str | None,
    *,
    updated_by: int | None = None,
) -> None:
    key = setting_key(chat_id, name)
    async with async_session() as session:
        result = await session.execute(select(BotSetting).where(BotSetting.key == key))
        row = result.scalar_one_or_none()
        if row is None:
            row = BotSetting(key=key, value=value, updated_by=updated_by)
            session.add(row)
        else:
            row.value = value
            if updated_by is not None:
                row.updated_by = updated_by
        await session.commit()


async def _get_value(chat_id: int, name: str) -> str | None:
    key = setting_key(chat_id, name)
    async with async_session() as session:
        result = await session.execute(select(BotSetting.value).where(BotSetting.key == key))
        return result.scalar_one_or_none()


async def get_enabled(chat_id: int, *, default: bool = True) -> bool:
    """Return whether the call-stats feature is enabled for a chat."""
    value = await _get_value(chat_id, SETTING_ENABLED)
    if value is None:
        return default
    return str(value).strip().lower() in _TRUE_VALUES


async def set_enabled(
    chat_id: int,
    enabled: bool,
    *,
    updated_by: int | None = None,
) -> None:
    """Enable or disable the call-stats feature for a chat."""
    await _set_value(
        chat_id,
        SETTING_ENABLED,
        "1" if enabled else "0",
        updated_by=updated_by,
    )


async def get_reset_cadence(chat_id: int) -> str | None:
    """Return the configured call-stats reset cadence, or None when unset."""
    value = await _get_value(chat_id, SETTING_RESET_CADENCE)
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized if normalized in VALID_RESET_CADENCES else None


async def set_reset_cadence(
    chat_id: int,
    cadence: str,
    *,
    updated_by: int | None = None,
) -> None:
    """Set the daily/monthly cadence used to roll over call-stats aggregates."""
    if cadence not in VALID_RESET_CADENCES:
        raise ValueError(f"invalid call-stats reset cadence: {cadence}")
    await _set_value(chat_id, SETTING_RESET_CADENCE, cadence, updated_by=updated_by)


async def get_reset_watermark(chat_id: int) -> datetime | None:
    """Return the instant before which call-stats aggregates are hidden."""
    value = await _get_value(chat_id, SETTING_RESET_WATERMARK)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


async def set_reset_watermark(chat_id: int, moment: datetime) -> None:
    """Hide call-stats aggregates older than ``moment`` without deleting rows."""
    normalized = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    await _set_value(
        chat_id, SETTING_RESET_WATERMARK, normalized.astimezone(timezone.utc).isoformat()
    )


async def get_last_reset_bucket(chat_id: int) -> str | None:
    """Return the last cadence bucket already rolled over for this chat."""
    value = await _get_value(chat_id, SETTING_LAST_RESET_BUCKET)
    return str(value) if value is not None else None


async def set_last_reset_bucket(chat_id: int, bucket: str) -> None:
    """Record the cadence bucket just rolled over so resets stay idempotent."""
    await _set_value(chat_id, SETTING_LAST_RESET_BUCKET, bucket)


async def chat_ids_with_reset_cadence() -> dict[int, str]:
    """Return every chat that has an explicit call-stats reset cadence."""
    prefix = f"{KEY_PREFIX}:"
    suffix = f":{SETTING_RESET_CADENCE}"
    async with async_session() as session:
        result = await session.execute(
            select(BotSetting.key, BotSetting.value).where(
                BotSetting.key.like(f"{prefix}%{suffix}")
            )
        )
        rows = result.all()
    cadences: dict[int, str] = {}
    for key, value in rows:
        raw_id = str(key)[len(prefix) : -len(suffix)]
        normalized = str(value or "").strip().lower()
        if normalized not in VALID_RESET_CADENCES:
            continue
        try:
            cadences[int(raw_id)] = normalized
        except ValueError:
            continue
    return cadences


async def is_effective_id_call_stats(chat_id: int) -> bool:
    """Return whether Id output should include call stats (both toggles on)."""
    if not await get_enabled(chat_id):
        return False
    return await id_command_settings_repo.get_show_call_stats(chat_id)
